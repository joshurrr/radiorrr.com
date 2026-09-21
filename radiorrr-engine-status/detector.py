import os
import subprocess
import time
import tempfile
import json
from urllib.request import urlopen, Request
from urllib.parse import quote

import essentia.standard as es

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
    if not username:
        return None
    return {"username": username, "name": name}


def analyse_audio(audio_file):
    audio = es.MonoLoader(filename=audio_file, sampleRate=16000)()
    embeddings = embedding_model(audio)
    predictions = genre_model(embeddings)
    scores = predictions.mean(axis=0)
    results = sorted(zip(classes, scores), key=lambda x: float(x[1]), reverse=True)
    return [{"genre": genre, "confidence": float(score)} for genre, score in results[:TOP_N] if float(score) >= MIN_CONFIDENCE]

def analyse_current_dj(dj):
    wav_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_file = tmp.name

        stream_url = f"{RADIO_ROUTER_URL}/api/live-stream?dj={quote(dj['username'], safe='')}"

        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel", "error",
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
        )

        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"FFmpeg failed: {error}")

        if os.path.getsize(wav_file) < 10000:
            raise RuntimeError("FFmpeg produced an empty/invalid WAV file")

        return analyse_audio(wav_file)

    finally:
        if wav_file:
            try:
                os.unlink(wav_file)
            except Exception:
                pass
def print_results(dj, results):
    print("", flush=True)
    print("=" * 60, flush=True)
    print(f"Current DJ: @{dj['username']} ({dj['name']})", flush=True)
    print("AI detected genre mix:", flush=True)
    print("-" * 60, flush=True)
    for index, result in enumerate(results, start=1):
        print(f"{index:2}. {result['genre']:<30} {result['confidence'] * 100:6.2f}%", flush=True)
    print("=" * 60, flush=True)

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


def main():
    start_engine_heartbeat()
    current_username = None
    print("Radio RRR AI Genre Detector", flush=True)
    print(f"Router: {RADIO_ROUTER_URL}", flush=True)
    print(f"Sample: {SAMPLE_SECONDS}s", flush=True)
    print(f"Top genres: {TOP_N}", flush=True)
    print("", flush=True)
    while True:
        try:
            dj = get_current_relay()
            if not dj:
                if current_username is not None:
                    print("No active relay. Waiting...", flush=True)
                    current_username = None
                time.sleep(DJ_POLL_SECONDS)
                continue
            if dj["username"] != current_username:
                print(f"Relay changed to @{dj['username']} ({dj['name']})", flush=True)
                current_username = dj["username"]
            results = analyse_current_dj(dj)
            print_results(dj, results)
            new_dj = get_current_relay()
            if not new_dj:
                print("Relay ended.", flush=True)
                current_username = None
                continue
            if new_dj["username"] != current_username:
                print(f"DJ changed during analysis: @{current_username} -> @{new_dj['username']}", flush=True)
                current_username = new_dj["username"]
        except KeyboardInterrupt:
            print("\nStopping detector.", flush=True)
            break
        except Exception as e:
            print(f"Detector error: {type(e).__name__}: {e}", flush=True)
            time.sleep(DJ_POLL_SECONDS)

if __name__ == "__main__":
    main()
