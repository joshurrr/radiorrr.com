STUCK_FRAME_TOOL_DIR = Path("/tmp/radiorouter-stuck-frame-tool")
STUCK_FRAME_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
STUCK_FRAME_MAX_DURATION_SECONDS = 10 * 60
STUCK_FRAME_SAMPLE_RATE = 8000
STUCK_FRAME_MAX_EFFECTS = 240
stuck_frame_tool_semaphore = asyncio.Semaphore(1)


def _stuck_frame_probe_video(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=avg_frame_rate:format=duration",
            "-of", "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )

    if result.returncode != 0:
        detail = str(result.stderr or "").strip()
        raise RuntimeError(detail or "FFprobe could not inspect this video")

    try:
        payload = json.loads(result.stdout or "{}")
        duration = float((payload.get("format") or {}).get("duration") or 0)
        streams = payload.get("streams") or []
        rate_text = str((streams[0] if streams else {}).get("avg_frame_rate") or "0/1")
        numerator, denominator = rate_text.split("/", 1)
        fps = float(numerator) / max(float(denominator), 1.0)
    except Exception as error:
        raise RuntimeError("Could not read the video's duration or frame rate") from error

    if duration <= 0 or fps <= 0:
        raise RuntimeError("Could not read the video's duration or frame rate")

    if duration > STUCK_FRAME_MAX_DURATION_SECONDS:
        raise RuntimeError("Videos longer than 10 minutes are not supported by this tool")

    return duration, fps


def _stuck_frame_extract_audio(input_path, wav_path, duration):
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-t", f"{duration:.3f}",
            "-vn",
            "-ac", "1",
            "-ar", str(STUCK_FRAME_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            str(wav_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=max(60, int(duration) + 30),
        check=False,
    )

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 600:
            detail = detail[-600:]
        raise RuntimeError(detail or "FFmpeg could not read the video's audio")

    if not wav_path.exists() or wav_path.stat().st_size < 20000:
        raise RuntimeError("The video does not contain enough usable audio to detect a beat")


def _stuck_frame_detect_beat_grid(wav_path, duration):
    bpm, confidence = _estimate_bpm_from_wav(wav_path)

    with wave.open(str(wav_path), "rb") as wav:
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()

    frame_size = 512
    energies = []
    for start in range(0, len(samples) - frame_size, frame_size):
        chunk = samples[start:start + frame_size]
        total = 0.0
        for sample in chunk:
            value = float(sample) / 32768.0
            total += value * value
        energies.append(math.sqrt(total / frame_size))

    if len(energies) < 80:
        raise RuntimeError("The video does not contain enough rhythmic audio")

    log_energy = [math.log(max(value, 1e-8)) for value in energies]
    onset = [0.0]
    for index in range(1, len(log_energy)):
        onset.append(max(0.0, log_energy[index] - log_energy[index - 1]))

    smoothed = []
    for index in range(len(onset)):
        left = max(0, index - 1)
        right = min(len(onset), index + 2)
        smoothed.append(sum(onset[left:right]) / (right - left))

    seconds_per_frame = frame_size / float(rate)
    beat_period = 60.0 / float(bpm)
    period_frames = max(1, int(round(beat_period / seconds_per_frame)))

    strongest = sorted(
        range(len(smoothed)),
        key=lambda index: smoothed[index],
        reverse=True,
    )[: min(80, len(smoothed))]

    candidate_phases = {index % period_frames for index in strongest}
    if not candidate_phases:
        candidate_phases = {0}

    best_phase = 0
    best_score = -1.0

    for phase in candidate_phases:
        score = 0.0
        count = 0
        index = phase
        while index < len(smoothed):
            left = max(0, index - 1)
            right = min(len(smoothed), index + 2)
            score += max(smoothed[left:right] or [0.0])
            count += 1
            index += period_frames
        if count:
            score /= count
        if score > best_score:
            best_score = score
            best_phase = phase

    first_beat = best_phase * seconds_per_frame
    while first_beat > beat_period:
        first_beat -= beat_period

    beats = []
    beat_time = first_beat
    while beat_time < duration:
        if beat_time >= 0.15:
            beats.append(beat_time)
        beat_time += beat_period

    if len(beats) < 4:
        raise RuntimeError("A stable beat grid could not be detected in this video")

    beats = beats[::2]

    if len(beats) > STUCK_FRAME_MAX_EFFECTS:
        stride = int(math.ceil(len(beats) / STUCK_FRAME_MAX_EFFECTS))
        beats = beats[::stride]

    return bpm, confidence, beats


def _stuck_frame_render_video(input_path, output_path, fps, beats):
    freeze_frames = max(2, min(6, int(round(fps * 0.08))))
    filters = []

    for beat_time in beats:
        frame = max(1, int(round(beat_time * fps)))
        first = frame
        last = frame + freeze_frames - 1
        replace = frame - 1
        filters.append(
            f"freezeframes=first={first}:last={last}:replace={replace}"
        )

    if not filters:
        raise RuntimeError("No beat positions were available for the effect")

    filter_chain = ",".join(filters)

    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", str(input_path),
            "-vf", filter_chain,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            str(output_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=max(120, int(STUCK_FRAME_MAX_DURATION_SECONDS * 2)),
        check=False,
    )

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 800:
            detail = detail[-800:]
        raise RuntimeError(detail or "FFmpeg could not render the stuck-frame effect")

    if not output_path.exists() or output_path.stat().st_size < 50000:
        raise RuntimeError("The processed video was not created correctly")


def _stuck_frame_process_video(input_path, wav_path, output_path):
    duration, fps = _stuck_frame_probe_video(input_path)
    _stuck_frame_extract_audio(input_path, wav_path, duration)
    bpm, confidence, beats = _stuck_frame_detect_beat_grid(wav_path, duration)
    _stuck_frame_render_video(input_path, output_path, fps, beats)
    return bpm, confidence, len(beats)




@app.post("/api/tools/stuck-frame-effect")
async def public_stuck_frame_effect(request: Request):
    raw_name = str(request.headers.get("X-RRR-Filename") or "video").strip()
    try:
        from urllib.parse import unquote
        raw_name = unquote(raw_name)
    except Exception:
        pass

    safe_name = Path(raw_name).name
    extension = Path(safe_name).suffix.lower()
    if extension not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
        raise HTTPException(
            status_code=400,
            detail="Upload an MP4, MOV, M4V, WebM or MKV video",
        )

    STUCK_FRAME_TOOL_DIR.mkdir(parents=True, exist_ok=True)
    job_dir = STUCK_FRAME_TOOL_DIR / uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=False)

    input_path = job_dir / ("input" + extension)
    wav_path = job_dir / "audio.wav"
    output_path = job_dir / "stuck-frame.mp4"

    received = 0

    try:
        with input_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                received += len(chunk)
                if received > STUCK_FRAME_MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="The video is larger than the 100 MB upload limit",
                    )
                handle.write(chunk)

        if received < 1024:
            raise HTTPException(status_code=400, detail="The uploaded video was empty")

        async with stuck_frame_tool_semaphore:
            try:
                bpm, confidence, effect_count = await asyncio.to_thread(
                    _stuck_frame_process_video,
                    input_path,
                    wav_path,
                    output_path,
                )
            except subprocess.TimeoutExpired:
                raise HTTPException(
                    status_code=504,
                    detail="Video processing timed out",
                )
            except RuntimeError as error:
                raise HTTPException(status_code=422, detail=str(error))

        download_base = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-._")
        if not download_base:
            download_base = "video"

        return FileResponse(
            output_path,
            media_type="video/mp4",
            filename=f"{download_base}-stuck-frame.mp4",
            headers={
                "Cache-Control": "no-store",
                "X-RRR-Detected-BPM": str(bpm),
                "X-RRR-Beat-Confidence": str(confidence),
                "X-RRR-Stuck-Frames": str(effect_count),
            },
            background=BackgroundTask(shutil.rmtree, job_dir, ignore_errors=True),
        )
    except HTTPException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise


