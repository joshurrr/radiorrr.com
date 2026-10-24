import os
import subprocess
import time
import tempfile
import json
import math
import sys
from array import array
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.parse import quote

import essentia.standard as es
import webrtcvad
from sound_intelligence import load_model, analyse_embeddings

RADIO_ROUTER_URL = os.getenv("RADIO_ROUTER_URL", "http://host.docker.internal:8000").rstrip("/")
SAMPLE_SECONDS = int(os.getenv("SAMPLE_SECONDS", "30"))
TOP_N = int(os.getenv("TOP_N", "10"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.05"))
DJ_POLL_SECONDS = int(os.getenv("DJ_POLL_SECONDS", "5"))

EFFNET_MODEL = "/app/models/discogs-effnet-bs64-1.pb"
GENRE_MODEL = "/app/models/genre_discogs400-discogs-effnet-1.pb"
GENRE_JSON = "/app/models/genre_discogs400-discogs-effnet-1.json"

print("Loading Essentia models...", flush=True)
embedding_model = es.TensorflowPredictEffnetDiscogs(graphFilename=EFFNET_MODEL, output="PartitionedCall:1")
genre_model = es.TensorflowPredict2D(graphFilename=GENRE_MODEL, input="serving_default_model_Placeholder", output="PartitionedCall:0")
with open(GENRE_JSON, "r", encoding="utf-8") as f:
    genre_metadata = json.load(f)
classes = genre_metadata["classes"]
print(f"Loaded {len(classes)} Discogs genre classes.", flush=True)
sound_model = load_model(es)

def get_current_relay():
    url = f"{RADIO_ROUTER_URL}/api/live"
    req = Request(url, headers={"Accept": "application/json"})
    with urlopen(req, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    relay = data.get("relay")
    if not relay:
        return None
    username = relay.get("username")
    name = relay.get("name") or username
    platform = str(relay.get("platform") or "TikTok").strip()
    if not username:
        return None
    return {"username": username, "name": name, "platform": platform}


def analyse_audio(audio_file, include_sound=False):
    audio = es.MonoLoader(filename=audio_file, sampleRate=16000)()
    embeddings = embedding_model(audio)
    predictions = genre_model(embeddings)
    scores = predictions.mean(axis=0)
    results = sorted(zip(classes, scores), key=lambda x: float(x[1]), reverse=True)
    genres = [{"genre": genre, "confidence": float(score)} for genre, score in results[:TOP_N] if float(score) >= MIN_CONFIDENCE]
    if not include_sound:
        return genres
    try:
        sound = analyse_embeddings(sound_model, embeddings)
    except Exception as error:
        print(f"[Sound AI] Optional inference failed: {error}", flush=True)
        sound = None
    return genres, sound


def analyse_bpm(audio_file):
    """Detect tempo from the same captured sample used for genre analysis."""
    audio = es.MonoLoader(filename=audio_file, sampleRate=44100, resampleQuality=4)()
    rhythm = es.RhythmExtractor2013(method="multifeature")
    bpm, beats, raw_confidence, _estimates, _intervals = rhythm(audio)

    bpm = float(bpm or 0.0)
    raw_confidence = max(0.0, float(raw_confidence or 0.0))

    if bpm <= 0 or len(beats) == 0:
        return None

    # RhythmExtractor2013 confidence is not a simple percentage. Convert it
    # to a bounded 0..1 weighting while preserving its relative strength.
    confidence = min(1.0, raw_confidence / 5.32)

    return {
        "bpm": round(bpm, 2),
        "bpm_confidence": round(confidence, 4),
    }


def analyse_speech(audio_file):
    """Estimate persistent DJ mic/talk activity from the captured WAV.

    WebRTC VAD remains useful as a supporting signal, but music can both trigger
    false positives and mask speech. Add an independent ducking detector that
    looks for repeated 0.4-6 second loudness drops followed by a recovery. Only
    repeated ducking contributes materially to the talk score, which avoids
    treating normal breakdowns or one brief announcement as persistent chatter.
    """
    convert = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-i", audio_file,
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-f", "s16le",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )

    if convert.returncode != 0 or not convert.stdout:
        return None

    pcm = convert.stdout
    sample_rate = 16000
    frame_ms = 20
    frame_bytes = int(sample_rate * frame_ms / 1000) * 2

    vad = webrtcvad.Vad(3)

    flags = []
    for offset in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = pcm[offset:offset + frame_bytes]
        try:
            flags.append(bool(vad.is_speech(frame, sample_rate)))
        except Exception:
            flags.append(False)

    if not flags:
        return None

    # Require at least 5 consecutive positive 20 ms frames (100 ms) before
    # counting a run as speech. This removes many isolated musical transients.
    qualified = [False] * len(flags)
    run_start = None

    for index, is_speech in enumerate(flags + [False]):
        if is_speech and run_start is None:
            run_start = index
        elif not is_speech and run_start is not None:
            run_length = index - run_start
            if run_length >= 5:
                for pos in range(run_start, index):
                    qualified[pos] = True
            run_start = None

    speech_frames = sum(1 for value in qualified if value)
    speech_ratio = speech_frames / len(flags)

    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()

    loudness_frame_ms = 100
    loudness_samples = int(sample_rate * loudness_frame_ms / 1000)
    loudness_db = []

    for start in range(0, len(samples) - loudness_samples + 1, loudness_samples):
        chunk = samples[start:start + loudness_samples]
        if not chunk:
            continue
        mean_square = sum(float(value) * float(value) for value in chunk) / len(chunk)
        rms = math.sqrt(mean_square) / 32768.0
        loudness_db.append(20.0 * math.log10(max(rms, 1e-6)))

    def median(values):
        ordered = sorted(values)
        if not ordered:
            return -120.0
        middle = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[middle]
        return (ordered[middle - 1] + ordered[middle]) / 2.0

    duck_events = []
    index = 5

    while index + 3 < len(loudness_db):
        pre_level = median(loudness_db[index - 5:index])
        low_level = median(loudness_db[index:index + 3])
        drop_db = pre_level - low_level

        if drop_db < 4.0 or low_level < -55.0:
            index += 1
            continue

        recovery_index = None
        max_recovery = min(len(loudness_db) - 2, index + 60)

        for candidate in range(index + 4, max_recovery + 1):
            recovered_level = median(loudness_db[candidate:candidate + 3])
            if recovered_level >= pre_level - 1.5:
                recovery_index = candidate
                break

        if recovery_index is None:
            index += 1
            continue

        duration_frames = recovery_index - index
        duration_seconds = duration_frames * (loudness_frame_ms / 1000.0)

        if 0.4 <= duration_seconds <= 6.0:
            valley_level = median(loudness_db[index:recovery_index])
            depth_db = max(0.0, pre_level - valley_level)
            if depth_db >= 4.0:
                duck_events.append((index, recovery_index, depth_db))
                index = recovery_index + 3
                continue

        index += 1

    ducked_loudness_frames = sum(end - start for start, end, _ in duck_events)
    ducking_ratio = (
        ducked_loudness_frames / len(loudness_db)
        if loudness_db
        else 0.0
    )

    ducked_vad_frames = 0
    ducked_vad_speech_frames = 0
    vad_frames_per_loudness = max(1, loudness_frame_ms // frame_ms)

    for start, end, _depth in duck_events:
        vad_start = start * vad_frames_per_loudness
        vad_end = min(len(qualified), end * vad_frames_per_loudness)
        if vad_end <= vad_start:
            continue
        ducked_vad_frames += vad_end - vad_start
        ducked_vad_speech_frames += sum(
            1 for value in qualified[vad_start:vad_end] if value
        )

    speech_duck_overlap = (
        ducked_vad_speech_frames / ducked_vad_frames
        if ducked_vad_frames
        else 0.0
    )

    average_depth = (
        sum(depth for _start, _end, depth in duck_events) / len(duck_events)
        if duck_events
        else 0.0
    )

    event_factor = min(1.0, len(duck_events) / 3.0)
    depth_factor = min(1.0, average_depth / 8.0)
    ducking_confidence = (
        0.55 * event_factor
        + 0.25 * depth_factor
        + 0.20 * speech_duck_overlap
    )

    # Require repeated ducking before it can affect ranking. One isolated drop is
    # diagnostic only unless VAD strongly agrees with it. This makes ordinary
    # musical breakdowns far less likely to be mistaken for DJ chatter.
    if len(duck_events) >= 2:
        talk_ratio = ducking_ratio * (0.70 + 0.30 * speech_duck_overlap)
        talk_confidence = ducking_confidence
    elif (
        len(duck_events) == 1
        and speech_duck_overlap >= 0.60
        and speech_ratio >= 0.15
    ):
        talk_ratio = ducking_ratio * 0.50
        talk_confidence = ducking_confidence * 0.60
    else:
        talk_ratio = 0.0
        talk_confidence = 0.0

    return {
        "analysis": "speech",
        "speech_ratio": round(speech_ratio, 4),
        "speech_confidence": round(min(1.0, speech_ratio * 2.0), 4),
        "ducking_ratio": round(min(1.0, ducking_ratio), 4),
        "ducking_events": len(duck_events),
        "ducking_confidence": round(min(1.0, ducking_confidence), 4),
        "talk_ratio": round(min(1.0, talk_ratio), 4),
        "talk_confidence": round(min(1.0, talk_confidence), 4),
    }

def analyse_current_dj(dj):
    wav_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_file = tmp.name

        stream_url = (
            f"{RADIO_ROUTER_URL}/api/live-stream"
            f"?dj={quote(dj['username'], safe='')}"
            f"&platform={quote(dj.get('platform') or 'TikTok', safe='')}"
        )

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-i", stream_url,
                "-t", str(SAMPLE_SECONDS),
                "-vn",
                "-ac", "1",
                "-ar", "44100",
                "-f", "wav",
                wav_file,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=SAMPLE_SECONDS + 30,
        )

        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg failed: {error}")

        if os.path.getsize(wav_file) < 10000:
            raise RuntimeError("FFmpeg produced an empty/invalid WAV file")

        genres, sound_result = analyse_audio(wav_file, include_sound=True)
        return genres, analyse_bpm(wav_file), analyse_speech(wav_file), sound_result

    finally:
        if wav_file:
            try:
                os.unlink(wav_file)
            except Exception:
                pass
def print_results(dj, results, bpm_result=None, speech_result=None):
    print("", flush=True)
    print("=" * 60, flush=True)
    print(
        f"Current DJ: {dj.get('platform') or 'TikTok'} "
        f"@{dj['username']} ({dj['name']})",
        flush=True,
    )
    print("AI detected genre mix:", flush=True)
    print("-" * 60, flush=True)
    for index, result in enumerate(results, start=1):
        print(f"{index:2}. {result['genre']:<30} {result['confidence'] * 100:6.2f}%", flush=True)
    if bpm_result:
        print(
            f"BPM: {bpm_result['bpm']:.1f} "
            f"(confidence {bpm_result['bpm_confidence']:.0%})",
            flush=True,
        )
    if speech_result:
        print(
            f"Speech VAD: {speech_result['speech_ratio']:.0%} | "
            f"Ducking: {speech_result['ducking_ratio']:.0%} "
            f"({speech_result['ducking_events']} events) | "
            f"Talk score: {speech_result['talk_ratio']:.0%}",
            flush=True,
        )
    print("=" * 60, flush=True)


def publish_current_results(dj, results, bpm_result=None, speech_result=None, sound_result=None):
    """Publish current-relay analysis so Stage 3 sees fresh BPM and speech."""
    published_results = list(results)

    if bpm_result:
        published_results.append({
            "analysis": "bpm",
            "bpm": bpm_result["bpm"],
            "bpm_confidence": bpm_result["bpm_confidence"],
        })

    if speech_result:
        published_results.append(speech_result)

    payload = json.dumps({
        "platform": dj.get("platform") or "TikTok",
        "username": dj["username"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "genres": published_results,
    }).encode("utf-8")

    req = Request(
        f"{RADIO_ROUTER_URL}/api/ai-genre",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urlopen(req, timeout=10) as response:
        response.read()
    publish_sound_result(dj, sound_result)


def publish_sound_result(dj, result):
    """Best-effort shadow-only report; failure must not interfere with genre detection."""
    if not result:
        return
    token = os.getenv('ENGINE_HEARTBEAT_TOKEN', '')
    if not token:
        print('[Sound AI] No engine token; cannot publish', flush=True)
        return
    payload = dict(result)
    payload.update({
        'platform': dj.get('platform') or 'TikTok',
        'username': dj['username'],
        'detected_at': datetime.now(timezone.utc).isoformat(),
    })
    try:
        req = Request(
            f'{RADIO_ROUTER_URL}/api/sound-intelligence',
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token,
            },
            method='POST',
        )
        with urlopen(req, timeout=8) as response:
            response.read()
    except Exception as error:
        print(f'[Sound AI] Publish failed (genre pipeline unaffected): {error}', flush=True)


def start_engine_heartbeat():
    # Runs after model loading. Reports process liveness, not analysis success.
    import threading
    token = os.getenv("ENGINE_HEARTBEAT_TOKEN", "")
    if not token:
        print("Engine status reporting disabled: ENGINE_HEARTBEAT_TOKEN is unset", flush=True)
        return

    def report():
        while True:
            try:
                req = Request(
                    f"{RADIO_ROUTER_URL}/api/engine-heartbeat/genre-detector",
                    data=b"{}",
                    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=5) as response:
                    response.read(1024)
            except Exception:
                # Reporting must never interrupt audio analysis or leak credentials.
                pass
            time.sleep(20)

    threading.Thread(target=report, name="engine-heartbeat", daemon=True).start()



def _json_request(url, method="GET", payload=None, timeout=10):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def process_pending_dj_test():
    """Process at most one admin DJ candidate test using the loaded models."""
    try:
        queue = _json_request(
            f"{RADIO_ROUTER_URL}/api/admin/dj-test-jobs/next",
            timeout=5,
        )
    except Exception:
        return False

    job = queue.get("job")
    if not job:
        return False

    job_id = str(job.get("id") or "")
    username = str(job.get("username") or "")
    audio_url = str(job.get("audio_url") or "")

    if not job_id or not audio_url:
        return False

    wav_file = None
    try:
        print(f"[DJ Test] Analysing candidate @{username}", flush=True)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_file = tmp.name

        audio_req = Request(
            f"{RADIO_ROUTER_URL}{audio_url}",
            headers={"Accept": "audio/wav"},
        )
        with urlopen(audio_req, timeout=20) as response:
            with open(wav_file, "wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)

        if os.path.getsize(wav_file) < 10000:
            raise RuntimeError("Candidate WAV is empty or invalid")

        results = analyse_audio(wav_file)

        _json_request(
            f"{RADIO_ROUTER_URL}/api/admin/dj-test-jobs/{quote(job_id, safe='')}/result",
            method="POST",
            payload={"genres": results},
            timeout=10,
        )

        print(
            f"[DJ Test] @{username} complete "
            f"({len(results)} genre result(s))",
            flush=True,
        )

    except Exception as error:
        try:
            _json_request(
                f"{RADIO_ROUTER_URL}/api/admin/dj-test-jobs/{quote(job_id, safe='')}/result",
                method="POST",
                payload={
                    "genres": [],
                    "error": f"{type(error).__name__}: {error}",
                },
                timeout=10,
            )
        except Exception:
            pass

        print(
            f"[DJ Test] @{username} failed: "
            f"{type(error).__name__}: {error}",
            flush=True,
        )
    finally:
        if wav_file:
            try:
                os.unlink(wav_file)
            except OSError:
                pass

    return True


def main():
    start_engine_heartbeat()
    current_identity = None
    print("Radio RRR AI Genre Detector", flush=True)
    print(f"Router: {RADIO_ROUTER_URL}", flush=True)
    print(f"Sample: {SAMPLE_SECONDS}s", flush=True)
    print(f"Top genres: {TOP_N}", flush=True)
    print("", flush=True)
    while True:
        try:
            process_pending_dj_test()
            dj = get_current_relay()
            if not dj:
                if current_identity is not None:
                    print("No active relay. Waiting...", flush=True)
                    current_identity = None
                time.sleep(DJ_POLL_SECONDS)
                continue
            identity = (
                str(dj.get("platform") or "TikTok").casefold(),
                str(dj["username"]).casefold(),
            )
            if identity != current_identity:
                print(
                    f"Relay changed to {dj.get('platform') or 'TikTok'} "
                    f"@{dj['username']} ({dj['name']})",
                    flush=True,
                )
                current_identity = identity
            results, bpm_result, speech_result, sound_result = analyse_current_dj(dj)
            publish_current_results(dj, results, bpm_result, speech_result, sound_result)
            print_results(dj, results, bpm_result, speech_result)
            new_dj = get_current_relay()
            if not new_dj:
                print("Relay ended.", flush=True)
                current_identity = None
                continue
            new_identity = (
                str(new_dj.get("platform") or "TikTok").casefold(),
                str(new_dj["username"]).casefold(),
            )
            if new_identity != current_identity:
                print(
                    f"DJ changed during analysis: {current_identity} -> {new_identity}",
                    flush=True,
                )
                current_identity = new_identity
        except KeyboardInterrupt:
            print("\nStopping detector.", flush=True)
            break
        except Exception as e:
            print(f"Detector error: {type(e).__name__}: {e}", flush=True)
            time.sleep(DJ_POLL_SECONDS)

if __name__ == "__main__":
    main()
