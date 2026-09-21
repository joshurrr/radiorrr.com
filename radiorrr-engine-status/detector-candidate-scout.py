import os
import subprocess
import time
import tempfile
import json
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.parse import quote

import essentia.standard as es

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
        if not username:
            continue
        key = username.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "username": username,
            "name": dj.get("name") or username,
        })

    return result


def get_current_relay_username():
    try:
        data = get_json("/api/live")
        relay = data.get("relay") or {}
        username = str(relay.get("username") or "").lstrip("@").strip()
        return username or None
    except Exception:
        return None


def analyse_audio(audio_file):
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

    return [
        {
            "genre": genre,
            "confidence": float(score),
        }
        for genre, score in results[:TOP_N]
        if float(score) >= MIN_CONFIDENCE
    ]


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
        )

        print(
            f"[Scout] Capturing {SAMPLE_SECONDS}s for "
            f"@{dj['username']}...",
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
                "-ar", "16000",
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

        return analyse_audio(wav_file)

    finally:
        if wav_file:
            try:
                os.unlink(wav_file)
            except Exception:
                pass


def post_results(dj, results):
    payload = json.dumps({
        "username": dj["username"],
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "genres": results,
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

    print(
        f"[Scout] Published AI genres for @{dj['username']}",
        flush=True,
    )


def release_relay(username):
    payload = json.dumps({}).encode("utf-8")
    req = Request(
        f"{RADIO_ROUTER_URL}/api/live-stream-release"
        f"?dj={quote(username, safe='')}",
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
            f"@{username}: {error}",
            flush=True,
        )


def print_results(dj, results):
    top = ", ".join(
        f"{r['genre']}={r['confidence']:.1%}"
        for r in results[:5]
    )
    print(
        f"[Scout] @{dj['username']} -> {top or 'no predictions'}",
        flush=True,
    )


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
            live_djs = get_live_djs()
            current = get_current_relay_username()

            if SKIP_CURRENT_RELAY and current:
                scan_djs = [
                    dj for dj in live_djs
                    if dj["username"].lower() != current.lower()
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
                # Re-check the live list before each expensive sample so
                # DJs who have gone offline are not unnecessarily analysed.
                try:
                    current_live = get_live_djs()
                    live_keys = {
                        x["username"].lower()
                        for x in current_live
                    }
                    if dj["username"].lower() not in live_keys:
                        print(
                            f"[Scout] Skipping @{dj['username']} "
                            "— no longer live",
                            flush=True,
                        )
                        continue

                    # If the DJ has become the main relay while we were
                    # scanning, leave the current relay to the fast detector.
                    current = get_current_relay_username()
                    if (
                        SKIP_CURRENT_RELAY
                        and current
                        and dj["username"].lower() == current.lower()
                    ):
                        print(
                            f"[Scout] Skipping @{dj['username']} "
                            "— now current relay",
                            flush=True,
                        )
                        continue

                    print(
                        f"[Scout] {index}/{len(scan_djs)} "
                        f"@{dj['username']}",
                        flush=True,
                    )

                    results = analyse_dj(dj)
                    post_results(dj, results)
                    print_results(dj, results)

                except Exception as error:
                    print(
                        f"[Scout] Analysis failed for "
                        f"@{dj['username']}: "
                        f"{type(error).__name__}: {error}",
                        flush=True,
                    )
                finally:
                    # The per-DJ relay is only needed for this sample.
                    # Release it immediately so the next candidate does
                    # not accumulate another FFmpeg process.
                    release_relay(dj["username"])

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
