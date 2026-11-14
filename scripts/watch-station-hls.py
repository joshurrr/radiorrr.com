"""Read-only NAS/public HLS comparison. Writes timestamped JSON lines to stdout."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


def get(url):
    request = Request(url, headers={
        "Origin": "https://radiorrr.com",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    })
    with urlopen(request, timeout=5) as response:
        return response.read(16 * 1024 * 1024), {
            "status": response.status,
            "cache": response.headers.get("CF-Cache-Status"),
            "age": response.headers.get("Age"),
        }


def sample(base, previous, ffmpeg):
    result = {"at": datetime.now(timezone.utc).isoformat(), "endpoint": base}
    playlist_url = base + "/api/live-stream"
    try:
        body, headers = get(playlist_url)
    except HTTPError as error:
        return dict(result, status=error.code, cache=error.headers.get("CF-Cache-Status"),
                    body=error.read(100).decode(errors="replace"))
    except (URLError, OSError) as error:
        return dict(result, error=str(error))

    result.update(headers)
    playlist = body.decode(errors="replace")
    sequence = re.search(r"(?m)^#EXT-X-MEDIA-SEQUENCE:(\d+)", playlist)
    if sequence:
        result["media_sequence"] = int(sequence[1])
    segments = [line for line in playlist.splitlines() if line and not line.startswith("#")]
    result["segments"] = len(segments)
    if not segments:
        return result

    result["tail_sequence"] = result.get("media_sequence", 0) + len(segments) - 1
    segment = urljoin(playlist_url, segments[-1])
    if urlsplit(segment).netloc != urlsplit(base).netloc:
        return dict(result, segment_error="Unexpected segment host")
    result["segment"] = urlsplit(segment).path
    try:
        data, headers = get(segment)
        result.update(segment_status=headers["status"], bytes=len(data),
                      sha256=hashlib.sha256(data).hexdigest())
        if previous.get("sha256") == result["sha256"] and previous.get("audio_decoded"):
            return dict(result, audio_decoded=True,
                        nonzero_audio_samples=previous["nonzero_audio_samples"])
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-v", "error", "-xerror", "-i", "pipe:0",
             "-map", "0:a:0", "-f", "s16le", "pipe:1"],
            input=data, capture_output=True, timeout=8,
        )
        result["audio_decoded"] = decoded.returncode == 0 and bool(decoded.stdout)
        if result["audio_decoded"]:
            result["nonzero_audio_samples"] = any(decoded.stdout)
        else:
            result["audio_error"] = decoded.stderr.decode(errors="replace")[-500:]
    except HTTPError as error:
        result["segment_status"] = error.code
    except (OSError, URLError, subprocess.TimeoutExpired) as error:
        result["segment_error"] = str(error)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nas", help="NAS origin including port")
    parser.add_argument("--public", default="https://stream.radiorrr.com")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--samples", type=int, default=120)
    args = parser.parse_args()
    origins = [args.nas.rstrip("/"), args.public.rstrip("/")]
    previous = [{}, {}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for index in range(args.samples):
            if index:
                time.sleep(10)
            requests = [pool.submit(sample, origin, last, args.ffmpeg)
                        for origin, last in zip(origins, previous)]
            previous = [request.result() for request in requests]
            for result in previous:
                print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
