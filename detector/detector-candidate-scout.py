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

RADIO_ROUTER_URL = os.getenv(
    "RADIO_ROUTER_URL",
    "http://host.docker.internal:8000",
).rstrip("/")

# One 30-second sample per live DJ. With ~10 live DJs, the pool refreshes
# roughly every 5 minutes plus relay startup/inference overhead.
SAMPLE_SECONDS = int(os.getenv("SAMPLE_SECONDS", "30"))
TOP_N = int(os.getenv("TOP_N", "10"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.05"))
SCAN_IDLE_SECONDS = int(os.getenv("SCAN_IDLE_SECONDS", "5"))
SKIP_CURRENT_RELAY = os.getenv("SKIP_CURRENT_RELAY", "1").lower() not in ("0", "false", "no")

EFFNET_MODEL = "/app/models/discogs-effnet-bs64-1.pb"
GENRE_MODEL = "/app/models/genre_discogs400-discogs-effnet-1.pb"
GENRE_JSON = "/app/models/genre_discogs400-discogs-effnet-1.json"

print("Loading Essentia models...", flush=True)

embedding_model = es.TensorflowPredictEffnetDiscogs(
    graphFilename=EFFNET_MODEL,
    output="PartitionedCall:1",
)
genre_model = es.TensorflowPredict2D(
    graphFilename=GENRE_MODEL,
    input="serving_default_model_Placeholder",
    output="PartitionedCall:0",
)

with open(GENRE_JSON, "r", encoding="utf-8") as f:
    genre_metadata = json.load(f)

classes = genre_metadata["classes"]
print(f"Loaded {len(classes)} Discogs genre classes.", flush=True)
sound_model = load_model(es)


def get_json(path):
    req = Request(
        f"{RADIO_ROUTER_URL}{path}",
        headers={"Accept": "application/json"},
    )
    with urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def get_live_djs():
    data = get_json("/api/live")
    live = data.get("live") or []
    result = []
    seen = set()

    for dj in live:
        username = str(dj.get("username") or "").lstrip("@").strip()
        platform = str(dj.get("platform") or "TikTok").strip() or "TikTok"
        if not username:
            continue
        key = (platform.casefold(), username.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "username": username,
            "name": dj.get("name") or username,
            "platform": platform,
        })

    return result


def get_current_relay_identity():
    try:
        data = get_json("/api/live")
        relay = data.get("relay") or {}
        username = str(relay.get("username") or "").lstrip("@").strip()
        platform = str(relay.get("platform") or "TikTok").strip() or "TikTok"
        if not username:
            return None
        return (platform.casefold(), username.casefold())
    except Exception:
        return None


def analyse_audio(audio_file, include_sound=False):
    audio = es.MonoLoader(
        filename=audio_file,
        sampleRate=16000,
        resampleQuality=4,
    )()

    embeddings = embedding_model(audio)
    predictions = genre_model(embeddings)

    if getattr(predictions, "ndim", 0) != 2 or predictions.shape[0] == 0:
        raise RuntimeError(
            f"Unexpected genre prediction shape: {getattr(predictions, 'shape', None)}"
        )

    scores = predictions.mean(axis=0)
    results = sorted(
        zip(classes, scores),
        key=lambda x: float(x[1]),
        reverse=True,
    )

    genres = [
        {
            "genre": genre,
            "confidence": float(score),
        }
        for genre, score in results[:TOP_N]
        if float(score) >= MIN_CONFIDENCE
    ]
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
    audio = es.MonoLoader(
        filename=audio_file,
        sampleRate=44100,
        resampleQuality=4,
    )()

    rhythm = es.RhythmExtractor2013(method="multifeature")
    bpm, beats, raw_confidence, _estimates, _intervals = rhythm(audio)

    bpm = float(bpm or 0.0)
    raw_confidence = max(0.0, float(raw_confidence or 0.0))

    if bpm <= 0 or len(beats) == 0:
        return None

    confidence = min(1.0, raw_confidence / 5.32)

    return {
        "analysis": "bpm",
        "bpm": round(bpm, 2),
        "bpm_confidence": round(confidence, 4),
    }



def analyse_speech(audio_file):
    """Estimate persistent DJ mic/talk activity from the captured WAV.

    WebRTC VAD is only supporting evidence because electronic music can trigger
    it heavily. A duck must now be a deeper, sustained loudness reduction that
    recovers promptly, and speech activity must increase inside the duck versus
    the surrounding audio. This makes ordinary beats/breakdowns much less likely
    to be mistaken for DJ mic chatter.
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

    def vad_ratio(start_loudness, end_loudness):
        vad_frames_per_loudness = max(1, loudness_frame_ms // frame_ms)
        vad_start = max(0, start_loudness * vad_frames_per_loudness)
        vad_end = min(len(qualified), end_loudness * vad_frames_per_loudness)
        if vad_end <= vad_start:
            return 0.0
        return (
            sum(1 for value in qualified[vad_start:vad_end] if value)
            / (vad_end - vad_start)
        )

    # First find plausible loudness ducks. Thresholds are intentionally
    # conservative: at least 6 dB deep and 0.7-5.0 seconds long.
    raw_duck_events = []
    index = 10

    while index + 5 < len(loudness_db):
        pre_level = median(loudness_db[index - 10:index])
        low_level = median(loudness_db[index:index + 5])
        drop_db = pre_level - low_level

        if drop_db < 6.0 or low_level < -55.0:
            index += 1
            continue

        recovery_index = None
        max_recovery = min(len(loudness_db) - 3, index + 50)

        for candidate in range(index + 7, max_recovery + 1):
            recovered_level = median(loudness_db[candidate:candidate + 5])
            if recovered_level >= pre_level - 1.0:
                recovery_index = candidate
                break

        if recovery_index is None:
            index += 1
            continue

        duration_frames = recovery_index - index
        duration_seconds = duration_frames * (loudness_frame_ms / 1000.0)

        if 0.7 <= duration_seconds <= 5.0:
            valley_level = median(loudness_db[index:recovery_index])
            depth_db = max(0.0, pre_level - valley_level)
            if depth_db >= 6.0:
                raw_duck_events.append((index, recovery_index, depth_db))
                index = recovery_index + 5
                continue

        index += 1

    # A real mic duck should have stronger speech evidence inside the duck than
    # in the nearby music. This relative test is important when WebRTC VAD is
    # already firing heavily on the music itself.
    duck_events = []
    event_overlaps = []

    for start, end, depth in raw_duck_events:
        inside_ratio = vad_ratio(start, end)

        context_start = max(0, start - 10)
        context_end = min(len(loudness_db), end + 10)
        before_ratio = vad_ratio(context_start, start)
        after_ratio = vad_ratio(end, context_end)

        context_parts = []
        if start > context_start:
            context_parts.append(before_ratio)
        if context_end > end:
            context_parts.append(after_ratio)
        context_ratio = (
            sum(context_parts) / len(context_parts)
            if context_parts
            else speech_ratio
        )

        if (
            inside_ratio >= 0.55
            and inside_ratio >= context_ratio + 0.12
        ):
            duck_events.append((start, end, depth))
            event_overlaps.append(inside_ratio)

    ducked_loudness_frames = sum(end - start for start, end, _ in duck_events)
    ducking_ratio = (
        ducked_loudness_frames / len(loudness_db)
        if loudness_db
        else 0.0
    )

    speech_duck_overlap = (
        sum(event_overlaps) / len(event_overlaps)
        if event_overlaps
        else 0.0
    )

    average_depth = (
        sum(depth for _start, _end, depth in duck_events) / len(duck_events)
        if duck_events
        else 0.0
    )

    event_factor = min(1.0, len(duck_events) / 3.0)
    depth_factor = min(1.0, average_depth / 10.0)
    ducking_confidence = (
        0.50 * event_factor
        + 0.25 * depth_factor
        + 0.25 * speech_duck_overlap
    )

    # Do not score one isolated duck as persistent chatter. Two or more
    # speech-correlated ducks are required before talk_ratio can affect ranking.
    if len(duck_events) >= 2:
        talk_ratio = ducking_ratio * (0.65 + 0.35 * speech_duck_overlap)
        talk_confidence = ducking_confidence
    else:
        talk_ratio = 0.0
        talk_confidence = 0.0

    return {
        "analysis": "speech",
        "speech_ratio": round(speech_ratio, 4),
        "speech_confidence": round(min(1.0, speech_ratio * 2.0), 4),
        "ducking_ratio": round(min(1.0, ducking_ratio), 4),
        "ducking_events": len(duck_events),
        "ducking_candidates": len(raw_duck_events),
        "ducking_confidence": round(min(1.0, ducking_confidence), 4),
        "talk_ratio": round(min(1.0, talk_ratio), 4),
        "talk_confidence": round(min(1.0, talk_confidence), 4),
    }

def analyse_dj(dj):
    wav_file = None

    try:
        with tempfile.NamedTemporaryFile(
            suffix=".wav",
            delete=False,
        ) as tmp:
            wav_file = tmp.name

        stream_url = (
            f"{RADIO_ROUTER_URL}/api/live-stream"
            f"?dj={quote(dj['username'], safe='')}"
            f"&platform={quote(dj.get('platform') or 'TikTok', safe='')}"
        )

        print(
            f"[Scout] Capturing {SAMPLE_SECONDS}s for "
            f"{dj.get('platform') or 'TikTok'} @{dj['username']}...",
            flush=True,
        )

        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
                "-y",
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
            check=False,
        )

        if result.returncode != 0:
            error = result.stderr.decode(
                "utf-8", errors="replace"
            ).strip()
            raise RuntimeError(
                f"FFmpeg exit {result.returncode}: "
                f"{error or 'no error output'}"
            )

        if not os.path.exists(wav_file):
            raise RuntimeError("FFmpeg did not create WAV")

        if os.path.getsize(wav_file) < 10000:
            raise RuntimeError(
                f"Invalid WAV ({os.path.getsize(wav_file)} bytes)"
            )

        genres, sound_result = analyse_audio(wav_file, include_sound=True)
        return genres, analyse_bpm(wav_file), analyse_speech(wav_file), sound_result

    finally:
        if wav_file:
            try:
                os.unlink(wav_file)
            except Exception:
                pass


def post_results(dj, results, bpm_result=None, speech_result=None, sound_result=None):
    published_results = list(results)
    if bpm_result:
        # Keep analysis metadata inside the existing genres_json payload so
        # RadioRouter needs no schema migration. Entries without a genre are
        # ignored by the learned genre-profile code.
        published_results.append(bpm_result)
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

    print(
        f"[Scout] Published AI genres for "
        f"{dj.get('platform') or 'TikTok'} @{dj['username']}",
        flush=True,
    )


def release_relay(dj):
    payload = json.dumps({}).encode("utf-8")
    req = Request(
        f"{RADIO_ROUTER_URL}/api/live-stream-release"
        f"?dj={quote(dj['username'], safe='')}"
        f"&platform={quote(dj.get('platform') or 'TikTok', safe='')}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=10) as response:
            response.read()
    except Exception as error:
        print(
            f"[Scout] Warning: could not release "
            f"{dj.get('platform') or 'TikTok'} @{dj['username']}: {error}",
            flush=True,
        )


def print_results(dj, results, bpm_result=None, speech_result=None):
    top = ", ".join(
        f"{r['genre']}={r['confidence']:.1%}"
        for r in results[:5]
    )
    bpm_text = ""
    if bpm_result:
        bpm_text = (
            f" | BPM={bpm_result['bpm']:.1f} "
            f"({bpm_result['bpm_confidence']:.0%})"
        )
    speech_text = ""
    if speech_result:
        speech_text = (
            f" | Speech={speech_result['speech_ratio']:.0%}"
            f" | Duck={speech_result['ducking_ratio']:.0%}"
            f"/{speech_result['ducking_events']}"
            f"({speech_result.get('ducking_candidates', speech_result['ducking_events'])} raw)"
            f" | Talk={speech_result['talk_ratio']:.0%}"
        )
    print(
        f"[Scout] {dj.get('platform') or 'TikTok'} @{dj['username']} -> "
        f"{top or 'no predictions'}{bpm_text}{speech_text}",
        flush=True,
    )


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
                    f"{RADIO_ROUTER_URL}/api/engine-heartbeat/genre-candidate-scout",
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


# Priority jobs are consumed between normal samples, without interrupting capture.
def priority_post(path, payload):
    req = Request(f'{RADIO_ROUTER_URL}{path}', data=json.dumps(payload).encode('utf-8'),
                  headers={'Content-Type':'application/json','Accept':'application/json'},method='POST')
    with urlopen(req,timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))


def scan_priority_djs(max_scans=3):
    scanned=set()
    for _ in range(max_scans):
        try:
            dj=priority_post('/api/ai-genre/scan-priority/next',{}).get('dj')
        except Exception as error:
            print(f'[Scout] Priority queue unavailable: {error}',flush=True)
            break
        if not dj:
            break
        success=False
        try:
            print(f"[Scout] PRIORITY {dj['platform']} @{dj['username']}",flush=True)
            results,bpm_result,speech_result,sound_result=analyse_dj(dj)
            post_results(dj,results,bpm_result,speech_result,sound_result)
            print_results(dj,results,bpm_result,speech_result)
            scanned.add((dj['platform'].casefold(),dj['username'].casefold()))
            success=True
        except Exception as error:
            print(f"[Scout] PRIORITY FAILED {dj['platform']} @{dj['username']}: {error}",flush=True)
        finally:
            try:
                priority_post(f"/api/ai-genre/scan-priority/{dj['request_id']}/complete",{'success':success})
            except Exception as error:
                print(f'[Scout] Priority completion failed: {error}',flush=True)
            release_relay(dj)
    return scanned


def main():
    start_engine_heartbeat()
    print("Radio RRR Candidate Audio Scout", flush=True)
    print(f"Router: {RADIO_ROUTER_URL}", flush=True)
    print(f"Sample: {SAMPLE_SECONDS}s", flush=True)
    print(
        "Mode: sequential live-DJ scan; current relay skipped"
        if SKIP_CURRENT_RELAY
        else "Mode: sequential live-DJ scan; current relay included",
        flush=True,
    )
    print("", flush=True)

    cycle = 0

    while True:
        try:
            cycle += 1
            priority_scanned = scan_priority_djs()
            live_djs = get_live_djs()
            current = get_current_relay_identity()

            if SKIP_CURRENT_RELAY and current:
                scan_djs = [
                    dj for dj in live_djs
                    if (
                        (str(dj.get("platform") or "TikTok").casefold(),
                         dj["username"].casefold())
                        != current
                    )
                ]
            else:
                scan_djs = live_djs

            if not scan_djs:
                print(
                    "[Scout] No candidate DJs available; "
                    "waiting...",
                    flush=True,
                )
                time.sleep(30)
                continue

            print(
                f"[Scout] === Cycle {cycle}: "
                f"{len(live_djs)} live, "
                f"{len(scan_djs)} candidates ===",
                flush=True,
            )

            for index, dj in enumerate(scan_djs, start=1):
                priority_scanned.update(scan_priority_djs())
                if (str(dj.get("platform") or "TikTok").casefold(), dj["username"].casefold()) in priority_scanned:
                    continue

                # Re-check the live list before each expensive sample so
                # DJs who have gone offline are not unnecessarily analysed.
                try:
                    current_live = get_live_djs()
                    live_keys = {
                        (
                            str(x.get("platform") or "TikTok").casefold(),
                            x["username"].casefold(),
                        )
                        for x in current_live
                    }
                    dj_key = (
                        str(dj.get("platform") or "TikTok").casefold(),
                        dj["username"].casefold(),
                    )
                    if dj_key not in live_keys:
                        print(
                            f"[Scout] Skipping "
                            f"{dj.get('platform') or 'TikTok'} @{dj['username']} "
                            "— no longer live",
                            flush=True,
                        )
                        continue

                    # If the DJ has become the main relay while we were
                    # scanning, leave the current relay to the fast detector.
                    current = get_current_relay_identity()
                    if (
                        SKIP_CURRENT_RELAY
                        and current
                        and dj_key == current
                    ):
                        print(
                            f"[Scout] Skipping "
                            f"{dj.get('platform') or 'TikTok'} @{dj['username']} "
                            "— now current relay",
                            flush=True,
                        )
                        continue

                    print(
                        f"[Scout] {index}/{len(scan_djs)} "
                        f"{dj.get('platform') or 'TikTok'} @{dj['username']}",
                        flush=True,
                    )

                    results, bpm_result, speech_result, sound_result = analyse_dj(dj)
                    post_results(dj, results, bpm_result, speech_result, sound_result)
                    print_results(dj, results, bpm_result, speech_result)

                except Exception as error:
                    print(
                        f"[Scout] Analysis failed for "
                        f"{dj.get('platform') or 'TikTok'} @{dj['username']}: "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )
                finally:
                    # The per-DJ relay is only needed for this sample.
                    # Release it immediately so the next candidate does
                    # not accumulate another FFmpeg process.
                    release_relay(dj)

                time.sleep(SCAN_IDLE_SECONDS)

            print(
                f"[Scout] === Cycle {cycle} complete ===",
                flush=True,
            )

        except KeyboardInterrupt:
            print("\nStopping candidate scout.", flush=True)
            break
        except Exception as error:
            print(
                f"[Scout] Cycle error: {type(error).__name__}: {error}",
                flush=True,
            )
            time.sleep(15)


if __name__ == "__main__":
    main()
