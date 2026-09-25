import os
import math
from contextlib import asynccontextmanager, closing
import asyncio
import httpx
import sqlite3
import json
import shutil
import subprocess
from urllib.parse import quote
from pathlib import Path
from datetime import datetime, timedelta, timezone
import re
from uuid import UUID, uuid4
import time
import random
import sys
import threading
import traceback
import socket
import ipaddress
import wave
from array import array
from urllib.parse import urlparse
from pydantic import BaseModel

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from TikTokLive import TikTokLiveClient
from radio_rrr_genre_matcher import rank_live_djs


DB_PATH = "/data/radiorouter.db"
CHECK_INTERVAL = 60

# ---------------------------------------------------------
# TWITCH API / LIVE MONITOR
# ---------------------------------------------------------
# Twitch credentials are supplied to the container through radiorouter.env.
# The monitor uses a server-side app access token and batches up to 100
# configured Twitch logins per Helix Get Streams request.
TWITCH_CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
TWITCH_CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()
TWITCH_CHECK_INTERVAL = 60
TWITCH_TOKEN_REFRESH_MARGIN = 60

# YouTube favourites are checked independently with yt-dlp. Keep this slower
# than Twitch because each channel requires its own lightweight extraction.
YOUTUBE_CHECK_INTERVAL = 120
YOUTUBE_START_DELAY = 12
YOUTUBE_YTDLP_TIMEOUT = 35

twitch_app_token = None
twitch_app_token_expires_at = 0.0
twitch_token_lock = asyncio.Lock()

# ---------------------------------------------------------
# RESILIENT TIKTOK LIVENESS
# ---------------------------------------------------------
# TikTokLiveClient.is_live() can occasionally return a transient
# false-negative while the creator is still broadcasting. Never let
# one bad status response tear down a live DJ or the active relay.
#
# Normal DJs require 3 consecutive OFFLINE results before removal.
# The DJ currently feeding the shared relay gets a larger safety
# margin because stopping a healthy relay is much more disruptive.
OFFLINE_CONFIRMATIONS_REQUIRED = 3
ACTIVE_RELAY_OFFLINE_CONFIRMATIONS_REQUIRED = 5

# TikTok LIVE checks remain strictly sequential. Testing showed TikTok becomes
# unreliable when requests are started faster than roughly one every 2 seconds,
# so performance comes from checking likely-live DJs more intelligently rather
# than increasing request rate.
TIKTOK_LIVE_CHECK_DELAY = 2.0
TIKTOK_LIVE_ERROR_BACKOFF_THRESHOLD = 5
TIKTOK_LIVE_ERROR_BACKOFF_SECONDS = 5 * 60

# Adaptive scan intervals. DJs with a proven recent/frequent LIVE history are
# checked first and more often; cold accounts consume progressively fewer
# TikTok requests. Newly tracked accounts get a learning window before being
# treated as cold.
TIKTOK_SCAN_CURRENTLY_LIVE = 90
TIKTOK_SCAN_DAILY = 3 * 60
TIKTOK_SCAN_WEEKLY = 5 * 60
TIKTOK_SCAN_OCCASIONAL = 15 * 60
TIKTOK_SCAN_UNKNOWN_NEW = 4 * 60
TIKTOK_SCAN_COLD = 30 * 60
TIKTOK_NEW_DJ_LEARNING_DAYS = 14

# Never-seen-live accounts should not stay in the fast learning tier for two
# full weeks if they have already produced repeated clean OFFLINE results.
# This reduces catalogue polling load while preserving an initial discovery
# window for newly-added DJs.
TIKTOK_UNKNOWN_FAST_CHECKS = 5
TIKTOK_UNKNOWN_WARM_CHECKS = 10

# The continuous scheduler re-evaluates due DJs after every single TikTok
# request. A short idle poll keeps 90-second/3-minute regular-DJ intervals
# meaningful without increasing TikTok request rate.
TIKTOK_SCHEDULER_IDLE_SLEEP = 10
TIKTOK_MAINTENANCE_INTERVAL = 60

# If a DJ has not been observed LIVE for 30 days, remove it from normal
# discovery/scanning and probe it only once per week. A confirmed LIVE result
# automatically restores it to the normal enabled pool.
TIKTOK_DORMANT_AFTER = 30 * 24 * 60 * 60
TIKTOK_DORMANT_RECHECK = 7 * 24 * 60 * 60

# TikTokLive uses one broad exception for accounts that cannot go LIVE,
# have never gone LIVE, or no longer exist. Treat this separately from
# transient API/network failures. Require repeated observations before any
# automatic cleanup action.
TIKTOK_INVALID_ACCOUNT_CONFIRMATIONS_REQUIRED = 3
TIKTOK_INVALID_ACCOUNT_RECHECK_SECONDS = 6 * 60 * 60

# A successful LIVE refresh keeps a row fresh. This is only a final
# safety net for rows that stop receiving successful refreshes because
# of an exception or monitor problem. Ten minutes gives the relay
# considerably more tolerance for temporary TikTok/API trouble.
LIVE_STALE_AFTER = 10 * 60

PROFILE_REFRESH_INTERVAL = 24 * 60 * 60
PROFILE_REFRESH_START_DELAY = 10
PROFILE_REFRESH_TIMEOUT = 60
PROFILE_REFRESH_CHECK_INTERVAL = 15 * 60

# Keep background profile maintenance deliberately slow and stop the current
# pass when TikTok starts returning 403 responses.
PROFILE_REFRESH_REQUEST_DELAY_MIN = 3.0
PROFILE_REFRESH_REQUEST_DELAY_MAX = 5.0
PROFILE_REFRESH_FAILURE_COOLDOWN = 6 * 60 * 60
PROFILE_REFRESH_403_BACKOFFS = (5 * 60, 15 * 60, 30 * 60)

# ---------------------------------------------------------
# STAGE 3 — automatic default DJ selection
# ---------------------------------------------------------
# The matcher may control ONLY the shared/default Radio RRR relay.
# Personal DJ clicks continue to use /api/live-stream?dj=USERNAME.
STAGE3_ENABLED = True

# Do not switch away from a healthy current DJ unless the new candidate
# is materially better. This prevents unnecessary relay churn.
STAGE3_MIN_SCORE = 5.0
STAGE3_SWITCH_MARGIN = 6.0

# Require the same candidate to win repeatedly before switching away from a
# healthy relay. When no healthy relay exists, selection is immediate below.
STAGE3_REQUIRED_WINS = 3
STAGE3_DECISION_INTERVAL = 10
STAGE3_FAILURE_COOLDOWN = 5 * 60

# A current relay remains protected if it still has a reasonable match.
STAGE3_CURRENT_MIN_SCORE = 5.0

# Genres/content explicitly avoided by a programme. These are editable per
# schedule slot in the Scheduler Admin. The non-music defaults cover common
# talking/Q&A content without requiring it to be treated as a music genre.
DEFAULT_ANTI_GENRES = [
    "Spoken Word", "Talk", "Talking", "Dialogue", "Comedy", "Parody",
    "Audiobook", "Radioplay", "Education", "Poetry", "Religious",
    "Field Recording",
]
DEFAULT_DINNER_WARMUP_ANTI_GENRES = [
    "Hardcore", "Gabber", "Speedcore", "Hardstyle",
]
# Music anti-genres use a confidence dead-zone so weak secondary classifier
# hits do not materially down-rate an otherwise good programme match.
ANTI_GENRE_PENALTY_START = 0.15
ANTI_GENRE_HARD_CONFIDENCE = 0.40
ANTI_GENRE_MAX_PENALTY = 35.0

# Spoken/non-music content remains deliberately aggressive.
NON_MUSIC_ANTI_CONFIDENCE = 0.08

# Speech analysis is currently diagnostic-only. WebRTC VAD is over-detecting
# speech on music streams, so Stage 3 must not use it for ranking until the
# detector is replaced with a more suitable speech/music classifier.
SPEECH_SCORING_ENABLED = False
SPEECH_PENALTY_START = 0.08
SPEECH_STRONG_START = 0.20
SPEECH_HARD_EXCLUDE = 0.55
SPEECH_MAX_PENALTY = 22.0


# AI freshness affects confidence rather than eligibility. Learned profiles
# remain useful when live AI observations are older or temporarily absent.
STAGE3_AI_FRESH_AGE = 15 * 60
STAGE3_AI_DECAY_AGE = 60 * 60


# ---------------------------------------------------------
# LIVE DJ stream relay
# ---------------------------------------------------------

RELAY_DIR = Path("/tmp/radiorouter-live")
RELAY_PLAYLIST = RELAY_DIR / "index.m3u8"
MANUAL_RELAY_DIR = Path("/tmp/radiorouter-manual")
relay_process = None
relay_username = None
relay_platform = None
relay_lock = asyncio.Lock()


def relay_identity_key(platform, username):
    """Return a stable platform-qualified key for relay/Stage 3 state."""
    platform_key = str(platform or "TikTok").strip().casefold()
    username_key = str(username or "").lstrip("@").strip().lower()
    return f"{platform_key}:{username_key}"
# The public HLS timeline must remain monotonic when the underlying TikTok
# relay is replaced. Each candidate FFmpeg starts its own media sequence,
# so these values translate the active candidate sequence into one stable
# station-wide sequence and mark the exact DJ handover boundary.
relay_public_sequence_offset = 0
relay_public_discontinuity_sequence = None
# Shared with diagnostics; blocked DJs remain visible but cannot monopolise
# automatic selection while their stream is temporarily unavailable.
relay_failed_until = {}
# Keep old HLS segments briefly for clients finishing a pre-switch playlist.
retired_relay_outputs = []
RELAY_OUTPUT_RETENTION = 180

# Shared audio-only MP3 relay. The audio encoder reads the local HLS relay,
# so TikTok is contacted only once by the main relay process.
audio_process = None
audio_task = None
audio_clients = set()
audio_clients_lock = asyncio.Lock()
AUDIO_CHUNK_SIZE = 8192
AUDIO_QUEUE_SIZE = 8

manual_relays = {}
manual_relay_lock = asyncio.Lock()
profile_import_lock = asyncio.Lock()
profile_refresh_job_lock = asyncio.Lock()

# Profile-maintenance cooldowns are intentionally in-memory. Normal operation
# avoids retrying failed accounts too aggressively or continuing a batch after
# TikTok starts returning 403.
profile_refresh_failed_until = {}
profile_refresh_rate_limited_until = 0.0
profile_refresh_403_streak = 0

# Consecutive TikTok false-negative counters. These are intentionally
# in-memory: a process restart should not preserve a possibly stale
# offline count. A successful LIVE check always resets the counter.
offline_miss_counts = {}

# Consecutive TikTokLive "cannot/never LIVE/does not exist" observations.
# These are kept separate from transient CHECK ERRORs so stale/bad accounts
# do not trigger the API throttle circuit breaker.
invalid_tiktok_account_counts = {}

# ---------------------------------------------------------
# ADMIN DJ CANDIDATE TEST
# ---------------------------------------------------------
# LAN-only scheduler/admin helper. A candidate is sampled without being added
# to favourite_djs. The detector container polls for ready jobs, analyses the
# temporary WAV with the existing Essentia models, and posts the result back.
DJ_TEST_DIR = Path("/tmp/radiorouter-djtest")
DJ_TEST_SAMPLE_SECONDS = 30
DJ_TEST_MAX_SAMPLE_SECONDS = 90
DJ_TEST_JOB_TTL = 60 * 60
dj_test_jobs = {}
dj_test_lock = asyncio.Lock()

# ---------------------------------------------------------
# PUBLIC BPM DETECTOR TOOL
# ---------------------------------------------------------
# Lightweight public utility for direct HTTP(S) audio/HLS stream URLs. It is
# deliberately independent of the station relay and existing genre detector.
BPM_TOOL_SAMPLE_SECONDS = 24
BPM_TOOL_MAX_URL_LENGTH = 2048
BPM_TOOL_SAMPLE_RATE = 8000
BPM_TOOL_DIR = Path("/tmp/radiorouter-bpm-tool")
bpm_tool_semaphore = asyncio.Semaphore(2)


def _validate_public_stream_url(value):
    url = str(value or "").strip()
    if not url or len(url) > BPM_TOOL_MAX_URL_LENGTH:
        raise ValueError("Enter a valid direct stream URL")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only direct http:// or https:// stream URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("Stream URLs containing embedded credentials are not supported")

    hostname = parsed.hostname.strip().rstrip(".")
    if hostname.casefold() == "localhost":
        raise ValueError("Local/private stream addresses are not supported")

    try:
        resolved = socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("Could not resolve the stream hostname") from error

    addresses = {item[4][0].split("%", 1)[0] for item in resolved if item and item[4]}
    if not addresses:
        raise ValueError("Could not resolve the stream hostname")

    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as error:
            raise ValueError("Stream hostname resolved to an invalid address") from error
        if not ip.is_global:
            raise ValueError("Local/private stream addresses are not supported")

    return url


def _estimate_bpm_from_wav(path):
    """Estimate tempo from a short mono PCM WAV using onset-energy autocorrelation."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if channels != 1 or sample_width != 2 or rate <= 0:
        raise RuntimeError("Unexpected audio format returned by FFmpeg")

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()

    if len(samples) < rate * 8:
        raise RuntimeError("The stream did not provide enough audio to estimate BPM")

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
        raise RuntimeError("The stream did not provide enough rhythmic audio")

    log_energy = [math.log(max(value, 1e-8)) for value in energies]
    onset = [0.0]
    for index in range(1, len(log_energy)):
        onset.append(max(0.0, log_energy[index] - log_energy[index - 1]))

    smoothed = []
    for index in range(len(onset)):
        left = max(0, index - 1)
        right = min(len(onset), index + 2)
        smoothed.append(sum(onset[left:right]) / (right - left))

    mean = sum(smoothed) / len(smoothed)
    envelope = [value - mean for value in smoothed]
    envelope_energy = sum(value * value for value in envelope)
    if envelope_energy <= 1e-8:
        raise RuntimeError("No stable beat was detected in this sample")

    seconds_per_frame = frame_size / float(rate)
    min_bpm = 60.0
    max_bpm = 190.0
    min_lag = max(1, int(round(60.0 / (max_bpm * seconds_per_frame))))
    max_lag = min(len(envelope) // 2, int(round(60.0 / (min_bpm * seconds_per_frame))))

    candidates = []
    for lag in range(min_lag, max_lag + 1):
        left = envelope[:-lag]
        right = envelope[lag:]
        numerator = sum(a * b for a, b in zip(left, right))
        denom_left = sum(a * a for a in left)
        denom_right = sum(b * b for b in right)
        denominator = math.sqrt(max(denom_left * denom_right, 1e-12))
        correlation = numerator / denominator
        bpm = 60.0 / (lag * seconds_per_frame)

        # Electronic dance music commonly produces strong half-time peaks. Give
        # the musically useful 100-160 BPM range a small tie-break preference,
        # without preventing genuine slower/faster results.
        preference = 1.0
        if 100.0 <= bpm <= 160.0:
            preference = 1.08
        elif 80.0 <= bpm < 100.0 or 160.0 < bpm <= 180.0:
            preference = 1.03

        candidates.append((correlation * preference, correlation, bpm))

    if not candidates:
        raise RuntimeError("No stable beat was detected in this sample")

    candidates.sort(reverse=True)
    best_score, best_corr, best_bpm = candidates[0]

    # If the strongest result is below 90 BPM and its double has nearly the same
    # periodic support, prefer the doubled tempo. This reduces common 70-vs-140
    # half-time errors on dance streams.
    if best_bpm < 90.0:
        doubled = min(candidates, key=lambda item: abs(item[2] - (best_bpm * 2.0)))
        if doubled[1] >= best_corr * 0.72:
            best_score, best_corr, best_bpm = doubled

    distinct = [item for item in candidates[1:] if abs(item[2] - best_bpm) >= 3.0]
    second_corr = distinct[0][1] if distinct else 0.0
    confidence = max(0.0, min(1.0, (best_corr * 0.7) + max(0.0, best_corr - second_corr) * 1.5))

    if best_corr < 0.05:
        raise RuntimeError("No stable beat was detected in this sample")

    return round(best_bpm), round(confidence, 2)


def _capture_and_detect_bpm(url, output_path):
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_delay_max", "3",
        "-rw_timeout", "15000000",
        "-protocol_whitelist", "http,https,tcp,tls,crypto",
        "-i", url,
        "-t", str(BPM_TOOL_SAMPLE_SECONDS),
        "-vn",
        "-ac", "1",
        "-ar", str(BPM_TOOL_SAMPLE_RATE),
        "-c:a", "pcm_s16le",
        str(output_path),
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=BPM_TOOL_SAMPLE_SECONDS + 30,
        check=False,
    )

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 500:
            detail = detail[-500:]
        raise RuntimeError(detail or "FFmpeg could not read this stream")

    if not output_path.exists() or output_path.stat().st_size < 20000:
        raise RuntimeError("The stream did not provide enough audio")

    return _estimate_bpm_from_wav(output_path)

# ---------------------------------------------------------
# ADMIN PLAYBACK FREEZE TEST
# ---------------------------------------------------------
# Passive 60-second diagnostic used from /admin/djs. A tiny asyncio heartbeat
# runs on the Uvicorn event loop while a daemon watchdog thread observes it.
# If the event loop stops advancing for >= 1 second, the watchdog captures the
# blocked Python stack and a read-only snapshot of the active HLS relay.
FREEZE_TEST_DURATION = 60
FREEZE_TEST_BLOCK_THRESHOLD = 1.0
FREEZE_TEST_HEARTBEAT_INTERVAL = 0.10
FREEZE_TEST_MAX_EVENTS = 20

freeze_test_lock = threading.Lock()
freeze_test_state = {
    "active": False,
    "started_at": None,
    "finished_at": None,
    "ends_monotonic": 0.0,
    "last_tick_monotonic": 0.0,
    "loop_thread_id": None,
    "events": [],
    "current_event": None,
}


def _freeze_test_relay_snapshot():
    """Return a read-only relay/HLS snapshot safe to call from the watchdog."""
    now = time.time()
    playlist = RELAY_PLAYLIST

    snapshot = {
        "username": relay_username,
        "platform": relay_platform,
        "process_running": (
            relay_process is not None
            and relay_process.poll() is None
        ),
        "audio_running": (
            audio_process is not None
            and audio_process.poll() is None
        ),
        "playlist": str(playlist),
        "playlist_exists": False,
        "playlist_age_seconds": None,
        "segment_count": 0,
        "latest_segment": None,
        "latest_segment_age_seconds": None,
    }

    try:
        if not playlist.exists() or not playlist.is_file():
            return snapshot

        snapshot["playlist_exists"] = True
        snapshot["playlist_age_seconds"] = round(
            max(0.0, now - playlist.stat().st_mtime),
            3,
        )

        _, segment_names = hls_playlist_window(playlist)
        snapshot["segment_count"] = len(segment_names)

        latest_path = None
        latest_mtime = None

        for name in segment_names:
            path = playlist.parent / Path(name).name
            try:
                if not path.exists() or not path.is_file():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue

            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
                latest_path = path

        if latest_path is not None and latest_mtime is not None:
            snapshot["latest_segment"] = latest_path.name
            snapshot["latest_segment_age_seconds"] = round(
                max(0.0, now - latest_mtime),
                3,
            )

    except Exception as error:
        snapshot["snapshot_error"] = str(error)

    return snapshot


def _freeze_test_stack(thread_id):
    frame = sys._current_frames().get(thread_id)
    if frame is None:
        return "Event-loop thread stack was unavailable."
    return "".join(traceback.format_stack(frame))[-12000:]


def _freeze_test_finish_current_locked(now_monotonic):
    event = freeze_test_state.get("current_event")
    if not event:
        return

    event = dict(event)
    event["duration_seconds"] = round(
        max(0.0, now_monotonic - event.pop("_started_monotonic")),
        3,
    )
    freeze_test_state["events"].append(event)
    freeze_test_state["events"] = freeze_test_state["events"][-FREEZE_TEST_MAX_EVENTS:]
    freeze_test_state["current_event"] = None


async def _freeze_test_heartbeat():
    while True:
        with freeze_test_lock:
            if not freeze_test_state["active"]:
                return
            freeze_test_state["last_tick_monotonic"] = time.monotonic()

        await asyncio.sleep(FREEZE_TEST_HEARTBEAT_INTERVAL)


def _freeze_test_watchdog(loop_thread_id):
    while True:
        time.sleep(FREEZE_TEST_HEARTBEAT_INTERVAL)
        now_monotonic = time.monotonic()

        with freeze_test_lock:
            if not freeze_test_state["active"]:
                return

            ends_monotonic = freeze_test_state["ends_monotonic"]
            last_tick = freeze_test_state["last_tick_monotonic"]
            current_event = freeze_test_state["current_event"]

            if now_monotonic >= ends_monotonic:
                _freeze_test_finish_current_locked(now_monotonic)
                freeze_test_state["active"] = False
                freeze_test_state["finished_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
                return

        blocked_for = max(0.0, now_monotonic - last_tick)

        if blocked_for >= FREEZE_TEST_BLOCK_THRESHOLD and current_event is None:
            event = {
                "started_at": (
                    datetime.now(timezone.utc)
                    - timedelta(seconds=blocked_for)
                ).isoformat(),
                "detected_after_seconds": round(blocked_for, 3),
                "stack": _freeze_test_stack(loop_thread_id),
                "relay": _freeze_test_relay_snapshot(),
                "_started_monotonic": last_tick,
            }

            with freeze_test_lock:
                if (
                    freeze_test_state["active"]
                    and freeze_test_state["current_event"] is None
                ):
                    freeze_test_state["current_event"] = event

        elif blocked_for < FREEZE_TEST_BLOCK_THRESHOLD:
            with freeze_test_lock:
                if freeze_test_state["current_event"] is not None:
                    _freeze_test_finish_current_locked(now_monotonic)


def _freeze_test_public_state():
    now_monotonic = time.monotonic()

    with freeze_test_lock:
        active = bool(freeze_test_state["active"])
        started_at = freeze_test_state["started_at"]
        finished_at = freeze_test_state["finished_at"]
        ends_monotonic = float(freeze_test_state["ends_monotonic"] or 0.0)
        last_tick = float(freeze_test_state["last_tick_monotonic"] or now_monotonic)
        events = [dict(event) for event in freeze_test_state["events"]]
        current = freeze_test_state["current_event"]

    for event in events:
        event.pop("_started_monotonic", None)

    if current is not None:
        current = dict(current)
        current["duration_seconds"] = round(
            max(
                0.0,
                now_monotonic - float(current.pop("_started_monotonic")),
            ),
            3,
        )

    return {
        "active": active,
        "duration_seconds": FREEZE_TEST_DURATION,
        "threshold_seconds": FREEZE_TEST_BLOCK_THRESHOLD,
        "started_at": started_at,
        "finished_at": finished_at,
        "seconds_remaining": (
            round(max(0.0, ends_monotonic - now_monotonic), 1)
            if active
            else 0.0
        ),
        "event_loop_lag_seconds": round(
            max(0.0, now_monotonic - last_tick),
            3,
        ),
        "events": events,
        "current_event": current,
        "relay": _freeze_test_relay_snapshot(),
    }



def stop_audio_relay(*, disconnect_clients=False):
    global audio_process, audio_task

    # DJ handovers replace the MP3 encoder, but the public /radio.mp3 HTTP
    # connection must remain open. Home Assistant and other radio clients may
    # not reconnect automatically if they receive EOF during a station switch.
    if disconnect_clients:
        for queue in list(audio_clients):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        audio_clients.clear()

    if audio_process is not None:
        try:
            audio_process.terminate()
            audio_process.wait(timeout=3)
        except Exception:
            try:
                audio_process.kill()
            except Exception:
                pass

    audio_process = None

    if audio_task is not None:
        if not audio_task.done():
            audio_task.cancel()
        audio_task = None


async def stop_audio_relay_async(*, disconnect_clients=False):
    """Async-safe audio relay shutdown without blocking the FastAPI event loop."""
    global audio_process, audio_task

    if disconnect_clients:
        for queue in list(audio_clients):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        audio_clients.clear()

    process = audio_process
    audio_process = None

    if process is not None and process.poll() is None:
        try:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=3)
        except Exception:
            try:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=3)
            except Exception:
                pass

    if audio_task is not None:
        if not audio_task.done():
            audio_task.cancel()
        audio_task = None


def stop_relay(*, disconnect_audio_clients=False):
    global relay_process, relay_username, relay_platform, RELAY_PLAYLIST
    global relay_public_sequence_offset, relay_public_discontinuity_sequence

    stop_audio_relay(disconnect_clients=disconnect_audio_clients)

    if relay_process is not None:
        try:
            relay_process.terminate()
            relay_process.wait(timeout=5)
        except Exception:
            try:
                relay_process.kill()
            except Exception:
                pass

    relay_process = None
    relay_username = None
    relay_platform = None
    RELAY_PLAYLIST = RELAY_DIR / "index.m3u8"
    relay_public_sequence_offset = 0
    relay_public_discontinuity_sequence = None
    retired_relay_outputs.clear()

    if RELAY_DIR.exists():
        for item in RELAY_DIR.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass


async def stop_relay_async(*, disconnect_audio_clients=False):
    """Async-safe shared relay shutdown without blocking HTTP/HLS servicing."""
    global relay_process, relay_username, relay_platform, RELAY_PLAYLIST
    global relay_public_sequence_offset, relay_public_discontinuity_sequence

    await stop_audio_relay_async(disconnect_clients=disconnect_audio_clients)

    process = relay_process
    relay_process = None
    relay_username = None
    relay_platform = None
    RELAY_PLAYLIST = RELAY_DIR / "index.m3u8"
    relay_public_sequence_offset = 0
    relay_public_discontinuity_sequence = None
    retired_relay_outputs.clear()

    if process is not None and process.poll() is None:
        try:
            process.terminate()
            await asyncio.to_thread(process.wait, timeout=5)
        except Exception:
            try:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            except Exception:
                pass

    if RELAY_DIR.exists():
        for item in RELAY_DIR.iterdir():
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            except Exception:
                pass


async def get_live_djs():
    """
    Return only live DJ rows that have been refreshed recently.

    The TikTok monitor normally removes an offline DJ from live_djs.
    If TikTok status checking raises an exception, however, the old
    row can otherwise remain forever. Treat rows older than
    LIVE_STALE_AFTER as stale at the API/relay layer as a safety net.
    """
    conn = get_db()

    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=LIVE_STALE_AFTER)
    ).isoformat()

    rows = conn.execute("""
        SELECT username, name, platform, url, viewers, started_at, updated_at
        FROM live_djs
        WHERE updated_at IS NOT NULL
          AND updated_at >= ?
        ORDER BY viewers DESC, started_at ASC
    """, (cutoff,)).fetchall()

    conn.close()

    return [dict(row) for row in rows]


async def get_tiktok_stream_fallback(username, headers=None, cookies=None):
    """Fetch TikTok streamData directly for streams blocked by TikTokLive."""
    api_url = "https://www.tiktok.com/api-live/user/room/"

    request_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.tiktok.com/",
        "Accept": "application/json, text/plain, */*",
    }

    if headers:
        request_headers.update(headers)

    async with httpx.AsyncClient(
        headers=request_headers,
        cookies=cookies or {},
        timeout=15.0,
        follow_redirects=True,
    ) as http:
        response = await http.get(
            api_url,
            params={
                "aid": 1988,
                "sourceType": 54,
                "uniqueId": username,
            },
        )

    response.raise_for_status()
    payload = response.json()

    room = (payload.get("data") or {}).get("liveRoom") or {}
    if not room:
        raise RuntimeError(
            f"TikTok fallback: no liveRoom returned for @{username}"
        )

    stream_data = room.get("streamData")
    if not stream_data:
        raise RuntimeError(
            f"TikTok fallback: no streamData returned for @{username}"
        )

    # The direct TikTok API can return streamData in two forms:
    # 1. {"data": {...}}
    # 2. {"pull_data": {"stream_data": "{...}"}}
    if isinstance(stream_data, str):
        stream_data = json.loads(stream_data)

    if not isinstance(stream_data, dict):
        raise RuntimeError(
            f"TikTok fallback: unexpected streamData format for @{username}"
        )

    # Current API format: streamData -> pull_data -> stream_data (JSON string).
    pull_data = stream_data.get("pull_data") or {}
    nested_stream_data = pull_data.get("stream_data")

    if nested_stream_data:
        if isinstance(nested_stream_data, str):
            nested_stream_data = json.loads(nested_stream_data)
        stream_data = nested_stream_data

    data = (stream_data or {}).get("data") or {}

    if not isinstance(data, dict):
        raise RuntimeError(
            f"TikTok fallback: no usable stream data for @{username}"
        )

    # Prefer HTTP-FLV, matching the existing relay architecture.
    for quality in ("hd", "sd", "ld", "ao", "uhd"):
        main = data.get(quality, {}).get("main", {})
        flv = main.get("flv")
        if flv:
            print(
                f"[TikTok] Direct fallback found {quality.upper()} FLV "
                f"for @{username}"
            )
            return {
                "url": flv,
                "type": "flv",
                "headers": request_headers,
                "cookies": cookies or {},
            }

    # Fall back to HLS.
    for quality in ("hd", "sd", "ld", "ao", "uhd"):
        main = data.get(quality, {}).get("main", {})
        hls = main.get("hls")
        if hls:
            print(
                f"[TikTok] Direct fallback found {quality.upper()} HLS "
                f"for @{username}"
            )
            return {
                "url": hls,
                "type": "hls",
                "headers": request_headers,
                "cookies": cookies or {},
            }

    raise RuntimeError(
        f"TikTok fallback: no usable FLV or HLS URL for @{username}"
    )


def _run_tiktok_fetch_room_info(username, context="stream"):
    """
    Run TikTokLive room-info resolution on a private asyncio loop.

    TikTokLive's web client can occasionally block inside an async call. Keep
    that work off the Uvicorn/FastAPI event loop so HTTP/HLS requests continue
    to be serviced while TikTok responds.
    """
    async def _fetch():
        client = TikTokLiveClient(unique_id=f"@{username}")

        session_file = Path("/data/tiktok_session.json")
        if session_file.exists():
            try:
                session = json.loads(session_file.read_text())
                session_id = session.get("sessionid")
                tt_target_idc = session.get("tt-target-idc")
                if session_id:
                    client.web.set_session(session_id, tt_target_idc or None)
                    if context == "profile":
                        print(
                            f"[TikTok] Using authenticated session "
                            f"for profile refresh @{username}"
                        )
                    else:
                        print("[TikTok] Using authenticated session")
            except Exception as e:
                if context == "profile":
                    print(
                        f"[TikTok] Profile session load warning "
                        f"@{username}: {e}"
                    )
                else:
                    print(f"[TikTok] Session load warning: {e}")

        try:
            room_info = await client.web.fetch_room_info(
                unique_id=username
            )
            return (
                room_info,
                dict(client.web.headers),
                dict(client.web.cookies),
            )
        except Exception as error:
            # Preserve the web client state for the age-restricted direct API
            # fallback without sharing the TikTokLive client across event loops.
            setattr(
                error,
                "_rrr_tiktok_headers",
                dict(client.web.headers),
            )
            setattr(
                error,
                "_rrr_tiktok_cookies",
                dict(client.web.cookies),
            )
            raise

    return asyncio.run(_fetch())


async def get_tiktok_stream(username):
    try:
        room_info, tiktok_headers, tiktok_cookies = await asyncio.to_thread(
            _run_tiktok_fetch_room_info,
            username,
            "stream",
        )
    except Exception as e:
        if "Age restricted stream" not in str(e):
            raise
        print(f"[TikTok] Age-restricted stream detected for @{username} - using direct API fallback")
        return await get_tiktok_stream_fallback(
            username,
            headers=getattr(e, "_rrr_tiktok_headers", {}),
            cookies=getattr(e, "_rrr_tiktok_cookies", {}),
        )

    stream_url = room_info.get("stream_url", {})
    core = stream_url.get("live_core_sdk_data", {})
    pull_data = core.get("pull_data", {})
    stream_data = pull_data.get("stream_data")

    if not stream_data:
        raise RuntimeError(
            "TikTok did not return stream_data"
        )

    data = json.loads(stream_data).get("data", {})

    headers = tiktok_headers
    cookies = tiktok_cookies

    # Prefer HTTP-FLV. TikTok's HLS CDN has been returning 504
    # when requested by FFmpeg from this server.
    for quality in ("hd", "sd", "ld", "ao", "uhd"):
        main = data.get(quality, {}).get("main", {})
        flv = main.get("flv")

        if flv:
            return {
                "url": flv,
                "type": "flv",
                "headers": headers,
                "cookies": cookies,
            }

    # Fall back to HLS if no FLV URL is supplied.
    for quality in ("hd", "sd", "ld", "ao", "uhd"):
        main = data.get(quality, {}).get("main", {})
        hls = main.get("hls")

        if hls:
            return {
                "url": hls,
                "type": "hls",
                "headers": headers,
                "cookies": cookies,
            }

    # Older TikTok response formats.
    flv_map = stream_url.get("flv_pull_url", {})

    if isinstance(flv_map, dict):
        for quality in ("FULL_HD1", "HD1", "SD1", "SD2"):
            flv = flv_map.get(quality)

            if flv:
                return {
                    "url": flv,
                    "type": "flv",
                    "headers": headers,
                    "cookies": cookies,
                }

    raise RuntimeError(
        "TikTok did not return a usable FLV or HLS stream URL"
    )


async def get_twitch_stream(username):
    """Resolve one live Twitch channel to an HLS URL using Streamlink."""
    username = str(username or "").strip().lstrip("@").lower()

    if not username or not re.fullmatch(r"[A-Za-z0-9_]{2,25}", username):
        raise ValueError("Invalid Twitch username")

    channel_url = f"https://www.twitch.tv/{username}"

    def resolve():
        return subprocess.run(
            [
                "streamlink",
                "--no-config",
                "--stream-url",
                channel_url,
                "best",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=25,
            check=False,
        )

    try:
        result = await asyncio.to_thread(resolve)
    except FileNotFoundError:
        raise RuntimeError(
            "Streamlink is not installed in the radiorouter container"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Twitch stream resolution timed out for @{username}"
        )

    stream_url = str(result.stdout or "").strip().splitlines()
    stream_url = stream_url[-1].strip() if stream_url else ""

    if result.returncode != 0 or not stream_url.startswith(("http://", "https://")):
        detail = str(result.stderr or "").strip()
        if not detail:
            detail = "Streamlink did not return a usable stream URL"
        raise RuntimeError(
            f"Unable to resolve Twitch stream for @{username}: {detail}"
        )

    print(f"[Twitch] Streamlink resolved @{username} to HLS")

    return {
        "url": stream_url,
        "type": "hls",
        "headers": {},
        "cookies": {},
    }


def _run_ytdlp(args, timeout=YOUTUBE_YTDLP_TIMEOUT):
    """Run yt-dlp off the FastAPI event loop and return the completed process."""
    try:
        return subprocess.run(
            ["yt-dlp", "--no-config", "--no-warnings", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "yt-dlp is not installed in the radiorouter container"
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("YouTube yt-dlp request timed out")


def _youtube_no_live_error(detail):
    message = str(detail or "").casefold()
    return any(fragment in message for fragment in (
        "no live videos",
        "is not currently live",
        "not currently live",
        "this live event will begin",
        "premieres in",
    ))


async def get_youtube_live_info(url):
    """Return metadata for the current YouTube LIVE video, or None if offline."""
    target = str(url or "").strip()
    if not target:
        raise ValueError("YouTube LIVE URL is required")

    result = await asyncio.to_thread(
        _run_ytdlp,
        [
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            target,
        ],
    )

    if result.returncode != 0:
        detail = str(result.stderr or result.stdout or "").strip()
        if _youtube_no_live_error(detail):
            return None
        raise RuntimeError(detail or "yt-dlp could not read the YouTube LIVE page")

    try:
        info = json.loads(str(result.stdout or "").strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("yt-dlp returned invalid YouTube metadata") from error

    live_status = str(info.get("live_status") or "").casefold()
    if live_status and live_status != "is_live":
        return None
    if info.get("is_live") is False:
        return None

    return info


async def resolve_youtube_account(account):
    """Resolve a pasted YouTube video URL back to its permanent channel identity."""
    if not account.get("needs_resolve"):
        return account

    source_url = str(account.get("source_url") or "").strip()
    result = await asyncio.to_thread(
        _run_ytdlp,
        [
            "--dump-single-json",
            "--skip-download",
            "--no-playlist",
            source_url,
        ],
    )

    if result.returncode != 0:
        detail = str(result.stderr or result.stdout or "").strip()
        raise RuntimeError(
            detail or "Could not resolve that YouTube video to a channel"
        )

    try:
        info = json.loads(str(result.stdout or "").strip())
    except json.JSONDecodeError as error:
        raise RuntimeError("yt-dlp returned invalid YouTube metadata") from error

    uploader_id = str(info.get("uploader_id") or "").strip().lstrip("@")
    channel_id = str(info.get("channel_id") or "").strip()
    username = uploader_id or channel_id
    if not username:
        raise RuntimeError("YouTube did not return a channel identity")

    channel_url = str(info.get("channel_url") or "").strip()
    if not channel_url:
        if uploader_id:
            channel_url = f"https://www.youtube.com/@{uploader_id}"
        else:
            channel_url = f"https://www.youtube.com/channel/{channel_id}"

    return {
        "platform": "YouTube",
        "username": username,
        "name": str(info.get("channel") or info.get("uploader") or username).strip(),
        "profile_url": channel_url.rstrip("/"),
        "live_url": channel_url.rstrip("/") + "/live",
    }


async def get_youtube_stream(username):
    """Resolve one live YouTube channel to a direct media URL using yt-dlp."""
    username = str(username or "").strip().lstrip("@")
    if not username:
        raise ValueError("Invalid YouTube channel identity")

    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT url
            FROM live_djs
            WHERE platform = 'YouTube'
              AND lower(username) = lower(?)
            LIMIT 1
            """,
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if not row or not str(row["url"] or "").strip():
        raise RuntimeError(f"YouTube @{username} is no longer live")

    watch_url = str(row["url"]).strip()
    result = await asyncio.to_thread(
        _run_ytdlp,
        [
            "--no-playlist",
            "--get-url",
            "--format",
            (
                "bestvideo[protocol^=m3u8]+bestaudio[protocol^=m3u8]/"
                "bestvideo+bestaudio"
            ),
            watch_url,
        ],
    )

    urls = [
        line.strip()
        for line in str(result.stdout or "").splitlines()
        if line.strip().startswith(("http://", "https://"))
    ]

    if result.returncode != 0 or len(urls) < 2:
        detail = str(result.stderr or "").strip()
        raise RuntimeError(
            f"Unable to resolve YouTube stream for @{username}: "
            f"{detail or 'yt-dlp did not return separate video and audio streams'}"
        )

    video_url, audio_url = urls[0], urls[1]

    print(f"[YouTube] yt-dlp resolved @{username} to split video/audio HLS")

    return {
        "url": video_url,
        "audio_url": audio_url,
        "type": "youtube_split",
        "headers": {},
        "cookies": {},
    }


async def get_platform_stream(username, platform):
    """Resolve a live DJ stream using the platform-specific adapter."""
    platform_key = str(platform or "").strip().casefold()

    if platform_key == "tiktok":
        return await get_tiktok_stream(username)

    if platform_key == "twitch":
        return await get_twitch_stream(username)

    if platform_key == "youtube":
        return await get_youtube_stream(username)

    raise ValueError(f"Unsupported DJ platform: {platform}")


async def audio_broadcast_loop(process):
    """Read encoded MP3 bytes and fan them out to connected listeners."""
    global audio_process

    try:
        while process.poll() is None:
            chunk = await asyncio.to_thread(
                process.stdout.read,
                AUDIO_CHUNK_SIZE,
            )

            if not chunk:
                break

            async with audio_clients_lock:
                clients = list(audio_clients)

            for queue in clients:
                try:
                    queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    # A slow client should not stall the station for everyone.
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                    try:
                        queue.put_nowait(chunk)
                    except asyncio.QueueFull:
                        pass

    except asyncio.CancelledError:
        raise
    except Exception as error:
        print(f"[Audio] Broadcast error: {error}")
    finally:
        # Do not send EOF to /radio.mp3 listeners when an encoder ends. The
        # relay monitor may immediately replace it during a DJ handover, and
        # keeping each client queue alive makes the station connection survive
        # that encoder replacement.
        if audio_process is process:
            audio_process = None


def start_audio_relay():
    """Start one shared MP3 encoder from the existing HLS relay."""
    global audio_process, audio_task

    if audio_process is not None and audio_process.poll() is None:
        return

    if not RELAY_PLAYLIST.exists():
        print("[Audio] HLS playlist not ready; audio relay not started")
        return

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-re",
        "-i", str(RELAY_PLAYLIST),
        "-vn",
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        "-f", "mp3",
        "pipe:1",
    ]

    audio_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=0,
    )

    audio_task = asyncio.create_task(
        audio_broadcast_loop(audio_process)
    )

    print("[Audio] MP3 relay started at 128 kbps")


async def wait_for_relay_ready(
    process,
    playlist,
    relay_dir,
    timeout=15,
    min_segments=1,
):
    """
    Wait until FFmpeg has produced a usable HLS playlist and at least
    one of the segments referenced by that playlist.

    A TikTok stream URL is not considered usable until FFmpeg has
    successfully opened it and generated actual local HLS output.
    """
    deadline = asyncio.get_running_loop().time() + timeout

    while asyncio.get_running_loop().time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"FFmpeg exited with code {process.returncode}"
            )

        if playlist.exists() and playlist.stat().st_size > 0:
            try:
                playlist_text = playlist.read_text()
            except OSError:
                playlist_text = ""

            segment_names = [
                line.strip()
                for line in playlist_text.splitlines()
                if line.strip()
                and not line.startswith("#")
                and line.strip().endswith(".ts")
            ]

            valid_segments = []

            for name in segment_names:
                target = relay_dir / Path(name).name

                if (
                    target.exists()
                    and target.is_file()
                    and target.stat().st_size > 0
                ):
                    valid_segments.append(target)

            if len(valid_segments) >= min_segments:
                print(
                    f"[Relay] HLS relay ready "
                    f"({len(valid_segments)} segment(s))"
                )
                return True

        await asyncio.sleep(0.5)

    raise RuntimeError(
        f"FFmpeg did not produce a usable HLS relay within "
        f"{timeout}s"
    )


def hls_playlist_window(playlist):
    """Return (media_sequence, segment_names) for a local HLS playlist."""
    try:
        playlist_text = playlist.read_text()
    except OSError:
        return 0, []

    media_sequence = 0
    segment_names = []

    for raw_line in playlist_text.splitlines():
        line = raw_line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                media_sequence = int(line.split(":", 1)[1].strip())
            except (TypeError, ValueError):
                media_sequence = 0
        elif (
            line
            and not line.startswith("#")
            and line.endswith(".ts")
        ):
            segment_names.append(line)

    return media_sequence, segment_names


def prepare_public_hls_timeline(previous_playlist, new_playlist):
    """Keep the public HLS media sequence continuous across a DJ switch."""
    global relay_public_sequence_offset, relay_public_discontinuity_sequence

    new_source_sequence, _ = hls_playlist_window(new_playlist)

    previous_next_sequence = None
    if previous_playlist is not None and previous_playlist.exists():
        previous_source_sequence, previous_segments = hls_playlist_window(
            previous_playlist
        )
        if previous_segments:
            previous_next_sequence = (
                previous_source_sequence
                + relay_public_sequence_offset
                + len(previous_segments)
            )

    if previous_next_sequence is None:
        relay_public_sequence_offset = 0
        relay_public_discontinuity_sequence = None
        return

    relay_public_sequence_offset = (
        previous_next_sequence - new_source_sequence
    )
    relay_public_discontinuity_sequence = previous_next_sequence

    print(
        "[Relay] Public HLS timeline continued at media sequence "
        f"{previous_next_sequence} with discontinuity"
    )


def rewrite_public_hls_playlist(playlist_text, playlist_prefix):
    """Expose the active relay as one continuous station-wide HLS timeline."""
    source_sequence = 0
    lines_in = playlist_text.splitlines()

    for raw_line in lines_in:
        line = raw_line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            try:
                source_sequence = int(line.split(":", 1)[1].strip())
            except (TypeError, ValueError):
                source_sequence = 0
            break

    public_sequence = source_sequence + relay_public_sequence_offset
    boundary = relay_public_discontinuity_sequence
    segment_index = 0
    lines_out = []

    for raw_line in lines_in:
        line = raw_line

        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            line = f"#EXT-X-MEDIA-SEQUENCE:{public_sequence}"

        if line.startswith("#EXTINF:"):
            segment_sequence = public_sequence + segment_index
            if boundary is not None and segment_sequence == boundary:
                lines_out.append("#EXT-X-DISCONTINUITY")

        if line.endswith(".ts") and not line.startswith("/"):
            line = playlist_prefix + line
            segment_index += 1

        lines_out.append(line)

    return "\n".join(lines_out) + "\n"


def relay_output_is_healthy(
    playlist,
    relay_dir,
    max_age=20,
):
    """
    Check that the active HLS relay is still producing fresh output.

    This catches the case where FFmpeg remains alive but the upstream
    TikTok connection has stalled or died.
    """
    if not playlist.exists():
        return False

    try:
        if playlist.stat().st_size <= 0:
            return False

        playlist_text = playlist.read_text()

        segment_names = [
            line.strip()
            for line in playlist_text.splitlines()
            if line.strip()
            and not line.startswith("#")
            and line.strip().endswith(".ts")
        ]

        if not segment_names:
            return False

        latest = None

        for name in segment_names:
            target = relay_dir / Path(name).name

            if (
                target.exists()
                and target.is_file()
                and target.stat().st_size > 0
            ):
                mtime = target.stat().st_mtime
                latest = mtime if latest is None else max(latest, mtime)

        if latest is None:
            return False

        return (
            datetime.now().timestamp() - latest
        ) <= max_age

    except OSError:
        return False


def start_relay(stream_info, username, *, isolated=False):
    global relay_process, relay_username

    stream_url = stream_info["url"]
    stream_type = stream_info.get("type", "unknown")
    headers = stream_info.get("headers", {})
    cookies = stream_info.get("cookies", {})

    # An automatic candidate must not stop or overwrite the on-air relay.
    # Non-isolated callers stop the previous relay asynchronously before this
    # function so process.wait() never blocks the FastAPI event loop.
    prefix = "candidate_" + uuid4().hex if isolated else ""
    playlist = RELAY_DIR / (prefix + ".m3u8") if isolated else RELAY_PLAYLIST
    segment_pattern = prefix + "_segment_%05d.ts" if isolated else "segment_%05d.ts"

    RELAY_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    header_lines = []

    for key, value in headers.items():
        if key.lower() in (
            "connection",
            "content-length",
            "host",
        ):
            continue

        header_lines.append(
            f"{key}: {value}"
        )

    if cookies:
        cookie_string = "; ".join(
            f"{key}={value}"
            for key, value in cookies.items()
        )

        header_lines.append(
            f"Cookie: {cookie_string}"
        )

    http_headers = (
        "\\r\\n".join(header_lines)
        + "\\r\\n"
    )

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_delay_max", "5",
        "-rw_timeout", "15000000",
    ]

    if stream_type == "youtube_split":
        cmd.extend([
            "-i", stream_url,
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "5",
            "-rw_timeout", "15000000",
            "-i", stream_info["audio_url"],
            "-map", "0:v:0",
            "-map", "1:a:0",
        ])
    else:
        cmd.extend([
            "-headers", http_headers,
            "-i", stream_url,
        ])

    cmd.extend([
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "30",
        # Keep recently unreferenced public segments on disk for roughly
        # two minutes. Browsers can still hold an older playlist while
        # FFmpeg advances the live window, so deleting the first expired
        # segment immediately can turn a normal HLS lag into a 404/500 stall.
        "-hls_delete_threshold", "60",
        "-hls_flags",
        "delete_segments+append_list+independent_segments",
        "-hls_segment_filename",
        str(RELAY_DIR / segment_pattern),
        str(playlist),
    ])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=None,
    )

    print(
        f"[Relay] FFmpeg started for "
        f"@{username} ({stream_type})"
    )

    # Do not mark the DJ as the active relay until the caller has
    # successfully validated the generated HLS output.
    if not isolated:
        relay_process = process
        relay_username = None

    return process, playlist


def terminate_relay_process(process):
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        process.kill()
        process.wait(timeout=5)


async def terminate_relay_process_async(process):
    """Terminate FFmpeg without blocking the asyncio event loop."""
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        await asyncio.to_thread(process.wait, timeout=5)
    except Exception:
        process.kill()
        await asyncio.to_thread(process.wait, timeout=5)


def relay_output_files(playlist):
    # Every candidate has a unique prefix, so cleanup cannot delete another
    # candidate's output or the active relay's segments.
    pattern = (playlist.stem + "_segment_*.ts"
               if playlist.stem.startswith("candidate_") else "segment_*.ts")
    return [playlist, Path(str(playlist) + ".tmp"), *playlist.parent.glob(pattern)]


def remove_relay_files(paths):
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            print(f"[Relay] Could not remove retired output {path.name}: {error}")


def cleanup_retired_relay_outputs():
    now = datetime.now(timezone.utc)
    for entry in list(retired_relay_outputs):
        deadline, paths = entry
        if now >= deadline:
            remove_relay_files(paths)
            retired_relay_outputs.remove(entry)


async def validate_and_promote_relay(stream_info, username, platform="TikTok"):
    """Keep the broadcast intact until an isolated candidate produces stable HLS."""
    global relay_process, relay_username, relay_platform, RELAY_PLAYLIST
    process = None
    playlist = None
    promoted = False
    try:
        process, playlist = start_relay(stream_info, username, isolated=True)

        # Do not expose a just-started internet DJ to listeners. TikTok/Twitch
        # feeds can produce one or two segments quickly and then pause while the
        # upstream connection settles. Require a deeper HLS warm-up so the
        # public player has enough media to ride through that startup jitter.
        await wait_for_relay_ready(
            process,
            playlist,
            RELAY_DIR,
            timeout=30,
            min_segments=8,
        )

        # Re-check shortly after the 8-segment threshold so a bursty candidate
        # that immediately stalls is rejected before promotion.
        first_sequence, first_segments = hls_playlist_window(playlist)
        await asyncio.sleep(2.5)
        second_sequence, second_segments = hls_playlist_window(playlist)

        first_tail = (
            first_sequence + len(first_segments) - 1
            if first_segments else None
        )
        second_tail = (
            second_sequence + len(second_segments) - 1
            if second_segments else None
        )

        if (
            process.poll() is not None
            or not relay_output_is_healthy(playlist, RELAY_DIR)
            or first_tail is None
            or second_tail is None
            or second_tail <= first_tail
        ):
            raise RuntimeError(
                "Candidate stopped or failed to advance healthy HLS before promotion"
            )

        previous_process = relay_process
        previous_playlist = RELAY_PLAYLIST
        await stop_audio_relay_async()
        prepare_public_hls_timeline(previous_playlist, playlist)
        # No await between these assignments: requests see one complete relay.
        relay_process = process
        relay_username = username
        relay_platform = str(platform or "TikTok").strip()
        RELAY_PLAYLIST = playlist
        promoted = True

        # The validated FFmpeg keeps running; do not reconnect to TikTok.
        try:
            await terminate_relay_process_async(previous_process)
        except Exception as error:
            print(f"[Relay] Previous FFmpeg cleanup failed: {error}")
        else:
            retired_relay_outputs.append((
                datetime.now(timezone.utc) + timedelta(seconds=RELAY_OUTPUT_RETENTION),
                relay_output_files(previous_playlist),
            ))
        try:
            start_audio_relay()
        except Exception as error:
            # HLS has already been promoted successfully. The monitor retries
            # the MP3 encoder without treating this DJ as a failed candidate.
            print(f"[Audio] Encoder startup failed after relay switch: {error}")
    finally:
        # Also runs on task cancellation, without touching the active relay.
        if not promoted and process is not None:
            try:
                await terminate_relay_process_async(process)
            except Exception as error:
                print(f"[Relay] Candidate FFmpeg cleanup failed: {error}")
            else:
                if playlist is not None:
                    remove_relay_files(relay_output_files(playlist))


def start_manual_relay(stream_info, username, relay_dir):
    if relay_dir.exists():
        shutil.rmtree(relay_dir, ignore_errors=True)

    relay_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    playlist = relay_dir / "index.m3u8"
    header_lines = []

    for key, value in stream_info.get("headers", {}).items():
        if key.lower() not in (
            "connection",
            "content-length",
            "host",
        ):
            header_lines.append(
                f"{key}: {value}"
            )

    cookies = stream_info.get("cookies", {})

    if cookies:
        header_lines.append(
            "Cookie: "
            + "; ".join(
                f"{key}={value}"
                for key, value in cookies.items()
            )
        )

    http_headers = "\\r\\n".join(header_lines) + "\\r\\n"

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_on_network_error", "1",
        "-reconnect_delay_max", "5",
        "-rw_timeout", "15000000",
    ]

    if stream_info.get("type") == "youtube_split":
        cmd.extend([
            "-i", stream_info["url"],
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "5",
            "-rw_timeout", "15000000",
            "-i", stream_info["audio_url"],
            "-map", "0:v:0",
            "-map", "1:a:0",
        ])
    else:
        cmd.extend([
            "-headers", http_headers,
            "-i", stream_info["url"],
        ])

    cmd.extend([
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "30",
        "-hls_flags",
        "delete_segments+append_list+independent_segments",
        "-hls_segment_filename",
        str(relay_dir / "segment_%05d.ts"),
        str(playlist),
    ])

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=None,
    )

    print(
        f"[Relay] Manual FFmpeg started for @{username}"
    )

    return process, playlist


async def ensure_manual_relay(username, platform="TikTok"):
    username = username.lstrip("@").strip()
    platform = str(platform or "TikTok").strip()
    platform_key = platform.casefold()

    if platform_key not in {"tiktok", "twitch", "youtube"}:
        raise ValueError("Unsupported DJ platform")

    async with manual_relay_lock:
        key = f"{platform_key}:{username.lower()}"

        existing = manual_relays.get(key)

        if existing and existing["process"].poll() is None:
            if relay_output_is_healthy(
                existing["playlist"],
                existing["relay_dir"],
            ):
                existing["last_used"] = datetime.now(timezone.utc)
                return existing

            print(
                f"[Relay] Existing manual {platform} relay for "
                f"@{username} is unhealthy; restarting"
            )

            try:
                existing["process"].terminate()
                await asyncio.to_thread(existing["process"].wait, timeout=3)
            except Exception:
                try:
                    existing["process"].kill()
                except Exception:
                    pass

            shutil.rmtree(
                existing["relay_dir"],
                ignore_errors=True,
            )

            manual_relays.pop(key, None)

        live_dj = next(
            (
                dj for dj in await get_live_djs()
                if str(dj.get("username") or "").lower() == username.lower()
                and str(dj.get("platform") or "").casefold() == platform_key
            ),
            None,
        )

        if not live_dj:
            raise ValueError(
                f"{platform} DJ is no longer live"
            )

        stream_info = await get_platform_stream(username, platform)

        relay_dir = MANUAL_RELAY_DIR / f"{platform_key}-{username.lower()}"

        process, playlist = start_manual_relay(
            stream_info,
            username,
            relay_dir,
        )

        try:
            await wait_for_relay_ready(
                process,
                playlist,
                relay_dir,
                timeout=25,
                min_segments=4,
            )

        except Exception as error:
            print(
                f"[Relay] Manual {platform} relay failed for "
                f"@{username}: {error}"
            )

            try:
                process.terminate()
                await asyncio.to_thread(process.wait, timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

            shutil.rmtree(
                relay_dir,
                ignore_errors=True,
            )

            raise RuntimeError(
                f"Unable to start a working {platform} relay for "
                f"@{username}: {error}"
            )

        relay = {
            "process": process,
            "playlist": playlist,
            "relay_dir": relay_dir,
            "last_used": datetime.now(timezone.utc),
            "platform": platform,
            "username": username,
        }

        manual_relays[key] = relay

        return relay


def stop_manual_relays():
    for relay in manual_relays.values():
        process = relay["process"]
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
    manual_relays.clear()
    if MANUAL_RELAY_DIR.exists():
        shutil.rmtree(MANUAL_RELAY_DIR, ignore_errors=True)


# ---------------------------------------------------------
# RRR PROGRAM SCHEDULE — SINGLE SOURCE FOR MATCHING
# ---------------------------------------------------------
# The public index.html contains the station schedule used by the website.
# The matcher reads the same schedule so programming changes do not have
# to be duplicated here in main.py.
#
# Schedule rows store genre names plus optional per-genre weights. Existing
# rows without explicit weights remain backward-compatible and are weighted
# equally by get_schedule_targets().
SCHEDULE_URL = os.getenv(
    "SCHEDULE_URL",
    "https://radiorrr.com/",
).rstrip("/")

SCHEDULE_CACHE_SECONDS = int(
    os.getenv("SCHEDULE_CACHE_SECONDS", "300")
)

schedule_cache = {
    "fetched_at": None,
    "blocks": None,
}
schedule_cache_lock = asyncio.Lock()


# Safe fallback used only if index.html cannot be fetched or parsed.
# This keeps the matcher operational during a temporary website outage.
FALLBACK_SCHEDULE = {
    "weekday": {
        (0, 4): [
            "Chill",
            "Deep House",
            "Melodic",
            "Progressive",
        ],
        (4, 8): [
            "Chill",
            "Downtempo",
            "Ambient",
            "Deep House",
        ],
        (8, 12): [
            "80s",
            "Synthwave",
            "Chill Electro",
            "Nu-Disco",
        ],
        (12, 16): [
            "House",
            "Deep House",
            "Progressive",
            "Funky House",
        ],
        (16, 20): [
            "House",
            "Tech House",
            "Trance",
            "Progressive",
        ],
        (20, 24): [
            "Electro",
            "Techno",
            "Trance",
            "Progressive",
        ],
    },
    "fridaySaturday": {
        (0, 4): [
            "Techno",
            "Hard Techno",
            "Trance",
            "Psy-Trance",
        ],
        (4, 8): [
            "Techno",
            "Trance",
            "Progressive",
            "Psy-Trance",
        ],
        (8, 12): [
            "Chill",
            "House",
            "Progressive",
            "Melodic",
        ],
        (12, 16): [
            "House",
            "Tech House",
            "Progressive",
            "Electro",
        ],
        (16, 20): [
            "Tech House",
            "Techno",
            "Trance",
            "Progressive",
        ],
        (20, 24): [
            "Techno",
            "Trance",
            "BASSLINE",
            "Drum & Bass",
        ],
    },
    "sunday": {
        (0, 4): [
            "Techno",
            "Trance",
            "Progressive",
            "Psy-Trance",
        ],
        (4, 8): [
            "Deep House",
            "Progressive",
            "Melodic",
            "Chill",
        ],
        (8, 12): [
            "Chill",
            "Downtempo",
            "Deep House",
            "Ambient",
        ],
        (12, 16): [
            "House",
            "Deep House",
            "Progressive",
            "Organic House",
        ],
        (16, 20): [
            "Melodic",
            "Progressive",
            "Deep House",
            "Chill",
        ],
        (20, 24): [
            "Chill",
            "Deep House",
            "Progressive",
            "Trance",
        ],
    },
}


def _parse_schedule_array(html, array_name):
    """
    Extract schedule blocks from the JavaScript array in index.html.

    Expected format:
        const weekdayBlocks = [
          {
            start: 0,
            end: 4,
            name: "Early Mornings",
            genres: "Chill · Deep House · ..."
          },
          ...
        ];
    """
    marker = f"const {array_name} = ["
    start = html.find(marker)

    if start < 0:
        raise ValueError(
            f"Schedule array '{array_name}' was not found"
        )

    end = html.find("];", start)

    if end < 0:
        raise ValueError(
            f"Schedule array '{array_name}' has no closing ];"
        )

    section = html[start:end]

    pattern = re.compile(
        r"""
        \{
            \s*start:\s*(?P<start>\d+(?:\.\d+)?)\s*, 
            \s*end:\s*(?P<end>\d+(?:\.\d+)?)\s*, 
            \s*name:\s*"(?P<name>(?:\\.|[^"])*)"\s*,
            \s*genres:\s*"(?P<genres>(?:\\.|[^"])*)"
            \s*\}
        """,
        re.VERBOSE,
    )

    blocks = []

    for match in pattern.finditer(section):
        genres = match.group("genres").replace(
            '\\"',
            '"',
        )

        genre_list = [
            genre.strip()
            for genre in genres.split("·")
            if genre.strip()
        ]

        # Remove accidental duplicate genre labels while preserving order.
        unique_genres = []
        seen = set()

        for genre in genre_list:
            key = genre.casefold()
            if key in seen:
                continue
            seen.add(key)
            unique_genres.append(genre)

        start_hour = float(match.group("start"))
        end_hour = float(match.group("end"))

        if (
            not unique_genres
            or start_hour < 0
            or end_hour <= start_hour
            or end_hour > 24
        ):
            continue

        blocks.append({
            "start": start_hour,
            "end": end_hour,
            "name": match.group("name"),
            "genres": unique_genres,
        })

    if not blocks:
        raise ValueError(
            f"No valid schedule blocks found in '{array_name}'"
        )

    return blocks


def _parse_index_schedule(html):
    """Return all three schedule profiles from index.html."""
    return {
        "weekday": _parse_schedule_array(
            html,
            "weekdayBlocks",
        ),
        "fridaySaturday": _parse_schedule_array(
            html,
            "fridaySaturdayBlocks",
        ),
        "sunday": _parse_schedule_array(
            html,
            "sundayBlocks",
        ),
    }


async def _fetch_index_schedule():
    """Fetch and parse the live public index.html schedule."""
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=True,
        headers={
            "User-Agent": "RadioRRR-Matcher/1.0",
            "Accept": "text/html,application/xhtml+xml",
        },
    ) as client:
        response = await client.get(SCHEDULE_URL)

    response.raise_for_status()

    if not response.text or len(response.text) < 1000:
        raise RuntimeError(
            "index.html response was unexpectedly small"
        )

    return _parse_index_schedule(response.text)


async def get_schedule_blocks():
    """Return the current Radio RRR schedule from SQLite."""
    now = datetime.now(timezone.utc)
    cached_at = schedule_cache.get("fetched_at")
    cached_blocks = schedule_cache.get("blocks")

    if cached_at and cached_blocks and (now - cached_at).total_seconds() < SCHEDULE_CACHE_SECONDS:
        return cached_blocks

    async with schedule_cache_lock:
        cached_at = schedule_cache.get("fetched_at")
        cached_blocks = schedule_cache.get("blocks")
        if cached_at and cached_blocks and (now - cached_at).total_seconds() < SCHEDULE_CACHE_SECONDS:
            return cached_blocks

        try:
            blocks = _db_schedule_profiles()
            if not any(blocks.values()):
                raise RuntimeError("Schedule database is empty")
            schedule_cache["blocks"] = blocks
            schedule_cache["fetched_at"] = now
            print("[Schedule] Loaded schedule from SQLite")
            return blocks
        except Exception as error:
            print(f"[Schedule] Could not load SQLite schedule: {error}")
            if cached_blocks:
                return cached_blocks
            return FALLBACK_SCHEDULE


async def get_active_schedule_block(now=None):
    """Return the active SQLite schedule block for Brisbane time."""
    current = now or datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_now = current.astimezone(ZoneInfo("Australia/Brisbane"))
    except Exception:
        local_now = current

    day_name = SCHEDULE_DAYS[local_now.weekday()]
    blocks = await get_schedule_blocks()

    # The SQLite profile groups Mon-Thu and Fri-Sat because their programming
    # is normally identical, while retaining the per-day schedule in admin.
    if local_now.weekday() == 6:
        profile_name = "sunday"
    elif local_now.weekday() in (4, 5):
        profile_name = "fridaySaturday"
    else:
        profile_name = "weekday"

    if isinstance(blocks.get(profile_name), list):
        hour_value = local_now.hour + local_now.minute / 60
        active = next(
            (
                block for block in blocks[profile_name]
                if hour_value >= block["start"] and hour_value < block["end"]
            ),
            None,
        )
        if active:
            return active

    # Defensive fallback.
    fallback = FALLBACK_SCHEDULE[profile_name]
    current_hour = local_now.hour + local_now.minute / 60
    for (start, end), genres in fallback.items():
        if start <= current_hour < end:
            return {
                "start": start,
                "end": end,
                "name": "Fallback",
                "genres": genres,
                "anti_genres": list(DEFAULT_ANTI_GENRES),
            }
    return None


async def get_schedule_targets(now=None):
    """Return the active programme's target genres and configured weights.

    Existing schedule rows without explicit weights remain backward-compatible:
    their selected genres are weighted equally. Custom weights are normalised to
    100 so the stored values act as relative programming priorities.
    """
    active_block = await get_active_schedule_block(now)
    if not active_block:
        return []

    genres = [
        str(g).strip()
        for g in active_block.get("genres", [])
        if str(g).strip()
    ]
    if not genres:
        return []

    raw_weights = active_block.get("genre_weights") or {}
    if isinstance(raw_weights, dict):
        configured = []
        total = 0.0

        for genre in genres:
            try:
                weight = float(raw_weights.get(genre, 0) or 0)
            except (TypeError, ValueError):
                weight = 0.0

            if weight < 0:
                weight = 0.0

            configured.append((genre, weight))
            total += weight

        if total > 0:
            return [
                (genre, round((weight / total) * 100.0, 4))
                for genre, weight in configured
            ]

    weight = 100.0 / len(genres)
    return [(genre, round(weight, 4)) for genre in genres]


async def get_schedule_anti_genres(now=None):
    """Return the active programme's explicitly avoided genres/content."""
    active_block = await get_active_schedule_block(now)
    if not active_block:
        return list(DEFAULT_ANTI_GENRES)
    anti = [str(g).strip() for g in active_block.get("anti_genres", []) if str(g).strip()]
    return anti or list(DEFAULT_ANTI_GENRES)



def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def ai_freshness_factor(dj, now=None):
    """Return 1.0 for fresh AI, decaying toward 0 for older observations."""
    detected_at = _parse_iso_datetime(
        dj.get("ai_genre_detected_at")
    )
    if detected_at is None:
        return 0.0

    current = now or datetime.now(timezone.utc)
    age = max(
        0.0,
        (current - detected_at).total_seconds(),
    )

    if age <= STAGE3_AI_FRESH_AGE:
        return 1.0

    if age >= STAGE3_AI_DECAY_AGE:
        return 0.0

    span = (
        STAGE3_AI_DECAY_AGE
        - STAGE3_AI_FRESH_AGE
    )

    return max(
        0.0,
        1.0 - (
            (age - STAGE3_AI_FRESH_AGE) / span
        ),
    )


def _normalise_genre_label(value):
    label = str(value or "").strip()
    if "---" in label:
        label = label.split("---", 1)[1].strip()
    return re.sub(r"\s+", " ", label).casefold()


def _is_non_music_label(raw):
    raw_text = str(raw or "").strip().casefold()
    return raw_text.startswith("non-music---") or _normalise_genre_label(raw) in {
        "spoken word", "talk", "talking", "dialogue", "comedy", "audiobook",
        "radioplay", "education", "poetry", "religious", "field recording",
    }



def _speech_evidence(dj):
    """Return speech ratio/confidence embedded in the latest AI payload."""
    values = dj.get("ai_genres") or []
    if not isinstance(values, list):
        return 0.0, 0.0

    for item in values:
        if not isinstance(item, dict):
            continue
        if item.get("analysis") != "speech" and "speech_ratio" not in item:
            continue

        try:
            ratio = float(item.get("speech_ratio") or 0.0)
        except (TypeError, ValueError):
            ratio = 0.0

        try:
            confidence = float(item.get("speech_confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        return (
            max(0.0, min(1.0, ratio)),
            max(0.0, min(1.0, confidence)),
        )

    return 0.0, 0.0


def _speech_penalty(ratio, confidence):
    """Scaled talking penalty with a dead-zone for brief mic use."""
    if ratio <= SPEECH_PENALTY_START:
        return 0.0

    confidence_factor = 0.5 + 0.5 * confidence

    if ratio <= SPEECH_STRONG_START:
        span = SPEECH_STRONG_START - SPEECH_PENALTY_START
        progress = (ratio - SPEECH_PENALTY_START) / span
        return 6.0 * progress * confidence_factor

    span = max(0.001, SPEECH_HARD_EXCLUDE - SPEECH_STRONG_START)
    progress = min(
        1.0,
        (ratio - SPEECH_STRONG_START) / span,
    )

    return (
        6.0
        + (SPEECH_MAX_PENALTY - 6.0) * progress
    ) * confidence_factor

def apply_anti_genre_rules(ranked, enriched_live_djs, anti_genres):
    """Apply per-programme avoid rules to the ranked live-DJ candidates.

    Current live AI is used for exclusion/penalty; learned history is not used
    to ban a DJ because a DJ can legitimately change style between streams.
    """
    avoid_map = {_normalise_genre_label(x): str(x).strip() for x in anti_genres if str(x).strip()}
    if not avoid_map:
        return ranked

    live_lookup = {
        relay_identity_key(
            dj.get("platform") or "TikTok",
            dj.get("username"),
        ): dj
        for dj in enriched_live_djs
        if dj.get("username")
    }

    output = []
    for item in ranked:
        copy_item = dict(item)
        identity = relay_identity_key(
            item.get("platform") or "TikTok",
            item.get("username"),
        )
        dj = live_lookup.get(identity, {})
        detections = dj.get("ai_genres") or []

        avoid_hits = []
        max_penalty = 0.0
        hard_excluded = False

        for detection in detections:
            if not isinstance(detection, dict):
                continue
            raw = detection.get("genre") or ""
            key = _normalise_genre_label(raw)
            if key not in avoid_map:
                continue
            try:
                confidence = float(detection.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence <= 0:
                continue

            avoid_hits.append({
                "genre": avoid_map[key],
                "confidence": round(confidence, 3),
            })

            # Non-music content remains aggressive because it is exactly
            # what should cause a music programme to move on. Music
            # anti-genres are confidence-sensitive: weak secondary labels
            # are diagnostic only, then the penalty ramps up gradually.
            if _is_non_music_label(raw):
                if confidence >= NON_MUSIC_ANTI_CONFIDENCE:
                    hard_excluded = True
                penalty_value = confidence * 100.0
            else:
                if confidence >= ANTI_GENRE_HARD_CONFIDENCE:
                    hard_excluded = True

                if confidence <= ANTI_GENRE_PENALTY_START:
                    penalty_value = 0.0
                else:
                    scaled_confidence = (
                        confidence - ANTI_GENRE_PENALTY_START
                    ) / (
                        1.0 - ANTI_GENRE_PENALTY_START
                    )
                    penalty_value = (
                        scaled_confidence
                        * ANTI_GENRE_MAX_PENALTY
                    )

            max_penalty = max(max_penalty, penalty_value)

        base_score = float(copy_item.get("score") or 0.0)

        speech_ratio, speech_confidence = _speech_evidence(dj)

        if SPEECH_SCORING_ENABLED:
            speech_penalty = _speech_penalty(
                speech_ratio,
                speech_confidence,
            )
            speech_excluded = speech_ratio >= SPEECH_HARD_EXCLUDE
        else:
            # Keep reporting the experimental speech signal in diagnostics,
            # but do not let it influence Stage 3 selection.
            speech_penalty = 0.0
            speech_excluded = False

        copy_item["anti_genre_hits"] = avoid_hits
        copy_item["anti_genre_excluded"] = hard_excluded
        copy_item["speech_ratio"] = round(speech_ratio, 4)
        copy_item["speech_confidence"] = round(speech_confidence, 4)
        copy_item["speech_penalty"] = round(speech_penalty, 2)
        copy_item["speech_excluded"] = speech_excluded
        copy_item["pre_anti_score"] = round(base_score, 2)

        if hard_excluded or speech_excluded:
            # Keep the DJ visible in diagnostics, but make it impossible for
            # Stage 3 to select while the current audio is an avoided genre.
            copy_item["score"] = -1000.0
            copy_item["anti_genre_penalty"] = round(max_penalty, 2)
        else:
            penalty = min(35.0, max_penalty)
            total_penalty = penalty + speech_penalty
            copy_item["score"] = round(
                max(0.0, base_score - total_penalty),
                2,
            )
            copy_item["anti_genre_penalty"] = round(penalty, 2)

        output.append(copy_item)

    output.sort(key=lambda item: (
        -float(item.get("score") or 0),
        -float(item.get("profile_score") or 0),
        -int(item.get("learned_samples") or 0),
        str(item.get("username") or "").casefold(),
    ))
    return output


async def build_genre_match_candidates():
    """
    Build the same enriched DJ set used by /api/genre-match.

    Returns:
        (live_djs, enriched_live_djs, ranked)
    """
    live_djs = [
        dj for dj in await get_live_djs()
        if str(dj.get("platform") or "").casefold() in {"tiktok", "twitch", "youtube"}
    ]

    conn = get_db()
    favourite_rows = conn.execute("""
        SELECT *
        FROM favourite_djs
        WHERE enabled = 1
        ORDER BY name COLLATE NOCASE
    """).fetchall()
    conn.close()

    favourites = [dict(row) for row in favourite_rows]

    favourite_genres = {
        (
            str(favourite.get("platform") or "TikTok").casefold(),
            str(favourite.get("username") or "").lstrip("@").strip().lower(),
        ): favourite
        for favourite in favourites
    }

    ai_genres = {}
    ai_conn = get_db()

    try:
        ai_rows = ai_conn.execute("""
            SELECT platform, username, detected_at, genres_json
            FROM ai_genre_detection
        """).fetchall()

        for row in ai_rows:
            identity = (
                str(row["platform"] or "TikTok").casefold(),
                str(row["username"] or "").lstrip("@").strip().lower(),
            )

            try:
                genres = json.loads(
                    row["genres_json"] or "[]"
                )
            except Exception:
                genres = []

            if isinstance(genres, list):
                ai_genres[identity] = {
                    "detected_at": row["detected_at"],
                    "genres": genres,
                }
    except sqlite3.OperationalError:
        pass
    finally:
        ai_conn.close()

    active_program = await get_active_schedule_block() or {}
    enriched_live_djs = []

    for dj in live_djs:
        enriched = dict(dj)
        enriched["bpm_min"] = active_program.get("bpm_min")
        enriched["bpm_max"] = active_program.get("bpm_max")

        username = (
            str(dj.get("username") or "")
            .lstrip("@")
            .strip()
            .lower()
        )

        identity = (
            str(dj.get("platform") or "TikTok").casefold(),
            username,
        )
        favourite = favourite_genres.get(identity)
        ai = ai_genres.get(identity)

        if favourite:
            for field in ("genre", "genre_keywords"):
                value = favourite.get(field)
                if value is not None and str(value).strip():
                    value = str(value).strip()
                    if field == "genre" and "---" in value:
                        value = value.split("---", 1)[0].strip()
                    enriched[field] = value

        if ai and ai.get("genres"):
            top = ai["genres"][0]

            if isinstance(top, dict):
                raw_genre = str(
                    top.get("genre") or ""
                ).strip()
                confidence = top.get("confidence")

                if raw_genre:
                    if "---" in raw_genre:
                        primary, subgenre = raw_genre.split(
                            "---", 1
                        )
                        enriched["genre"] = primary.strip()
                        enriched["ai_genre"] = subgenre.strip()
                    else:
                        enriched["genre"] = raw_genre
                        enriched["ai_genre"] = raw_genre

                    enriched["ai_genre_raw"] = raw_genre
                    enriched["ai_genres"] = ai["genres"]
                    enriched["ai_genre_confidence"] = confidence
                    enriched["ai_genre_detected_at"] = ai["detected_at"]

        learned = await asyncio.to_thread(
            get_cached_learned_genre_profile,
            username,
            dj.get("platform") or "TikTok",
            30,
            20,
        )

        learned_samples = int(
            learned.get("samples_used", 0) or 0
        )

        if learned_samples >= 20:
            profile_maturity = 1.00
        elif learned_samples >= 10:
            profile_maturity = 0.75
        elif learned_samples >= 5:
            profile_maturity = 0.50
        elif learned_samples >= 3:
            profile_maturity = 0.25
        else:
            profile_maturity = 0.00

        matured_profile = []

        for item in learned.get("genres", []):
            if not isinstance(item, dict):
                continue

            copy_item = dict(item)

            try:
                percentage = float(
                    copy_item.get("percentage") or 0.0
                )
            except (TypeError, ValueError):
                percentage = 0.0

            copy_item["percentage"] = round(
                percentage * profile_maturity,
                4,
            )
            matured_profile.append(copy_item)

        enriched["learned_profile"] = matured_profile
        enriched["learned_observations"] = learned.get(
            "observations_used", 0
        )
        enriched["learned_samples"] = learned_samples
        enriched["profile_maturity"] = profile_maturity

        enriched_live_djs.append(enriched)

    targets = await get_schedule_targets()

    # The matcher historically returned username/name only. Feed it a
    # platform-qualified temporary username so TikTok and Twitch accounts
    # with the same handle remain distinct without changing matcher scoring.
    matcher_identity = {}
    matcher_live_djs = []

    for dj in enriched_live_djs:
        platform = str(dj.get("platform") or "TikTok").strip()
        username = str(dj.get("username") or "").lstrip("@").strip()
        synthetic_username = relay_identity_key(platform, username)

        matcher_dj = dict(dj)
        matcher_dj["username"] = synthetic_username
        matcher_live_djs.append(matcher_dj)
        matcher_identity[synthetic_username] = (platform, username)

    ranked = rank_live_djs(
        matcher_live_djs,
        targets=targets,
    )

    for item in ranked:
        synthetic_username = str(item.get("username") or "")
        platform, username = matcher_identity.get(
            synthetic_username,
            ("TikTok", synthetic_username),
        )
        item["platform"] = platform
        item["username"] = username

    anti_genres = await get_schedule_anti_genres()
    ranked = apply_anti_genre_rules(
        ranked,
        enriched_live_djs,
        anti_genres,
    )

    return (
        live_djs,
        enriched_live_djs,
        ranked,
    )


def _positive_bpm_value(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def stage3_bpm_details(item, dj, freshness):
    low = _positive_bpm_value(dj.get("bpm_min"))
    high = _positive_bpm_value(dj.get("bpm_max"))
    if low is not None and high is not None and low > high:
        low, high = high, low
    enabled = low is not None or high is not None
    bpm = _positive_bpm_value(item.get("bpm"))
    confidence = min(1.0, _positive_bpm_value(item.get("bpm_confidence")) or 0.0)
    reliable = bpm is not None and confidence > 0.0 and freshness > 0.0
    distance = None
    adjustment = 0.0
    if enabled and reliable:
        distance = max(0.0, low - bpm if low is not None else 0.0,
                       bpm - high if high is not None else 0.0)
        # A small miss costs little; large misses cost up to 60 score points.
        # Reuse detector confidence and AI age decay; never reward unknown BPM.
        adjustment = -round(min(60.0, 0.1 * distance ** 2) * confidence * freshness, 2)
    return {
        "bpm": bpm,
        "bpm_min": low,
        "bpm_max": high,
        "bpm_distance": distance,
        "bpm_adjustment": adjustment,
        "bpm_matching_enabled": enabled,
        "bpm_status": "known" if reliable else "unknown",
    }


def stage3_adjusted_ranking(ranked, enriched_live_djs, now=None):
    """One score calculation for candidates, the current relay, and the UI."""
    now = now or datetime.now(timezone.utc)
    live_by_key = {
        relay_identity_key(
            dj.get("platform") or "TikTok",
            dj.get("username"),
        ): dj
        for dj in enriched_live_djs if dj.get("username")
    }
    adjusted_ranked = []
    for item in ranked:
        key = relay_identity_key(
            item.get("platform") or "TikTok",
            item.get("username"),
        )
        dj = live_by_key.get(key)
        if not dj:
            continue
        freshness = ai_freshness_factor(dj, now)
        bpm_details = stage3_bpm_details(item, dj, freshness)
        # Explicit schedule bounds replace the matcher's inferred BPM blend.
        # Without bounds, keep the original score path exactly as it was.
        ai_score = item.get("ai_score")
        if bpm_details["bpm_matching_enabled"]:
            ai_score = item.get("genre_ai_score", ai_score)
        profile_weight = 0.20 + 0.25 * (1.0 - freshness)
        ai_weight = 0.70
        viewer_weight = max(0.0, 1.0 - ai_weight - profile_weight)
        score = round(
            float(ai_score or 0.0) * freshness * ai_weight
            + float(item.get("profile_score") or 0.0) * profile_weight
            + float(item.get("viewer_bonus") or 0.0) * viewer_weight,
            2,
        )
        adjusted = dict(item)
        adjusted.update(bpm_details)
        adjusted["raw_score"] = round(float(item.get("score") or 0.0), 2)
        adjusted["ai_freshness"] = round(freshness, 3)
        adjusted["profile_driven"] = (
            freshness == 0.0 and int(item.get("learned_samples") or 0) >= 3
        )
        if item.get("anti_genre_excluded") or item.get("speech_excluded"):
            adjusted["score"] = -1000.0
        else:
            # Keep poor-but-valid DJs rankable instead of collapsing every
            # out-of-program candidate to 0. BPM and avoid-genre signals
            # reduce the underlying programme match proportionally rather
            # than subtracting enough points to erase it completely.
            bpm_penalty = abs(float(bpm_details["bpm_adjustment"] or 0.0))
            anti_penalty = float(item.get("anti_genre_penalty") or 0.0)
            speech_penalty = float(item.get("speech_penalty") or 0.0)

            bpm_factor = max(0.10, 1.0 - (bpm_penalty / 60.0))
            anti_factor = max(0.10, 1.0 - (anti_penalty / 35.0))
            speech_factor = max(0.10, 1.0 - (speech_penalty / SPEECH_MAX_PENALTY))

            adjusted["score"] = round(
                max(
                    0.0,
                    score
                    * bpm_factor
                    * anti_factor
                    * speech_factor,
                ),
                2,
            )
        adjusted_ranked.append(adjusted)
    adjusted_ranked.sort(key=lambda item: (
        -float(item.get("score") or 0),
        -float(item.get("profile_score") or 0),
        -int(item.get("learned_samples") or 0),
        str(item.get("username") or "").lower(),
    ))
    return adjusted_ranked


def select_stage3_candidate(ranked, enriched_live_djs, failed_until=None, now=None):
    now = now or datetime.now(timezone.utc)
    failed_until = relay_failed_until if failed_until is None else failed_until
    for item in stage3_adjusted_ranking(ranked, enriched_live_djs, now):
        key = relay_identity_key(
            item.get("platform") or "TikTok",
            item.get("username"),
        )
        blocked_until = failed_until.get(key)
        if blocked_until and now < blocked_until:
            continue
        has_evidence = (
            float(item.get("ai_score") or 0) > 0
            or float(item.get("profile_score") or 0) > 0
            or int(item.get("learned_samples") or 0) > 0
        )
        if has_evidence and not item.get("anti_genre_excluded"):
            return item, "eligible"
    return None, "no_eligible_candidate" if ranked else "no_live_djs"


def stage3_switch_decision(
    candidate,
    current_item,
    current_username,
    healthy,
    current_platform="TikTok",
):
    if not candidate:
        return False, "no_eligible_candidate"
    candidate_key = relay_identity_key(
        candidate.get("platform") or "TikTok",
        candidate.get("username"),
    )
    current_key = relay_identity_key(
        current_platform or "TikTok",
        current_username,
    )
    if not healthy:
        return True, "no_healthy_relay"
    if candidate_key == current_key:
        return False, "current_relay_is_best"
    candidate_score = float(candidate.get("score") or 0)
    current_score = float(current_item.get("score") or 0) if current_item else 0.0
    if current_item and (
        current_item.get("anti_genre_excluded")
        or current_item.get("speech_excluded")
    ):
        # An explicit programme exclusion should remove relay protection.
        # A non-excluded live candidate is preferable to staying on avoided
        # content even when that alternative is below the normal score floor.
        return True, "current_relay_anti_genre"
    if current_score < STAGE3_CURRENT_MIN_SCORE:
        # A low current score removes the large continuity margin, but a
        # healthy relay still must not be replaced by another below-floor DJ.
        # This prevents churn such as 0 -> 2.5 -> 7.4 while every candidate
        # remains below the configured minimum acceptable programme score.
        return (
            candidate_score >= STAGE3_MIN_SCORE
            and candidate_score > current_score
        ), "below_quality_floor"
    if candidate_score >= current_score + STAGE3_SWITCH_MARGIN:
        return True, "better_match"
    return False, "current_relay_protected"


async def relay_monitor():
    global relay_process, relay_username, relay_platform

    print(
        "[Relay] Live DJ relay starting..."
    )

    failed_until = relay_failed_until
    failure_cooldown = STAGE3_FAILURE_COOLDOWN

    # If the active DJ temporarily disappears from live_djs, retain a
    # healthy relay instead of immediately stopping it. This is a final
    # defence against transient TikTok status false-negatives or monitor
    # timing gaps.
    relay_missing_since = None
    RELAY_MISSING_GRACE = 5 * 60

    stage3_candidate_identity = None
    stage3_candidate_wins = 0

    while True:
        try:
            async with relay_lock:
                cleanup_retired_relay_outputs()
                live_djs = [
                    dj for dj in await get_live_djs()
                    if str(dj.get("platform") or "").casefold() in {"tiktok", "twitch", "youtube"}
                ]
                now = datetime.now(timezone.utc)

                # Keep the current relay only while it is both running
                # and actually producing fresh HLS output.
                if (
                    relay_process is not None
                    and relay_process.poll() is None
                ):
                    current = next(
                        (
                            dj for dj in live_djs
                            if relay_identity_key(
                                dj.get("platform") or "TikTok",
                                dj.get("username"),
                            ) == relay_identity_key(
                                relay_platform or "TikTok",
                                relay_username,
                            )
                        ),
                        None,
                    )

                    if current is None:
                        if relay_missing_since is None:
                            relay_missing_since = now
                            print(
                                f"[Relay] @{relay_username} temporarily "
                                "missing from live status — retaining "
                                "healthy relay"
                            )

                        missing_for = (
                            now - relay_missing_since
                        ).total_seconds()

                        if (
                            relay_output_is_healthy(
                                RELAY_PLAYLIST,
                                RELAY_DIR,
                            )
                            and missing_for < RELAY_MISSING_GRACE
                        ):
                            print(
                                f"[Relay] @{relay_username} still healthy; "
                                f"holding relay "
                                f"({int(missing_for)}s/"
                                f"{RELAY_MISSING_GRACE}s grace)"
                            )
                        else:
                            print(
                                f"[Relay] @{relay_username} "
                                "offline/missing confirmed — stopping relay"
                            )
                            relay_missing_since = None
                            await stop_relay_async()

                    else:
                        relay_missing_since = None

                    if (
                        relay_process is None
                        or relay_process.poll() is not None
                    ):
                        relay_missing_since = None

                    if relay_process is not None and relay_process.poll() is None and current is not None:

                        # Continue below with the normal health check.
                        pass

                    elif not (
                        relay_process is not None
                        and relay_process.poll() is None
                    ):
                        pass

                    if current is not None and not relay_output_is_healthy(
                        RELAY_PLAYLIST,
                        RELAY_DIR,
                    ):
                        print(
                            f"[Relay] Active relay "
                            f"@{relay_username} is unhealthy"
                        )
                        relay_missing_since = None
                        await stop_relay_async()

                # -----------------------------------------------------
                # STAGE 3: AUTOMATIC BEST-MATCH TAKEOVER
                # -----------------------------------------------------
                # Evaluate the genre matcher even when a healthy DJ is
                # already on air. The previous version only ran Stage 3
                # when the relay was unhealthy, which meant a better
                # matching DJ could never take over a healthy relay.
                relay_is_healthy = (
                    relay_process is not None
                    and relay_process.poll() is None
                    and relay_username is not None
                    and relay_output_is_healthy(
                        RELAY_PLAYLIST,
                        RELAY_DIR,
                    )
                )

                selected = False

                if not live_djs:
                    stage3_candidate_identity = None
                    stage3_candidate_wins = 0
                    if not relay_is_healthy:
                        await stop_relay_async()
                elif STAGE3_ENABLED:
                    try:
                        _, stage3_enriched, stage3_ranked = await build_genre_match_candidates()
                        decision_time = datetime.now(timezone.utc)
                        adjusted_ranked = stage3_adjusted_ranking(
                            stage3_ranked, stage3_enriched, decision_time,
                        )
                        candidate, reason = select_stage3_candidate(
                            stage3_ranked, stage3_enriched, failed_until, decision_time,
                        )
                        current_key = relay_identity_key(
                            relay_platform or "TikTok",
                            relay_username,
                        )
                        current_item = next((
                            item for item in adjusted_ranked
                            if relay_identity_key(
                                item.get("platform") or "TikTok",
                                item.get("username"),
                            ) == current_key
                        ), None)
                        should_switch, reason = stage3_switch_decision(
                            candidate,
                            current_item,
                            relay_username,
                            relay_is_healthy,
                            relay_platform or "TikTok",
                        )

                        # If the current relay is healthy but temporarily absent
                        # from live_djs, preserve it for the configured grace
                        # period. A transient TikTok/Twitch status miss must not
                        # trigger a programme-driven takeover while listeners are
                        # receiving a healthy stream.
                        if (
                            should_switch
                            and relay_is_healthy
                            and relay_missing_since is not None
                            and (decision_time - relay_missing_since).total_seconds()
                            < RELAY_MISSING_GRACE
                        ):
                            should_switch = False
                            reason = "current_relay_missing_but_healthy"

                        if not should_switch:
                            stage3_candidate_identity = None
                            stage3_candidate_wins = 0
                        else:
                            candidate_username = str(candidate["username"]).lstrip("@").strip()
                            candidate_platform = str(
                                candidate.get("platform") or "TikTok"
                            ).strip()
                            candidate_key = relay_identity_key(
                                candidate_platform,
                                candidate_username,
                            )
                            if not relay_is_healthy:
                                # Dead air is worse than a low programme score.
                                # The 3-win confirmation protects a healthy relay
                                # from churn; it must not delay station startup.
                                stage3_candidate_identity = candidate_key
                                stage3_candidate_wins = STAGE3_REQUIRED_WINS
                            elif stage3_candidate_identity == candidate_key:
                                stage3_candidate_wins = min(
                                    STAGE3_REQUIRED_WINS, stage3_candidate_wins + 1,
                                )
                            else:
                                stage3_candidate_identity = candidate_key
                                stage3_candidate_wins = 1
                            print(
                                f"[Stage 3] Candidate {candidate_platform} @{candidate_username} "
                                f"score={candidate['score']:.2f} "
                                f"wins={stage3_candidate_wins}/{STAGE3_REQUIRED_WINS} ({reason})"
                            )
                            if stage3_candidate_wins >= STAGE3_REQUIRED_WINS:
                                try:
                                    print(
                                        f"[Stage 3] Validating {candidate_platform} "
                                        f"@{candidate_username} as new default relay"
                                    )
                                    stream_info = await get_platform_stream(
                                        candidate_username,
                                        candidate_platform,
                                    )
                                    await validate_and_promote_relay(
                                        stream_info,
                                        candidate_username,
                                        candidate_platform,
                                    )
                                    selected = True
                                    relay_missing_since = None
                                    failed_until.pop(candidate_key, None)
                                    print(
                                        f"[Stage 3] Default relay switched to "
                                        f"{candidate_platform} @{candidate_username} "
                                        f"(score={candidate['score']:.2f})"
                                    )
                                except Exception as error:
                                    failed_until[candidate_key] = (
                                        datetime.now(timezone.utc) + timedelta(seconds=failure_cooldown)
                                    )
                                    print(
                                        f"[Stage 3] Candidate {candidate_platform} "
                                        f"@{candidate_username} failed validation: "
                                        f"{error}; skipping for {failure_cooldown}s"
                                    )
                                finally:
                                    stage3_candidate_identity = None
                                    stage3_candidate_wins = 0
                    except Exception as error:
                        stage3_candidate_identity = None
                        stage3_candidate_wins = 0
                        print(f"[Stage 3] Decision error: {error}")

                # Safety fallback: if there is no active healthy relay,
                # find any live DJ whose stream can actually be relayed.
                if not selected and (
                    relay_process is None
                    or relay_process.poll() is not None
                    or not relay_output_is_healthy(
                        RELAY_PLAYLIST,
                        RELAY_DIR,
                    )
                ):
                    for dj in live_djs:
                        username = str(
                            dj.get("username") or ""
                        ).lstrip("@").strip()

                        if not username:
                            continue

                        platform = str(
                            dj.get("platform") or "TikTok"
                        ).strip()
                        key = relay_identity_key(platform, username)
                        blocked_until = failed_until.get(key)

                        if blocked_until and now < blocked_until:
                            continue

                        try:
                            print(
                                f"[Relay] Safety fallback testing "
                                f"{platform} @{username}"
                            )

                            stream_info = await get_platform_stream(
                                username,
                                platform,
                            )

                            await stop_relay_async()
                            process, playlist = start_relay(
                                stream_info,
                                username,
                            )

                            await wait_for_relay_ready(
                                process,
                                playlist,
                                RELAY_DIR,
                                timeout=15,
                            )

                            relay_username = username
                            relay_platform = platform
                            start_audio_relay()

                            print(
                                f"[Relay] Safety fallback validated "
                                f"for {platform} @{username}"
                            )

                            selected = True
                            break

                        except Exception as error:
                            print(
                                f"[Relay] Safety fallback skipped "
                                f"{platform} @{username}: {error}"
                            )

                            failed_until[key] = (
                                datetime.now(timezone.utc)
                                + timedelta(
                                    seconds=failure_cooldown
                                )
                            )

                            await stop_relay_async()

                if not selected and (
                    relay_process is None
                    or relay_process.poll() is not None
                    or not relay_output_is_healthy(
                        RELAY_PLAYLIST,
                        RELAY_DIR,
                    )
                ):
                    print(
                        "[Relay] No working live relays available"
                    )
                    await stop_relay_async()

                if (
                    relay_process is not None
                    and relay_process.poll() is None
                    and relay_username
                    and relay_output_is_healthy(RELAY_PLAYLIST, RELAY_DIR)
                ):
                    start_audio_relay()

            await asyncio.sleep(STAGE3_DECISION_INTERVAL)

        except asyncio.CancelledError:
            raise

        except Exception as error:
            print(
                f"[Relay] MONITOR ERROR: {error}"
            )

            await asyncio.sleep(10)


# Database
# ---------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------
# TikTok DJ profile importer
# ---------------------------------------------------------

# Specific phrases are checked before generic genres to reduce
# false positives (for example, "tech house" should not become
# only "house").
GENRE_PRIORITY = [
    "80s",
    "Electro",
    "Synth",
    "Progressive House",
    "Deep House",
    "House",
    "Techno",
    "Tech House",
    "Trance",
    "Drum & Bass",
    "Hardstyle",
]

# Detection keywords for each RRR genre.
# The list/order above controls priority; these keywords control detection.
GENRE_DETECTION = {
    "80s": [
        "80s",
        "80's",
        "eighties",
        "80s music",
        "80s music",
    ],
    "Electro": [
        "electro",
        "electro music",
    ],
    "Synth": [
        "synth",
        "synthwave",
        "synth wave",
        "synthpop",
        "synth pop",
    ],
    "Progressive House": [
        "progressive house",
        "prog house",
    ],
    "Deep House": [
        "deep house",
    ],
    "House": [
        "house",
    ],
    "Techno": [
        "techno",
    ],
    "Tech House": [
        "tech house",
    ],
    "Trance": [
        "trance",
    ],
    "Drum & Bass": [
        "drum & bass",
        "drum and bass",
        "drum n bass",
        "dnb",
    ],
    "Hardstyle": [
        "hardstyle",
    ],
}



def extract_genres(*texts):
    """Return detected RRR genres in the configured priority order."""
    combined = " ".join(
        str(value or "")
        for value in texts
    ).lower()

    found = []

    for genre in GENRE_PRIORITY:
        keywords = GENRE_DETECTION.get(
            genre,
            [],
        )

        if any(keyword in combined for keyword in keywords):
            found.append(genre)

    return found



def extract_dj_profile(room_info, requested_username):
    """Extract stable profile information from TikTok room_info."""
    owner = room_info.get("owner") or {}
    follow_info = owner.get("follow_info") or {}

    avatar = owner.get("avatar_large") or {}
    avatar_urls = avatar.get("url_list") or []

    username = (
        owner.get("display_id")
        or owner.get("unique_id")
        or requested_username
    )
    username = str(username).lstrip("@").strip()

    name = (
        owner.get("nickname")
        or username
    )

    bio = (
        owner.get("bio_description")
        or ""
    )

    live_title = (
        room_info.get("title")
        or ""
    )

    genres = extract_genres(
        bio,
        live_title,
        name,
    )

    return {
        "username": username,
        "name": str(name),
        "bio": str(bio),
        "followers": follow_info.get("follower_count"),
        "following": follow_info.get("following_count"),
        "profile_pic": (
            avatar_urls[0]
            if avatar_urls
            else None
        ),
        "verified": 1 if owner.get("verified") else 0,
        "tiktok_user_id": str(
            owner.get("id_str")
            or owner.get("id")
            or ""
        ),
        "live_title": str(live_title),
        "genre_keywords": ", ".join(genres),
    }



async def fetch_tiktok_live_api_profile(username):
    """Fetch profile metadata from TikTok's direct LIVE user endpoint.

    This endpoint exposes the creator profile alongside live-room data and
    continues to work for some age-restricted rooms where TikTokLive's
    fetch_room_info() is rejected.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        raise ValueError("TikTok username is required")

    cookies = {}
    session_file = Path("/data/tiktok_session.json")
    if session_file.exists():
        try:
            session = json.loads(session_file.read_text())
            cookies = {
                str(key): str(value)
                for key, value in session.items()
                if value
            }
        except Exception as error:
            print(
                f"[TikTok] Direct profile API session warning "
                f"@{username}: {error}"
            )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.tiktok.com/",
        "Accept": "application/json, text/plain, */*",
    }

    async with httpx.AsyncClient(
        headers=headers,
        cookies=cookies,
        timeout=20.0,
        follow_redirects=True,
    ) as http:
        response = await http.get(
            "https://www.tiktok.com/api-live/user/room/",
            params={
                "aid": 1988,
                "sourceType": 54,
                "uniqueId": username,
            },
        )

    global profile_refresh_rate_limited_until, profile_refresh_403_streak

    if response.status_code == 403:
        profile_refresh_403_streak += 1
        backoff_index = min(
            profile_refresh_403_streak - 1,
            len(PROFILE_REFRESH_403_BACKOFFS) - 1,
        )
        backoff_seconds = PROFILE_REFRESH_403_BACKOFFS[backoff_index]
        profile_refresh_rate_limited_until = max(
            profile_refresh_rate_limited_until,
            time.monotonic() + backoff_seconds,
        )
        print(
            f"[TikTok] Direct profile API rate-limited (403); "
            f"backing off profile refreshes for {backoff_seconds}s"
        )

    response.raise_for_status()
    payload = response.json()

    data = payload.get("data") or {}
    user = data.get("user") or {}

    if not isinstance(user, dict) or not user:
        raise RuntimeError(
            f"TikTok direct profile API: no user returned for @{username}"
        )

    actual_username = str(
        user.get("uniqueId")
        or user.get("unique_id")
        or user.get("display_id")
        or username
    ).lstrip("@").strip()

    name = str(
        user.get("nickname")
        or actual_username
    ).strip()

    bio = str(
        user.get("signature")
        or user.get("bio_description")
        or ""
    )

    profile_pic = str(
        user.get("avatarLarger")
        or user.get("avatarMedium")
        or user.get("avatarThumb")
        or ""
    ).strip()

    stats = data.get("stats") or {}

    try:
        followers = int(
            stats.get("followerCount")
            if stats.get("followerCount") is not None
            else stats.get("follower_count")
        )
    except (TypeError, ValueError):
        followers = None

    try:
        following = int(
            stats.get("followingCount")
            if stats.get("followingCount") is not None
            else stats.get("following_count")
        )
    except (TypeError, ValueError):
        following = None

    live_room = data.get("liveRoom") or {}
    live_title = str(
        live_room.get("title")
        or ""
    )

    genres = extract_genres(
        bio,
        live_title,
        name,
    )

    print(
        f"[TikTok] Direct profile API succeeded for @{username}"
    )

    return {
        "username": actual_username,
        "name": name,
        "bio": bio,
        "followers": followers,
        "following": following,
        "profile_pic": profile_pic or None,
        "verified": 1 if user.get("verified") else 0,
        "tiktok_user_id": str(
            user.get("id")
            or user.get("id_str")
            or ""
        ),
        "live_title": live_title,
        "genre_keywords": ", ".join(genres),
    }


async def fetch_tiktok_profile_fallback(username):
    """Fetch profile metadata from TikTok's public profile HTML.

    This is used only when TikTokLive room-info cannot be read (for example
    some age-restricted LIVE rooms). It does not touch stream playback.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        raise ValueError("TikTok username is required")

    cookies = {}
    session_file = Path("/data/tiktok_session.json")
    if session_file.exists():
        try:
            session = json.loads(session_file.read_text())
            cookies = {
                str(key): str(value)
                for key, value in session.items()
                if value
            }
        except Exception as error:
            print(
                f"[TikTok] Profile fallback session warning "
                f"@{username}: {error}"
            )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.tiktok.com/",
    }

    async with httpx.AsyncClient(
        headers=headers,
        cookies=cookies,
        timeout=20.0,
        follow_redirects=True,
    ) as http:
        response = await http.get(
            f"https://www.tiktok.com/@{username}"
        )

    response.raise_for_status()
    html = response.text or ""

    def script_json(script_id):
        match = re.search(
            rf'<script[^>]+id=["\\\']{re.escape(script_id)}["\\\'][^>]*>(.*?)</script>',
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None
        raw = match.group(1).strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    user = None
    stats = {}

    universal = script_json("__UNIVERSAL_DATA_FOR_REHYDRATION__")
    if isinstance(universal, dict):
        scope = universal.get("__DEFAULT_SCOPE__") or {}
        detail = scope.get("webapp.user-detail") or {}
        user_info = detail.get("userInfo") or {}
        candidate = user_info.get("user")
        if isinstance(candidate, dict):
            user = candidate
            if isinstance(user_info.get("stats"), dict):
                stats = user_info["stats"]

    if user is None:
        sigi = script_json("SIGI_STATE")
        if isinstance(sigi, dict):
            module = sigi.get("UserModule") or {}
            users = module.get("users") or {}
            module_stats = module.get("stats") or {}

            if isinstance(users, dict):
                requested = username.casefold()
                for key, candidate in users.items():
                    if not isinstance(candidate, dict):
                        continue
                    candidate_username = str(
                        candidate.get("uniqueId")
                        or candidate.get("display_id")
                        or ""
                    ).lstrip("@").strip()

                    if candidate_username.casefold() == requested:
                        user = candidate
                        if isinstance(module_stats, dict):
                            candidate_id = str(
                                candidate.get("id")
                                or candidate.get("id_str")
                                or key
                            )
                            found_stats = module_stats.get(candidate_id)
                            if isinstance(found_stats, dict):
                                stats = found_stats
                        break

    if not isinstance(user, dict):
        raise RuntimeError(
            f"TikTok profile fallback: no profile JSON found for @{username}"
        )

    actual_username = str(
        user.get("uniqueId")
        or user.get("display_id")
        or username
    ).lstrip("@").strip()

    name = str(
        user.get("nickname")
        or actual_username
    ).strip()

    bio = str(
        user.get("signature")
        or user.get("bio_description")
        or ""
    )

    profile_pic = str(
        user.get("avatarLarger")
        or user.get("avatarMedium")
        or user.get("avatarThumb")
        or ""
    ).strip()

    try:
        followers = int(
            stats.get("followerCount")
            if stats.get("followerCount") is not None
            else stats.get("follower_count")
        )
    except (TypeError, ValueError):
        followers = None

    try:
        following = int(
            stats.get("followingCount")
            if stats.get("followingCount") is not None
            else stats.get("following_count")
        )
    except (TypeError, ValueError):
        following = None

    genres = extract_genres(
        bio,
        name,
    )

    print(
        f"[TikTok] Profile HTML fallback succeeded for @{username}"
    )

    return {
        "username": actual_username,
        "name": name,
        "bio": bio,
        "followers": followers,
        "following": following,
        "profile_pic": profile_pic or None,
        "verified": 1 if user.get("verified") else 0,
        "tiktok_user_id": str(
            user.get("id")
            or user.get("id_str")
            or ""
        ),
        "live_title": "",
        "genre_keywords": ", ".join(genres),
    }


def ensure_profile_columns(conn):
    """Safely add profile fields to existing favourite_djs tables."""
    columns = {
        "bio": "TEXT",
        "genre_keywords": "TEXT",
        "followers": "INTEGER",
        "following": "INTEGER",
        "profile_pic": "TEXT",
        "verified": "INTEGER DEFAULT 0",
        "tiktok_user_id": "TEXT",
        "live_title": "TEXT",
        "profile_updated_at": "TEXT",
    }

    existing = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(favourite_djs)"
        ).fetchall()
    }

    for name, definition in columns.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE favourite_djs ADD COLUMN {name} {definition}"
            )


async def import_tiktok_dj(username, profile_pic_only=False):
    """Serialize all TikTok profile imports within this process."""
    async with profile_import_lock:
        return await _import_tiktok_dj(
            username,
            profile_pic_only=profile_pic_only,
        )


async def _import_tiktok_dj(username, profile_pic_only=False):
    """Fetch a TikTok profile and upsert it into favourite_djs."""
    username = str(username or "").strip().lstrip("@")

    if not username:
        raise ValueError("TikTok username is required")

    try:
        room_info, _, _ = await asyncio.to_thread(
            _run_tiktok_fetch_room_info,
            username,
            "profile",
        )
        profile = extract_dj_profile(
            room_info,
            username,
        )
    except Exception as room_error:
        print(
            f"[TikTok] Room profile fetch failed @{username}: "
            f"{room_error}; trying direct profile API"
        )

        try:
            profile = await fetch_tiktok_live_api_profile(
                username
            )
        except Exception as direct_error:
            print(
                f"[TikTok] Direct profile API failed @{username}: "
                f"{direct_error}; trying profile HTML fallback"
            )
            try:
                profile = await fetch_tiktok_profile_fallback(
                    username
                )
            except Exception as fallback_error:
                raise RuntimeError(
                    f"{room_error}; direct profile API failed: "
                    f"{direct_error}; profile HTML fallback failed: "
                    f"{fallback_error}"
                ) from fallback_error

    actual_username = profile["username"]

    if not actual_username:
        raise RuntimeError(
            "TikTok did not return a usable username"
        )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    conn = get_db()

    # Ensure this works against the existing database as well as
    # newly-created databases.
    ensure_profile_columns(conn)

    profile_url = (
        f"https://www.tiktok.com/@{actual_username}"
    )
    live_url = (
        f"{profile_url}/live"
    )

    if profile_pic_only:
        profile_pic = str(profile["profile_pic"] or "").strip()

        if profile_pic:
            conn.execute(
                """
                UPDATE favourite_djs
                SET profile_pic = ?
                WHERE lower(username) = lower(?)
                  AND platform = 'TikTok'
                """,
                (profile_pic, username),
            )
            conn.commit()

        conn.close()

        return {
            **profile,
            "profile_url": profile_url,
            "live_url": live_url,
            "status": "ok",
        }

    # Keep the first manual genre value if one already exists.
    existing = conn.execute(
        """
        SELECT genre
        FROM favourite_djs
        WHERE username = ?
          AND platform = 'TikTok'
        """,
        (actual_username,),
    ).fetchone()

    existing_genre = (
        existing["genre"]
        if existing
        else None
    )

    # Use the first automatically detected genre as the primary
    # genre only when no primary genre has already been set manually.
    genre = (
        existing_genre
        or (
            profile["genre_keywords"].split(", ")[0]
            if profile["genre_keywords"]
            else None
        )
    )

    conn.execute(
        """
        INSERT INTO favourite_djs
        (
            username,
            name,
            platform,
            profile_url,
            live_url,
            genre,
            enabled,
            created_at,
            bio,
            genre_keywords,
            followers,
            following,
            profile_pic,
            verified,
            tiktok_user_id,
            live_title,
            profile_updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(platform, username) DO UPDATE SET
            name = excluded.name,
            profile_url = excluded.profile_url,
            live_url = excluded.live_url,
            genre = CASE
                WHEN favourite_djs.genre IS NOT NULL
                     AND TRIM(favourite_djs.genre) != ''
                THEN favourite_djs.genre
                ELSE excluded.genre
            END,
            enabled = 1,
            bio = excluded.bio,
            genre_keywords = excluded.genre_keywords,
            followers = excluded.followers,
            following = excluded.following,
            profile_pic = excluded.profile_pic,
            verified = excluded.verified,
            tiktok_user_id = excluded.tiktok_user_id,
            live_title = excluded.live_title,
            profile_updated_at = excluded.profile_updated_at
        """,
        (
            actual_username,
            profile["name"],
            "TikTok",
            profile_url,
            live_url,
            genre,
            now,
            profile["bio"],
            profile["genre_keywords"],
            profile["followers"],
            profile["following"],
            profile["profile_pic"],
            profile["verified"],
            profile["tiktok_user_id"],
            profile["live_title"],
            now,
        ),
    )

    conn.commit()
    conn.close()

    return {
        **profile,
        "profile_url": profile_url,
        "live_url": live_url,
        "genre": genre,
        "status": "ok",
    }


def profile_refresh_is_due(profile_updated_at, now=None):
    """Return whether a favourite DJ profile is at least 24 hours old."""
    if not profile_updated_at:
        return True

    try:
        updated_at = datetime.fromisoformat(
            str(profile_updated_at).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return True

    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    current_time = now or datetime.now(timezone.utc)
    return current_time - updated_at >= timedelta(
        seconds=PROFILE_REFRESH_INTERVAL
    )


async def refresh_stale_tiktok_profiles():
    """Refresh stale TikTok favourites with pacing and rate-limit backoff."""
    if profile_refresh_job_lock.locked():
        print("[TikTok] DAILY PROFILE REFRESH already running; skipping")
        return

    now_monotonic = time.monotonic()
    if now_monotonic < profile_refresh_rate_limited_until:
        wait_seconds = int(
            profile_refresh_rate_limited_until - now_monotonic
        )
        print(
            f"[TikTok] DAILY PROFILE REFRESH rate-limit cooldown active; "
            f"retrying later ({wait_seconds}s remaining)"
        )
        return

    async with profile_refresh_job_lock:
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT username, profile_updated_at
                FROM favourite_djs
                WHERE enabled = 1
                  AND platform = 'TikTok'
                ORDER BY id ASC
            """).fetchall()
        finally:
            conn.close()

        now = datetime.now(timezone.utc)
        now_monotonic = time.monotonic()
        due_rows = [
            row for row in rows
            if profile_refresh_is_due(
                row["profile_updated_at"],
                now,
            )
            and now_monotonic >= profile_refresh_failed_until.get(
                str(row["username"] or "").strip().lstrip("@"),
                0.0,
            )
        ]

        print(
            f"[TikTok] DAILY PROFILE REFRESH checking "
            f"{len(rows)} DJs; {len(due_rows)} due"
        )

        for row in due_rows:
            username = str(row["username"] or "").strip().lstrip("@")

            if not username:
                print(
                    "[TikTok] DAILY PROFILE REFRESH skipped blank username"
                )
                continue

            if time.monotonic() < profile_refresh_rate_limited_until:
                wait_seconds = int(
                    profile_refresh_rate_limited_until - time.monotonic()
                )
                print(
                    f"[TikTok] DAILY PROFILE REFRESH paused by 403 backoff; "
                    f"remaining DJs will retry later "
                    f"({wait_seconds}s remaining)"
                )
                break

            try:
                await asyncio.wait_for(
                    import_tiktok_dj(username),
                    timeout=PROFILE_REFRESH_TIMEOUT,
                )
                profile_refresh_failed_until.pop(username, None)
                print(
                    f"[TikTok] DAILY PROFILE REFRESHED @{username}"
                )
            except asyncio.TimeoutError:
                profile_refresh_failed_until[username] = (
                    time.monotonic() + PROFILE_REFRESH_FAILURE_COOLDOWN
                )
                print(
                    f"[TikTok] DAILY PROFILE REFRESH TIMEOUT "
                    f"@{username} after {PROFILE_REFRESH_TIMEOUT}s; "
                    f"retry delayed for {PROFILE_REFRESH_FAILURE_COOLDOWN}s"
                )
            except Exception as e:
                profile_refresh_failed_until[username] = (
                    time.monotonic() + PROFILE_REFRESH_FAILURE_COOLDOWN
                )
                print(
                    f"[TikTok] DAILY PROFILE REFRESH ERROR "
                    f"@{username}: {e}; "
                    f"retry delayed for {PROFILE_REFRESH_FAILURE_COOLDOWN}s"
                )

            if time.monotonic() < profile_refresh_rate_limited_until:
                wait_seconds = int(
                    profile_refresh_rate_limited_until - time.monotonic()
                )
                print(
                    f"[TikTok] DAILY PROFILE REFRESH stopping this pass "
                    f"after TikTok 403; remaining DJs will retry later "
                    f"({wait_seconds}s backoff)"
                )
                break

            await asyncio.sleep(
                random.uniform(
                    PROFILE_REFRESH_REQUEST_DELAY_MIN,
                    PROFILE_REFRESH_REQUEST_DELAY_MAX,
                )
            )


async def profile_refresh_monitor():
    """Run the non-blocking daily profile refresh scheduler."""
    print("[TikTok] Daily profile refresh starting...")
    await asyncio.sleep(PROFILE_REFRESH_START_DELAY)

    while True:
        try:
            await refresh_stale_tiktok_profiles()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[TikTok] DAILY PROFILE REFRESH JOB ERROR: {e}")

        await asyncio.sleep(PROFILE_REFRESH_CHECK_INTERVAL)



# ---------------------------------------------------------
# Radio RRR scheduler database
# ---------------------------------------------------------

SCHEDULE_DAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]

DEFAULT_GENRES = [
    "House", "Deep House", "Progressive", "Progressive House", "Funky House",
    "Tech House", "Techno", "Hard Techno", "Trance", "Psy-Trance", "Electro",
    "Chill", "Downtempo", "Ambient", "Melodic", "Melodic House", "Synthwave",
    "Chill Electro", "Nu-Disco", "Organic House", "BASSLINE", "Drum & Bass", "80s"
]

DEFAULT_SCHEDULE = {
    "Monday": [
        ("00:00", "04:00", "Late Nights Insomnia", ["Chill", "Deep House", "Melodic", "Progressive"]),
        ("04:00", "08:00", "Sunrise Sessions", ["Chill", "Downtempo", "Ambient", "Deep House"]),
        ("08:00", "12:00", "Day Drive", ["80s", "Synthwave", "Chill Electro", "Nu-Disco"]),
        ("12:00", "16:00", "Afternoon Beats", ["House", "Deep House", "Progressive", "Funky House"]),
        ("16:00", "20:00", "Dinner Warm ups", ["House", "Tech House", "Trance", "Progressive"]),
        ("20:00", "00:00", "Prime Time", ["Electro", "Techno", "Trance", "Progressive"]),
    ],
    "Tuesday": [
        ("00:00", "04:00", "Late Nights Insomnia", ["Chill", "Deep House", "Melodic", "Progressive"]),
        ("04:00", "08:00", "Sunrise Sessions", ["Chill", "Downtempo", "Ambient", "Deep House"]),
        ("08:00", "12:00", "Day Drive", ["80s", "Synthwave", "Chill Electro", "Nu-Disco"]),
        ("12:00", "16:00", "Afternoon Beats", ["House", "Deep House", "Progressive", "Funky House"]),
        ("16:00", "20:00", "Dinner Warm ups", ["House", "Tech House", "Trance", "Progressive"]),
        ("20:00", "00:00", "Prime Time", ["Electro", "Techno", "Trance", "Progressive"]),
    ],
    "Wednesday": [
        ("00:00", "04:00", "Late Nights Insomnia", ["Chill", "Deep House", "Melodic", "Progressive"]),
        ("04:00", "08:00", "Sunrise Sessions", ["Chill", "Downtempo", "Ambient", "Deep House"]),
        ("08:00", "12:00", "Day Drive", ["80s", "Synthwave", "Chill Electro", "Nu-Disco"]),
        ("12:00", "16:00", "Afternoon Beats", ["House", "Deep House", "Progressive", "Funky House"]),
        ("16:00", "20:00", "Dinner Warm ups", ["House", "Tech House", "Trance", "Progressive"]),
        ("20:00", "00:00", "Prime Time", ["Electro", "Techno", "Trance", "Progressive"]),
    ],
    "Thursday": [
        ("00:00", "04:00", "Late Nights Insomnia", ["Chill", "Deep House", "Melodic", "Progressive"]),
        ("04:00", "08:00", "Sunrise Sessions", ["Chill", "Downtempo", "Ambient", "Deep House"]),
        ("08:00", "12:00", "Day Drive", ["80s", "Synthwave", "Chill Electro", "Nu-Disco"]),
        ("12:00", "16:00", "Afternoon Beats", ["House", "Deep House", "Progressive", "Funky House"]),
        ("16:00", "20:00", "Dinner Warm ups", ["House", "Tech House", "Trance", "Progressive"]),
        ("20:00", "00:00", "Prime Time", ["Electro", "Techno", "Trance", "Progressive"]),
    ],
    "Friday": [
        ("00:00", "04:00", "After Dark", ["Techno", "Hard Techno", "Trance", "Psy-Trance"]),
        ("04:00", "08:00", "Late Mornings", ["Techno", "Trance", "Progressive", "Psy-Trance"]),
        ("08:00", "12:00", "Morning", ["Chill", "House", "Progressive", "Melodic"]),
        ("12:00", "16:00", "Day Party", ["House", "Tech House", "Progressive", "Electro"]),
        ("16:00", "20:00", "Prime Time", ["Tech House", "Techno", "Trance", "Progressive", "80s"]),
        ("20:00", "00:00", "Party Night", ["Techno", "Trance", "BASSLINE", "Drum & Bass"]),
    ],
    "Saturday": [
        ("00:00", "04:00", "After Dark", ["Techno", "Hard Techno", "Trance", "Psy-Trance"]),
        ("04:00", "08:00", "Late Mornings", ["Techno", "Trance", "Progressive", "Psy-Trance"]),
        ("08:00", "12:00", "Morning", ["Chill", "House", "Progressive", "Melodic"]),
        ("12:00", "16:00", "Day Party", ["House", "Tech House", "Progressive", "Electro"]),
        ("16:00", "20:00", "Prime Time", ["Tech House", "Techno", "Trance", "Progressive"]),
        ("20:00", "00:00", "Party Night", ["Techno", "Trance", "BASSLINE", "Drum & Bass"]),
    ],
    "Sunday": [
        ("00:00", "04:00", "Late Night", ["Techno", "Trance", "Progressive", "Psy-Trance"]),
        ("04:00", "08:00", "After Hours", ["Deep House", "Progressive", "Melodic", "Chill"]),
        ("08:00", "12:00", "Sunday Morning", ["Chill", "Downtempo", "Deep House", "Ambient"]),
        ("12:00", "16:00", "Sunday Session", ["House", "Deep House", "Progressive", "Organic House"]),
        ("16:00", "20:00", "Sunday Sunset", ["Melodic", "Progressive", "Deep House", "Chill"]),
        ("20:00", "00:00", "Sunday Night", ["Chill", "Deep House", "Progressive", "Trance"]),
    ],
}


def _time_to_minutes(value):
    value = str(value or "").strip()
    hour, minute = value.split(":", 1)
    hour = int(hour)
    minute = int(minute)
    if hour == 24 and minute == 0:
        return 1440
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("Invalid time")
    return hour * 60 + minute


def _minutes_to_time(value):
    value = int(value)
    if value == 1440:
        return "00:00"
    return f"{value // 60:02d}:{value % 60:02d}"


def _seed_scheduler(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            start_minutes INTEGER NOT NULL,
            end_minutes INTEGER NOT NULL,
            name TEXT NOT NULL,
            genres_json TEXT NOT NULL,
            genre_weights_json TEXT NOT NULL DEFAULT '{}',
            anti_genres_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    # Upgrade existing Radio RRR databases without destroying the schedule.
    schedule_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(schedule)").fetchall()
    }
    if "genre_weights_json" not in schedule_columns:
        conn.execute(
            "ALTER TABLE schedule ADD COLUMN genre_weights_json TEXT NOT NULL DEFAULT '{}'"
        )
    if "anti_genres_json" not in schedule_columns:
        conn.execute(
            "ALTER TABLE schedule ADD COLUMN anti_genres_json TEXT NOT NULL DEFAULT '[]'"
        )
    # Seed the new avoid rules on existing schedules without overwriting
    # any rules the operator has already configured.
    existing_schedule_rows = conn.execute(
        "SELECT id, name, anti_genres_json FROM schedule"
    ).fetchall()
    for sr in existing_schedule_rows:
        try:
            current_anti = json.loads(sr["anti_genres_json"] or "[]")
        except Exception:
            current_anti = []
        if current_anti:
            continue
        seeded = list(DEFAULT_ANTI_GENRES)
        if "dinner warm" in str(sr["name"] or "").casefold():
            seeded.extend(DEFAULT_DINNER_WARMUP_ANTI_GENRES)
        conn.execute(
            "UPDATE schedule SET anti_genres_json=? WHERE id=?",
            (json.dumps(sorted(set(seeded), key=str.casefold)), sr["id"]),
        )
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_schedule_day_start
        ON schedule(day, start_minutes)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_genres (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """)

    now = datetime.now(timezone.utc).isoformat()
    for genre in DEFAULT_GENRES:
        conn.execute(
            "INSERT OR IGNORE INTO schedule_genres (name, enabled, created_at) VALUES (?, 1, ?)",
            (genre, now),
        )

    count = conn.execute("SELECT COUNT(*) AS c FROM schedule").fetchone()["c"]
    if count == 0:
        for day in SCHEDULE_DAYS:
            for start, end, name, genres in DEFAULT_SCHEDULE[day]:
                anti = list(DEFAULT_ANTI_GENRES)
                if name == "Dinner Warm ups":
                    anti.extend(DEFAULT_DINNER_WARMUP_ANTI_GENRES)
                conn.execute(
                    """
                    INSERT INTO schedule
                    (day, start_minutes, end_minutes, name, genres_json, genre_weights_json, anti_genres_json, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        day,
                        _time_to_minutes(start),
                        _time_to_minutes(end),
                        name,
                        json.dumps(genres),
                        json.dumps({}),
                        json.dumps(anti),
                        now,
                        now,
                    ),
                )
    else:
        # Existing schedules pre-date anti-genres. Add the common non-music
        # exclusions and the requested Dinner Warm ups exclusions once.
        rows = conn.execute(
            "SELECT id, name, anti_genres_json FROM schedule"
        ).fetchall()
        for row in rows:
            try:
                anti = json.loads(row["anti_genres_json"] or "[]")
            except Exception:
                anti = []
            if not isinstance(anti, list):
                anti = []
            existing = {str(x).strip().casefold() for x in anti if str(x).strip()}
            changed = False
            additions = list(DEFAULT_ANTI_GENRES)
            if row["name"] == "Dinner Warm ups":
                additions.extend(DEFAULT_DINNER_WARMUP_ANTI_GENRES)
            for genre in additions:
                if genre.casefold() not in existing:
                    anti.append(genre)
                    existing.add(genre.casefold())
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE schedule SET anti_genres_json=?, updated_at=? WHERE id=?",
                    (json.dumps(anti), now, row["id"]),
                )


def _db_schedule():
    conn = get_db()
    rows = conn.execute("""
        SELECT *
        FROM schedule
        WHERE enabled = 1
        ORDER BY CASE day
            WHEN 'Monday' THEN 0 WHEN 'Tuesday' THEN 1 WHEN 'Wednesday' THEN 2
            WHEN 'Thursday' THEN 3 WHEN 'Friday' THEN 4 WHEN 'Saturday' THEN 5
            WHEN 'Sunday' THEN 6 ELSE 7 END, start_minutes, id
    """).fetchall()
    conn.close()
    return [
        {
            "id": row["id"], "day": row["day"],
            "start": _minutes_to_time(row["start_minutes"]),
            "end": _minutes_to_time(row["end_minutes"]),
            "start_minutes": row["start_minutes"], "end_minutes": row["end_minutes"],
            "name": row["name"],
            "genres": json.loads(row["genres_json"] or "[]"),
            "genre_weights": json.loads(row["genre_weights_json"] or "{}"),
            "anti_genres": json.loads(row["anti_genres_json"] or "[]"),
            "bpm_min": row["bpm_min"] if "bpm_min" in row.keys() else None,
            "bpm_max": row["bpm_max"] if "bpm_max" in row.keys() else None,
            "enabled": bool(row["enabled"]),
        }
        for row in rows
    ]


def _db_schedule_profiles():
    rows = _db_schedule()
    profiles = {"weekday": [], "fridaySaturday": [], "sunday": []}
    for row in rows:
        block = {
            "start": row["start_minutes"] / 60,
            "end": row["end_minutes"] / 60,
            "name": row["name"],
            "genres": row["genres"],
            "genre_weights": row.get("genre_weights", {}),
            "anti_genres": row.get("anti_genres", []),
            "bpm_min": row.get("bpm_min"),
            "bpm_max": row.get("bpm_max"),
        }
        if row["day"] == "Sunday":
            profiles["sunday"].append(block)
        elif row["day"] in ("Friday", "Saturday"):
            profiles["fridaySaturday"].append(block)
        else:
            profiles["weekday"].append(block)
    return profiles

def ensure_ai_platform_schema(conn):
    """Upgrade AI genre tables to use (platform, username) identity safely."""
    detection_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(ai_genre_detection)").fetchall()
    }

    if detection_columns and "platform" not in detection_columns:
        conn.execute("""
            CREATE TABLE ai_genre_detection_new (
                platform TEXT NOT NULL,
                username TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                genres_json TEXT NOT NULL,
                PRIMARY KEY (platform, username)
            )
        """)
        conn.execute("""
            INSERT INTO ai_genre_detection_new
            (platform, username, detected_at, genres_json)
            SELECT 'TikTok', username, detected_at, genres_json
            FROM ai_genre_detection
        """)
        conn.execute("DROP TABLE ai_genre_detection")
        conn.execute("ALTER TABLE ai_genre_detection_new RENAME TO ai_genre_detection")
    elif not detection_columns:
        conn.execute("""
            CREATE TABLE ai_genre_detection (
                platform TEXT NOT NULL,
                username TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                genres_json TEXT NOT NULL,
                PRIMARY KEY (platform, username)
            )
        """)

    observation_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(ai_genre_observations)").fetchall()
    }

    if observation_columns and "platform" not in observation_columns:
        conn.execute(
            "ALTER TABLE ai_genre_observations "
            "ADD COLUMN platform TEXT NOT NULL DEFAULT 'TikTok'"
        )
    elif not observation_columns:
        conn.execute("""
            CREATE TABLE ai_genre_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                username TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                genres_json TEXT NOT NULL
            )
        """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_genre_observations_platform_username
        ON ai_genre_observations(platform, username)
    """)


def init_db():
    conn = get_db()

    _seed_scheduler(conn)

    ensure_ai_platform_schema(conn)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_genre_observations_username
        ON ai_genre_observations(username)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ai_genre_observations_detected_at
        ON ai_genre_observations(detected_at)
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS live_djs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT,
            platform TEXT NOT NULL,
            url TEXT,
            genre TEXT,
            viewers INTEGER DEFAULT 0,
            started_at TEXT,
            updated_at TEXT,
            UNIQUE(platform, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS favourite_djs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            live_url TEXT,
            genre TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT,
            UNIQUE(platform, username)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS tiktok_live_stats (
            username TEXT PRIMARY KEY COLLATE NOCASE,
            first_tracked_at TEXT NOT NULL,
            last_checked_at TEXT,
            next_check_at TEXT,
            last_live_at TEXT,
            checks INTEGER NOT NULL DEFAULT 0,
            live_checks INTEGER NOT NULL DEFAULT 0,
            live_sessions INTEGER NOT NULL DEFAULT 0,
            last_is_live INTEGER NOT NULL DEFAULT 0,
            auto_dormant INTEGER NOT NULL DEFAULT 0,
            last_result TEXT
        )
    """)

    now_iso = datetime.now(timezone.utc).isoformat()

    conn.execute("""
        INSERT OR IGNORE INTO tiktok_live_stats
        (username, first_tracked_at, next_check_at)
        SELECT username, ?, ?
        FROM favourite_djs
        WHERE platform = 'TikTok'
          AND enabled = 1
    """, (now_iso, now_iso))

    # Add profile metadata columns to existing databases without
    # recreating or losing the current favourite_djs records.
    ensure_profile_columns(conn)

    # Remove stale LIVE rows left behind by an earlier process/version.
    # This is safe because live_djs is only authoritative for currently
    # live sessions and fresh rows are repopulated by the monitor.
    stale_cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=LIVE_STALE_AFTER)
    ).isoformat()

    conn.execute("""
        DELETE FROM live_djs
        WHERE updated_at IS NULL
           OR updated_at < ?
    """, (stale_cutoff,))

    favourites = [
        (
            "cwispyofficial",
            "Cwispy Official",
            "TikTok",
            "https://www.tiktok.com/@cwispyofficial",
            "https://www.tiktok.com/@cwispyofficial/live",
            None,
        ),
        (
            "alaynathevibe",
            "Alayna the Vibe",
            "TikTok",
            "https://www.tiktok.com/@alaynathevibe",
            "https://www.tiktok.com/@alaynathevibe/live",
            None,
        ),
    ]

    for username, name, platform, profile_url, live_url, genre in favourites:
        conn.execute("""
            INSERT INTO favourite_djs
            (
                username,
                name,
                platform,
                profile_url,
                live_url,
                genre,
                enabled,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)

            ON CONFLICT(platform, username) DO UPDATE SET
                name = excluded.name,
                profile_url = excluded.profile_url,
                live_url = excluded.live_url,
                genre = excluded.genre,
                enabled = 1
        """, (
            username,
            name,
            platform,
            profile_url,
            live_url,
            genre,
            datetime.now(timezone.utc).isoformat(),
        ))

    conn.commit()
    conn.close()


# ---------------------------------------------------------
# TikTok LIVE monitor
# ---------------------------------------------------------


# Missing TikTok profile pictures are retried with a cooldown. A failed
# network/profile lookup must not suppress retries for the rest of a live
# session.
PROFILE_PICTURE_RETRY_INTERVAL = 15 * 60
profile_picture_refresh_attempted = {}


async def ensure_favourite_profile_picture(username):
    """Refresh a missing favourite DJ profile picture with retry cooldown."""
    username = str(username or "").strip().lstrip("@")

    if not username:
        return

    last_attempt = profile_picture_refresh_attempted.get(username)
    now_monotonic = time.monotonic()

    if now_monotonic < profile_refresh_rate_limited_until:
        return

    if now_monotonic < profile_refresh_failed_until.get(username, 0.0):
        return

    if (
        last_attempt is not None
        and now_monotonic - last_attempt < PROFILE_PICTURE_RETRY_INTERVAL
    ):
        return

    conn = get_db()

    try:
        favourite = conn.execute(
            """
            SELECT profile_pic
            FROM favourite_djs
            WHERE lower(username) = lower(?)
              AND platform = 'TikTok'
            LIMIT 1
            """,
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if not favourite:
        return

    profile_pic = str(favourite["profile_pic"] or "").strip()

    if profile_pic:
        return

    # Mark the attempt before awaiting the network request so another polling
    # cycle cannot launch the same lookup concurrently. Failed/no-picture
    # attempts become eligible again after PROFILE_PICTURE_RETRY_INTERVAL.
    profile_picture_refresh_attempted[username] = now_monotonic

    print(
        f"[TikTok] Refreshing missing profile picture for favourite DJ @{username}"
    )

    try:
        profile = await import_tiktok_dj(
            username,
            profile_pic_only=True,
        )

        if str(profile.get("profile_pic") or "").strip():
            print(
                f"[TikTok] Profile picture refreshed for @{username}"
            )
        else:
            print(
                f"[TikTok] Profile refresh returned no usable picture for @{username}"
            )
    except Exception as e:
        profile_refresh_failed_until[username] = (
            time.monotonic() + PROFILE_REFRESH_FAILURE_COOLDOWN
        )
        print(
            f"[TikTok] Could not refresh profile picture for @{username}: {e}"
        )


def _tiktok_stats_datetime(value):
    return _parse_iso_datetime(value)


def _tiktok_scan_interval(stats, now=None):
    current = now or datetime.now(timezone.utc)

    if int(stats.get("auto_dormant") or 0):
        return TIKTOK_DORMANT_RECHECK

    if int(stats.get("last_is_live") or 0):
        return TIKTOK_SCAN_CURRENTLY_LIVE

    first_tracked = _tiktok_stats_datetime(stats.get("first_tracked_at"))
    last_live = _tiktok_stats_datetime(stats.get("last_live_at"))

    live_sessions = int(stats.get("live_sessions") or 0)
    checks = int(stats.get("checks") or 0)

    tracked_days = 1.0
    if first_tracked is not None:
        tracked_days = max(
            1.0,
            (current - first_tracked).total_seconds() / 86400.0,
        )

    sessions_per_day = live_sessions / tracked_days

    if last_live is not None:
        live_age = max(
            0.0,
            (current - last_live).total_seconds(),
        )

        if live_age <= 2 * 86400 or sessions_per_day >= 0.50:
            return TIKTOK_SCAN_DAILY

        if live_age <= 7 * 86400 or sessions_per_day >= 0.10:
            return TIKTOK_SCAN_WEEKLY

        if live_age <= TIKTOK_DORMANT_AFTER:
            return TIKTOK_SCAN_OCCASIONAL

    if first_tracked is not None:
        tracked_age = (current - first_tracked).total_seconds()

        # Brand-new accounts get several relatively quick observations. If
        # repeated successful checks never find them LIVE, progressively move
        # them out of the fast queue instead of polling them every four minutes
        # for the entire learning period.
        if tracked_age < TIKTOK_NEW_DJ_LEARNING_DAYS * 86400:
            if checks < TIKTOK_UNKNOWN_FAST_CHECKS:
                return TIKTOK_SCAN_UNKNOWN_NEW
            if checks < TIKTOK_UNKNOWN_WARM_CHECKS:
                return TIKTOK_SCAN_OCCASIONAL

    return TIKTOK_SCAN_COLD


def _tiktok_scan_priority(row, now=None):
    current = now or datetime.now(timezone.utc)
    stats = dict(row)
    interval = _tiktok_scan_interval(stats, current)

    last_live = _tiktok_stats_datetime(stats.get("last_live_at"))
    first_tracked = _tiktok_stats_datetime(stats.get("first_tracked_at"))
    live_sessions = int(stats.get("live_sessions") or 0)

    tracked_days = 1.0
    if first_tracked is not None:
        tracked_days = max(
            1.0,
            (current - first_tracked).total_seconds() / 86400.0,
        )

    sessions_per_day = live_sessions / tracked_days
    last_live_timestamp = last_live.timestamp() if last_live else 0.0

    return (
        interval,
        -sessions_per_day,
        -last_live_timestamp,
        str(row["username"] or "").casefold(),
    )


def _ensure_tiktok_stats_row(conn, username, now_iso=None):
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR IGNORE INTO tiktok_live_stats
        (username, first_tracked_at, next_check_at)
        VALUES (?, ?, ?)
        """,
        (username, now_iso, now_iso),
    )


def _record_tiktok_live_check(username, is_live):
    username = str(username or "").strip().lstrip("@")
    if not username:
        return

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    conn = get_db()
    try:
        _ensure_tiktok_stats_row(conn, username, now_iso)

        existing = conn.execute(
            """
            SELECT *
            FROM tiktok_live_stats
            WHERE lower(username) = lower(?)
            """,
            (username,),
        ).fetchone()

        stats = dict(existing)
        was_live = bool(stats.get("last_is_live"))
        was_dormant = bool(stats.get("auto_dormant"))
        live_sessions = int(stats.get("live_sessions") or 0)

        if is_live and not was_live:
            live_sessions += 1

        next_stats = dict(stats)
        next_stats["checks"] = int(stats.get("checks") or 0) + 1
        next_stats["live_checks"] = int(stats.get("live_checks") or 0) + (1 if is_live else 0)
        next_stats["live_sessions"] = live_sessions
        next_stats["last_is_live"] = 1 if is_live else 0
        next_stats["auto_dormant"] = 0 if is_live else int(stats.get("auto_dormant") or 0)
        if is_live:
            next_stats["last_live_at"] = now_iso

        interval = _tiktok_scan_interval(next_stats, now)
        next_check_at = (now + timedelta(seconds=interval)).isoformat()

        conn.execute(
            """
            UPDATE tiktok_live_stats
            SET last_checked_at = ?,
                next_check_at = ?,
                last_live_at = CASE WHEN ? = 1 THEN ? ELSE last_live_at END,
                checks = checks + 1,
                live_checks = live_checks + ?,
                live_sessions = ?,
                last_is_live = ?,
                auto_dormant = CASE WHEN ? = 1 THEN 0 ELSE auto_dormant END,
                last_result = ?
            WHERE lower(username) = lower(?)
            """,
            (
                now_iso,
                next_check_at,
                1 if is_live else 0,
                now_iso,
                1 if is_live else 0,
                live_sessions,
                1 if is_live else 0,
                1 if is_live else 0,
                "live" if is_live else "offline",
                username,
            ),
        )

        if is_live and was_dormant:
            conn.execute(
                """
                UPDATE favourite_djs
                SET enabled = 1
                WHERE platform = 'TikTok'
                  AND lower(username) = lower(?)
                """,
                (username,),
            )
            print(
                f"[TikTok] AUTO-REENABLED: @{username} — dormant DJ is LIVE again"
            )

        conn.commit()
    finally:
        conn.close()


def _defer_tiktok_transient_error(username, seconds=None):
    username = str(username or "").strip().lstrip("@")
    if not username:
        return

    delay = int(seconds or TIKTOK_LIVE_ERROR_BACKOFF_SECONDS)
    next_check = (
        datetime.now(timezone.utc) + timedelta(seconds=delay)
    ).isoformat()

    conn = get_db()
    try:
        _ensure_tiktok_stats_row(conn, username)
        conn.execute(
            """
            UPDATE tiktok_live_stats
            SET next_check_at = ?,
                last_result = 'error'
            WHERE lower(username) = lower(?)
            """,
            (next_check, username),
        )
        conn.commit()
    finally:
        conn.close()


def _refresh_tiktok_dormant_states():
    now = datetime.now(timezone.utc)

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                f.username,
                f.enabled,
                s.first_tracked_at,
                s.last_live_at,
                s.last_is_live,
                s.auto_dormant
            FROM favourite_djs f
            JOIN tiktok_live_stats s
              ON lower(s.username) = lower(f.username)
            WHERE f.platform = 'TikTok'
              AND (f.enabled = 1 OR s.auto_dormant = 1)
            """
        ).fetchall()

        for row in rows:
            if int(row["auto_dormant"] or 0):
                continue
            if int(row["last_is_live"] or 0):
                continue

            reference = (
                _tiktok_stats_datetime(row["last_live_at"])
                or _tiktok_stats_datetime(row["first_tracked_at"])
            )
            if reference is None:
                continue

            if (now - reference).total_seconds() < TIKTOK_DORMANT_AFTER:
                continue

            username = str(row["username"] or "").strip()
            if not username:
                continue

            next_week = (
                now + timedelta(seconds=TIKTOK_DORMANT_RECHECK)
            ).isoformat()

            conn.execute(
                """
                UPDATE favourite_djs
                SET enabled = 0
                WHERE platform = 'TikTok'
                  AND lower(username) = lower(?)
                """,
                (username,),
            )
            conn.execute(
                """
                UPDATE tiktok_live_stats
                SET auto_dormant = 1,
                    next_check_at = ?,
                    last_result = 'dormant'
                WHERE lower(username) = lower(?)
                """,
                (next_week, username),
            )
            conn.execute(
                """
                DELETE FROM live_djs
                WHERE platform = 'TikTok'
                  AND lower(username) = lower(?)
                """,
                (username,),
            )

            print(
                f"[TikTok] AUTO-DORMANT: @{username} — "
                "not observed LIVE for 30 days; weekly checks only"
            )

        conn.commit()
    finally:
        conn.close()


def _build_tiktok_scan_queue():
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    conn = get_db()
    try:
        enabled_rows = conn.execute(
            """
            SELECT username
            FROM favourite_djs
            WHERE platform = 'TikTok'
              AND enabled = 1
            """
        ).fetchall()

        for row in enabled_rows:
            _ensure_tiktok_stats_row(
                conn,
                str(row["username"] or "").strip(),
                now_iso,
            )

        conn.commit()

        rows = conn.execute(
            """
            SELECT
                f.username,
                f.name,
                f.live_url,
                f.enabled,
                f.created_at,
                s.first_tracked_at,
                s.last_checked_at,
                s.next_check_at,
                s.last_live_at,
                s.checks,
                s.live_checks,
                s.live_sessions,
                s.last_is_live,
                s.auto_dormant,
                s.last_result
            FROM favourite_djs f
            JOIN tiktok_live_stats s
              ON lower(s.username) = lower(f.username)
            WHERE f.platform = 'TikTok'
              AND (f.enabled = 1 OR s.auto_dormant = 1)
              AND (
                    s.next_check_at IS NULL
                    OR s.next_check_at <= ?
                  )
            """,
            (now_iso,),
        ).fetchall()

        queue = list(rows)
        queue.sort(key=lambda row: _tiktok_scan_priority(row, now))
        return queue
    finally:
        conn.close()


def _is_tiktok_invalid_account_error(error):
    """Return whether TikTokLive reported a non-LIVE-capable/missing account."""
    message = str(error or "").casefold()
    return (
        "not capable of going live on tiktok" in message
        and (
            "has never gone live on tiktok" in message
            or "does not exist" in message
        )
    )


async def _verify_and_cleanup_invalid_tiktok_account(username):
    """
    Verify an account after repeated TikTokLive invalid-account responses.

    If the public profile can still be parsed, disable the favourite rather
    than deleting it because the TikTokLive exception also covers accounts
    that simply cannot/never have gone LIVE. Only permanently delete when
    TikTok explicitly returns HTTP 404 for the profile. Other verification
    failures are treated as inconclusive and leave the database untouched.
    """
    username = str(username or "").strip().lstrip("@")
    if not username:
        return "unknown"

    try:
        await fetch_tiktok_profile_fallback(username)
        state = "exists"
    except httpx.HTTPStatusError as error:
        if error.response is not None and error.response.status_code == 404:
            state = "missing"
        else:
            print(
                f"[TikTok] INVALID DJ VERIFY ERROR @{username}: {error}"
            )
            return "unknown"
    except Exception as error:
        print(
            f"[TikTok] INVALID DJ VERIFY ERROR @{username}: {error}"
        )
        return "unknown"

    conn = get_db()
    try:
        if state == "missing":
            conn.execute(
                """
                DELETE FROM favourite_djs
                WHERE lower(username) = lower(?)
                  AND platform = 'TikTok'
                """,
                (username,),
            )
            conn.execute(
                """
                DELETE FROM live_djs
                WHERE lower(username) = lower(?)
                  AND platform = 'TikTok'
                """,
                (username,),
            )
            conn.commit()

            conn.execute(
                """
                DELETE FROM tiktok_live_stats
                WHERE lower(username) = lower(?)
                """,
                (username,),
            )
            conn.commit()

            offline_miss_counts.pop(username, None)
            profile_picture_refresh_attempted.pop(username, None)
            invalid_tiktok_account_counts.pop(username, None)

            print(
                f"[TikTok] AUTO-REMOVED: @{username} — "
                "TikTok profile returned HTTP 404"
            )
            return "removed"

        conn.execute(
            """
            UPDATE favourite_djs
            SET enabled = 0
            WHERE lower(username) = lower(?)
              AND platform = 'TikTok'
            """,
            (username,),
        )
        conn.execute(
            """
            DELETE FROM live_djs
            WHERE lower(username) = lower(?)
              AND platform = 'TikTok'
            """,
            (username,),
        )
        conn.execute(
            """
            UPDATE tiktok_live_stats
            SET auto_dormant = 0,
                next_check_at = NULL,
                last_result = 'invalid'
            WHERE lower(username) = lower(?)
            """,
            (username,),
        )
        conn.commit()

        offline_miss_counts.pop(username, None)
        profile_picture_refresh_attempted.pop(username, None)
        invalid_tiktok_account_counts.pop(username, None)

        print(
            f"[TikTok] AUTO-DISABLED: @{username} — "
            "profile exists but TikTokLive repeatedly reports "
            "the account cannot/has never gone LIVE"
        )
        return "disabled"
    finally:
        conn.close()


def _run_tiktok_is_live_check(username):
    """
    Run one TikTokLive liveness check on a private asyncio loop.

    TikTokLiveClient.is_live() is async, but observed production behaviour shows
    that parts of its request path can block the Uvicorn/FastAPI event loop for
    several seconds. This helper is called through asyncio.to_thread() so any
    blocking work inside TikTokLive cannot stall HTTP/HLS servicing.
    """
    async def _check():
        client = TikTokLiveClient(
            unique_id=f"@{username}"
        )
        return await client.is_live()

    return asyncio.run(_check())


async def check_tiktok_dj(username, name, live_url):

    try:

        is_live = await asyncio.to_thread(
            _run_tiktok_is_live_check,
            username,
        )

        # Any successful TikTok liveness response proves this account is
        # currently queryable, so clear prior invalid-account observations.
        invalid_tiktok_account_counts.pop(username, None)

        _record_tiktok_live_check(
            username,
            bool(is_live),
        )

        now = datetime.now(
            timezone.utc
        ).isoformat()

        if is_live:

            # Any confirmed LIVE response clears previous false-negative
            # observations immediately.
            offline_miss_counts.pop(username, None)

            await ensure_favourite_profile_picture(username)

        conn = get_db()

        if is_live:

            existing = conn.execute("""
                SELECT started_at
                FROM live_djs
                WHERE username = ?
                  AND platform = 'TikTok'
            """, (
                username,
            )).fetchone()

            if existing and existing["started_at"]:
                started_at = existing["started_at"]
            else:
                started_at = now

            conn.execute("""
                INSERT INTO live_djs
                (
                    username,
                    name,
                    platform,
                    url,
                    genre,
                    viewers,
                    started_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)

                ON CONFLICT(platform, username) DO UPDATE SET
                    name = excluded.name,
                    url = excluded.url,
                    genre = excluded.genre,
                    viewers = excluded.viewers,
                    started_at = excluded.started_at,
                    updated_at = excluded.updated_at
            """, (
                username,
                name,
                "TikTok",
                live_url,
                None,
                0,
                started_at,
                now,
            ))

            print(
                f"[TikTok] LIVE: @{username}"
            )

        else:

            # Do NOT delete the DJ on one false-negative. TikTok's
            # liveness endpoint can briefly report OFFLINE while the
            # creator is still live.
            miss_count = offline_miss_counts.get(username, 0) + 1
            offline_miss_counts[username] = miss_count

            # If this DJ is currently feeding the shared relay, use a
            # larger confirmation threshold. This protects an otherwise
            # healthy HLS stream from a transient TikTok status failure.
            is_active_relay = (
                str(relay_platform or "TikTok").casefold() == "tiktok"
                and str(relay_username or "").lower()
                == str(username).lower()
                and relay_process is not None
                and relay_process.poll() is None
            )

            required = (
                ACTIVE_RELAY_OFFLINE_CONFIRMATIONS_REQUIRED
                if is_active_relay
                else OFFLINE_CONFIRMATIONS_REQUIRED
            )

            existing = conn.execute("""
                SELECT username
                FROM live_djs
                WHERE username = ?
                  AND platform = 'TikTok'
            """, (
                username,
            )).fetchone()

            if existing and miss_count >= required:

                profile_picture_refresh_attempted.pop(username, None)

                conn.execute("""
                    DELETE FROM live_djs
                    WHERE username = ?
                      AND platform = 'TikTok'
                """, (
                    username,
                ))

                offline_miss_counts.pop(username, None)

                print(
                    f"[TikTok] OFFLINE CONFIRMED: @{username} "
                    f"after {required} consecutive checks"
                    + (
                        " (active relay protected)"
                        if is_active_relay
                        else ""
                    )
                )

            elif existing:

                print(
                    f"[TikTok] OFFLINE CHECK {miss_count}/{required}: "
                    f"@{username} — retaining LIVE state"
                    + (
                        " (active relay protected)"
                        if is_active_relay
                        else ""
                    )
                )

            else:

                # There is no stale LIVE row to protect. Keep the
                # counter so repeated false results are still visible,
                # but avoid creating a row for a DJ that is not live.
                print(
                    f"[TikTok] OFFLINE: @{username} "
                    f"(not currently in live list)"
                )

        conn.commit()
        conn.close()

        return True

    except Exception as e:

        # TikTokLive uses one broad exception for accounts that cannot go
        # LIVE, have never gone LIVE, or no longer exist. These are catalogue
        # quality problems, not API-throttle failures, so keep them out of the
        # transient-error circuit breaker and confirm them separately.
        if _is_tiktok_invalid_account_error(e):
            invalid_count = (
                invalid_tiktok_account_counts.get(username, 0) + 1
            )
            invalid_tiktok_account_counts[username] = invalid_count

            print(
                f"[TikTok] INVALID DJ CHECK "
                f"{invalid_count}/{TIKTOK_INVALID_ACCOUNT_CONFIRMATIONS_REQUIRED}: "
                f"@{username}"
            )

            if (
                invalid_count
                >= TIKTOK_INVALID_ACCOUNT_CONFIRMATIONS_REQUIRED
            ):
                result = await _verify_and_cleanup_invalid_tiktok_account(
                    username
                )

                # An inconclusive verification must not delete/disable
                # anything. Reset the counter so a later monitor pass can
                # gather fresh evidence instead of repeatedly verifying.
                if result == "unknown":
                    invalid_tiktok_account_counts.pop(
                        username,
                        None,
                    )

            # Invalid-account responses are catalogue problems, not a reason
            # to keep the DJ at the head of the due queue. Defer the next
            # liveness probe even when profile verification is inconclusive.
            _defer_tiktok_transient_error(
                username,
                TIKTOK_INVALID_ACCOUNT_RECHECK_SECONDS,
            )

            return True

        # Other exceptions do not count as OFFLINE confirmations. A network,
        # API, JSON/parser, or TikTokLive transport error is fundamentally
        # different from a confirmed offline response.
        print(
            f"[TikTok] CHECK ERROR @{username}: {e}"
        )

        _defer_tiktok_transient_error(
            username,
            TIKTOK_LIVE_ERROR_BACKOFF_SECONDS,
        )

        return False


def _maintain_tiktok_live_state():
    """Run periodic stale-row and in-memory counter maintenance."""
    stale_cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=LIVE_STALE_AFTER)
    ).isoformat()

    conn = get_db()
    try:
        stale_rows = conn.execute("""
            SELECT username
            FROM live_djs
            WHERE platform = 'TikTok'
              AND (updated_at IS NULL
               OR updated_at < ?)
        """, (stale_cutoff,)).fetchall()

        if stale_rows:
            active_username = (
                str(relay_username or "").lower()
                if str(relay_platform or "TikTok").casefold() == "tiktok"
                else ""
            )

            removable = [
                stale for stale in stale_rows
                if str(stale["username"]).lower() != active_username
            ]

            if removable:
                placeholders = ",".join("?" for _ in removable)
                removable_usernames = [
                    stale["username"] for stale in removable
                ]

                conn.execute(
                    f"""
                    DELETE FROM live_djs
                    WHERE platform = 'TikTok'
                      AND username IN ({placeholders})
                    """,
                    removable_usernames,
                )
                conn.commit()

                for stale in removable:
                    offline_miss_counts.pop(
                        stale["username"],
                        None,
                    )
                    print(
                        f"[TikTok] STALE LIVE REMOVED: "
                        f"@{stale['username']}"
                    )

            protected = [
                stale for stale in stale_rows
                if str(stale["username"]).lower() == active_username
            ]

            for stale in protected:
                print(
                    f"[TikTok] STALE CHECK: "
                    f"@{stale['username']} retained because "
                    f"it is the active relay"
                )

        tracked_rows = conn.execute("""
            SELECT f.username
            FROM favourite_djs f
            LEFT JOIN tiktok_live_stats s
              ON lower(s.username) = lower(f.username)
            WHERE f.platform = 'TikTok'
              AND (
                    f.enabled = 1
                    OR COALESCE(s.auto_dormant, 0) = 1
                  )
        """).fetchall()

        tracked_usernames = {
            str(row["username"])
            for row in tracked_rows
        }

        for username in list(offline_miss_counts):
            if username not in tracked_usernames:
                offline_miss_counts.pop(username, None)

        for username in list(invalid_tiktok_account_counts):
            if username not in tracked_usernames:
                invalid_tiktok_account_counts.pop(username, None)

    finally:
        conn.close()


async def tiktok_monitor():

    print(
        "[TikTok] Continuous priority LIVE monitor starting..."
    )

    await asyncio.sleep(5)

    consecutive_errors = 0
    last_maintenance = 0.0
    last_queue_preview = 0.0

    while True:

        try:
            now_monotonic = time.monotonic()

            if (
                now_monotonic - last_maintenance
                >= TIKTOK_MAINTENANCE_INTERVAL
            ):
                _refresh_tiktok_dormant_states()
                _maintain_tiktok_live_state()
                last_maintenance = now_monotonic

            # Rebuild the *due* set after every individual check. This is the
            # important difference from the old batch queue: a DJ checked LIVE
            # 90 seconds ago can become the next highest-priority item as soon
            # as it is due, even while many cold DJs have never been processed.
            due = _build_tiktok_scan_queue()

            if not due:
                await asyncio.sleep(
                    TIKTOK_SCHEDULER_IDLE_SLEEP
                )
                continue

            # Keep diagnostics useful without logging a giant queue before
            # every single request.
            if (
                now_monotonic - last_queue_preview
                >= TIKTOK_MAINTENANCE_INTERVAL
            ):
                preview = ", ".join(
                    f"@{row['username']}"
                    for row in due[:5]
                )
                print(
                    f"[TikTok] Priority scheduler: {len(due)} due"
                    + (f"; next: {preview}" if preview else "")
                )
                last_queue_preview = now_monotonic

            row = due[0]

            check_ok = await check_tiktok_dj(
                row["username"],
                row["name"],
                row["live_url"],
            )

            if check_ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1

                if (
                    consecutive_errors
                    >= TIKTOK_LIVE_ERROR_BACKOFF_THRESHOLD
                ):
                    print(
                        "[TikTok] LIVE monitor backing off for "
                        f"{TIKTOK_LIVE_ERROR_BACKOFF_SECONDS}s after "
                        f"{consecutive_errors} consecutive check errors; "
                        "continuous scheduler will resume with the most "
                        "overdue/high-priority DJ"
                    )
                    await asyncio.sleep(
                        TIKTOK_LIVE_ERROR_BACKOFF_SECONDS
                    )
                    consecutive_errors = 0
                    continue

            # Never start TikTok liveness checks faster than this minimum gap.
            await asyncio.sleep(
                TIKTOK_LIVE_CHECK_DELAY
            )

        except asyncio.CancelledError:
            raise

        except Exception as e:

            print(
                f"[TikTok] MONITOR ERROR: {e}"
            )

            await asyncio.sleep(10)


async def get_twitch_app_token(force_refresh=False):
    """Return a cached Twitch app access token, refreshing when required."""
    global twitch_app_token, twitch_app_token_expires_at

    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        raise RuntimeError(
            "Twitch API credentials are not configured"
        )

    now = time.monotonic()

    if (
        not force_refresh
        and twitch_app_token
        and now < (
            twitch_app_token_expires_at
            - TWITCH_TOKEN_REFRESH_MARGIN
        )
    ):
        return twitch_app_token

    async with twitch_token_lock:
        now = time.monotonic()

        if (
            not force_refresh
            and twitch_app_token
            and now < (
                twitch_app_token_expires_at
                - TWITCH_TOKEN_REFRESH_MARGIN
            )
        ):
            return twitch_app_token

        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
        ) as http:
            response = await http.post(
                "https://id.twitch.tv/oauth2/token",
                data={
                    "client_id": TWITCH_CLIENT_ID,
                    "client_secret": TWITCH_CLIENT_SECRET,
                    "grant_type": "client_credentials",
                },
            )

        response.raise_for_status()
        payload = response.json()

        token = str(
            payload.get("access_token") or ""
        ).strip()

        if not token:
            raise RuntimeError(
                "Twitch token response did not contain an access token"
            )

        try:
            expires_in = max(
                60,
                int(payload.get("expires_in") or 0),
            )
        except (TypeError, ValueError):
            expires_in = 3600

        twitch_app_token = token
        twitch_app_token_expires_at = (
            time.monotonic() + expires_in
        )

        print(
            f"[Twitch] App access token ready "
            f"(expires in {expires_in}s)"
        )

        return twitch_app_token


async def fetch_twitch_live_streams(usernames):
    """Return Helix live-stream records keyed by lowercase Twitch login."""
    logins = []

    for username in usernames:
        username = str(username or "").strip().lower()
        if username and username not in logins:
            logins.append(username)

    if not logins:
        return {}

    results = {}

    # Helix Get Streams accepts at most 100 user_login filters per request.
    for start in range(0, len(logins), 100):
        batch = logins[start:start + 100]

        for attempt in range(2):
            token = await get_twitch_app_token(
                force_refresh=(attempt == 1)
            )

            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id": TWITCH_CLIENT_ID,
            }
            params = [
                ("user_login", username)
                for username in batch
            ]

            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
            ) as http:
                response = await http.get(
                    "https://api.twitch.tv/helix/streams",
                    params=params,
                    headers=headers,
                )

            if response.status_code == 401 and attempt == 0:
                # App tokens cannot be refreshed. Discard the cached token
                # and obtain a new one before retrying once.
                global twitch_app_token, twitch_app_token_expires_at
                twitch_app_token = None
                twitch_app_token_expires_at = 0.0
                continue

            response.raise_for_status()

            for stream in response.json().get("data", []):
                login = str(
                    stream.get("user_login") or ""
                ).strip().lower()

                if login:
                    results[login] = stream

            break

    return results


async def fetch_twitch_users(usernames):
    """Return Helix user-profile records keyed by lowercase Twitch login."""
    logins = []

    for username in usernames:
        username = str(username or "").strip().lower()
        if username and username not in logins:
            logins.append(username)

    if not logins:
        return {}

    results = {}

    # Helix Get Users accepts at most 100 login filters per request.
    for start in range(0, len(logins), 100):
        batch = logins[start:start + 100]

        for attempt in range(2):
            token = await get_twitch_app_token(
                force_refresh=(attempt == 1)
            )

            headers = {
                "Authorization": f"Bearer {token}",
                "Client-Id": TWITCH_CLIENT_ID,
            }
            params = [
                ("login", username)
                for username in batch
            ]

            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
            ) as http:
                response = await http.get(
                    "https://api.twitch.tv/helix/users",
                    params=params,
                    headers=headers,
                )

            if response.status_code == 401 and attempt == 0:
                global twitch_app_token, twitch_app_token_expires_at
                twitch_app_token = None
                twitch_app_token_expires_at = 0.0
                continue

            response.raise_for_status()

            for user in response.json().get("data", []):
                login = str(
                    user.get("login") or ""
                ).strip().lower()

                if login:
                    results[login] = user

            break

    return results


async def twitch_monitor():
    """Maintain Twitch LIVE state in live_djs using the official Helix API."""
    if not TWITCH_CLIENT_ID or not TWITCH_CLIENT_SECRET:
        print(
            "[Twitch] LIVE monitor disabled: "
            "TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET not configured"
        )
        return

    print("[Twitch] LIVE monitor starting...")

    # Let the database initialise and the existing TikTok jobs start first.
    await asyncio.sleep(7)

    while True:
        try:
            conn = get_db()
            try:
                rows = conn.execute("""
                    SELECT
                        username,
                        name,
                        live_url,
                        profile_pic
                    FROM favourite_djs
                    WHERE enabled = 1
                      AND platform = 'Twitch'
                    ORDER BY id ASC
                """).fetchall()
            finally:
                conn.close()

            enabled = {
                str(row["username"] or "").strip().lower(): row
                for row in rows
                if str(row["username"] or "").strip()
            }

            live_by_login = await fetch_twitch_live_streams(
                enabled.keys()
            )

            missing_profile_logins = [
                username
                for username, favourite in enabled.items()
                if not str(favourite["profile_pic"] or "").strip()
            ]
            twitch_profiles = await fetch_twitch_users(
                missing_profile_logins
            )

            now = datetime.now(timezone.utc).isoformat()
            conn = get_db()

            try:
                for username, profile in twitch_profiles.items():
                    profile_pic = str(
                        profile.get("profile_image_url") or ""
                    ).strip()
                    display_name = str(
                        profile.get("display_name") or username
                    ).strip()

                    if not profile_pic:
                        continue

                    conn.execute(
                        """
                        UPDATE favourite_djs
                        SET profile_pic = ?,
                            name = CASE
                                WHEN ? != '' THEN ?
                                ELSE name
                            END,
                            profile_updated_at = ?
                        WHERE platform = 'Twitch'
                          AND lower(username) = lower(?)
                        """,
                        (
                            profile_pic,
                            display_name,
                            display_name,
                            now,
                            username,
                        ),
                    )

                    print(
                        f"[Twitch] Profile picture refreshed for @{username}"
                    )

                existing_rows = conn.execute("""
                    SELECT username
                    FROM live_djs
                    WHERE platform = 'Twitch'
                """).fetchall()

                existing = {
                    str(row["username"] or "").strip().lower()
                    for row in existing_rows
                }

                # Remove Twitch live-state rows whose favourite was disabled
                # or deleted. A successful Helix round is authoritative here.
                for username in sorted(existing - set(enabled)):
                    conn.execute("""
                        DELETE FROM live_djs
                        WHERE platform = 'Twitch'
                          AND lower(username) = lower(?)
                    """, (username,))
                    print(
                        f"[Twitch] LIVE state removed for "
                        f"disabled/deleted @{username}"
                    )

                for username, favourite in enabled.items():
                    stream = live_by_login.get(username)

                    if stream is None:
                        if username in existing:
                            conn.execute("""
                                DELETE FROM live_djs
                                WHERE platform = 'Twitch'
                                  AND lower(username) = lower(?)
                            """, (username,))
                            print(
                                f"[Twitch] OFFLINE: @{username}"
                            )
                        continue

                    display_name = str(
                        stream.get("user_name")
                        or favourite["name"]
                        or username
                    ).strip()

                    live_url = str(
                        favourite["live_url"]
                        or f"https://www.twitch.tv/{username}"
                    ).strip()

                    try:
                        viewers = max(
                            0,
                            int(stream.get("viewer_count") or 0),
                        )
                    except (TypeError, ValueError):
                        viewers = 0

                    started_at = str(
                        stream.get("started_at") or now
                    ).strip()

                    conn.execute("""
                        INSERT INTO live_djs
                        (
                            username,
                            name,
                            platform,
                            url,
                            genre,
                            viewers,
                            started_at,
                            updated_at
                        )
                        VALUES (?, ?, 'Twitch', ?, ?, ?, ?, ?)

                        ON CONFLICT(platform, username) DO UPDATE SET
                            name = excluded.name,
                            url = excluded.url,
                            genre = excluded.genre,
                            viewers = excluded.viewers,
                            started_at = excluded.started_at,
                            updated_at = excluded.updated_at
                    """, (
                        username,
                        display_name,
                        live_url,
                        None,
                        viewers,
                        started_at,
                        now,
                    ))

                    if username not in existing:
                        print(
                            f"[Twitch] LIVE: @{username} "
                            f"({viewers} viewer(s))"
                        )

                conn.commit()
            finally:
                conn.close()

        except asyncio.CancelledError:
            raise

        except Exception as error:
            # API/network failures are not treated as OFFLINE. Existing rows
            # are left untouched and the normal freshness guard will age them
            # out if successful Twitch checks stop for an extended period.
            print(
                f"[Twitch] MONITOR ERROR: {error}"
            )

        await asyncio.sleep(
            TWITCH_CHECK_INTERVAL
        )


async def youtube_monitor():
    """Maintain YouTube LIVE state in live_djs using channel /live pages."""
    print("[YouTube] LIVE monitor starting...")
    await asyncio.sleep(YOUTUBE_START_DELAY)

    while True:
        try:
            conn = get_db()
            try:
                rows = conn.execute(
                    """
                    SELECT username, name, profile_url, live_url, profile_pic
                    FROM favourite_djs
                    WHERE enabled = 1
                      AND platform = 'YouTube'
                    ORDER BY id ASC
                    """
                ).fetchall()
            finally:
                conn.close()

            enabled = {
                str(row["username"] or "").strip().lower(): row
                for row in rows
                if str(row["username"] or "").strip()
            }

            # Remove rows for YouTube favourites that were disabled/deleted.
            conn = get_db()
            try:
                existing_rows = conn.execute(
                    "SELECT username FROM live_djs WHERE platform = 'YouTube'"
                ).fetchall()
                existing = {
                    str(row["username"] or "").strip().lower()
                    for row in existing_rows
                }
                for username in sorted(existing - set(enabled)):
                    conn.execute(
                        """
                        DELETE FROM live_djs
                        WHERE platform = 'YouTube'
                          AND lower(username) = lower(?)
                        """,
                        (username,),
                    )
                    print(
                        f"[YouTube] LIVE state removed for "
                        f"disabled/deleted @{username}"
                    )
                conn.commit()
            finally:
                conn.close()

            for username, favourite in enabled.items():
                live_url = str(
                    favourite["live_url"]
                    or (str(favourite["profile_url"] or "").rstrip("/") + "/live")
                ).strip()

                try:
                    info = await get_youtube_live_info(live_url)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    # One channel/network failure must not mark a known live DJ
                    # offline or prevent the other YouTube favourites being checked.
                    print(f"[YouTube] CHECK ERROR @{username}: {error}")
                    continue

                conn = get_db()
                try:
                    existing_row = conn.execute(
                        """
                        SELECT started_at
                        FROM live_djs
                        WHERE platform = 'YouTube'
                          AND lower(username) = lower(?)
                        """,
                        (username,),
                    ).fetchone()

                    if info is None:
                        if existing_row is not None:
                            conn.execute(
                                """
                                DELETE FROM live_djs
                                WHERE platform = 'YouTube'
                                  AND lower(username) = lower(?)
                                """,
                                (username,),
                            )
                            conn.commit()
                            print(f"[YouTube] OFFLINE: @{username}")
                        continue

                    now = datetime.now(timezone.utc).isoformat()
                    display_name = str(
                        info.get("channel")
                        or info.get("uploader")
                        or favourite["name"]
                        or username
                    ).strip()
                    watch_url = str(
                        info.get("webpage_url")
                        or info.get("original_url")
                        or live_url
                    ).strip()

                    try:
                        viewers = max(
                            0,
                            int(
                                info.get("concurrent_view_count")
                                if info.get("concurrent_view_count") is not None
                                else info.get("view_count") or 0
                            ),
                        )
                    except (TypeError, ValueError):
                        viewers = 0

                    started_at = existing_row["started_at"] if existing_row else None
                    if not started_at:
                        timestamp = info.get("release_timestamp") or info.get("timestamp")
                        try:
                            started_at = datetime.fromtimestamp(
                                float(timestamp), timezone.utc
                            ).isoformat()
                        except (TypeError, ValueError, OSError):
                            started_at = now

                    conn.execute(
                        """
                        INSERT INTO live_djs
                        (username, name, platform, url, genre, viewers, started_at, updated_at)
                        VALUES (?, ?, 'YouTube', ?, ?, ?, ?, ?)
                        ON CONFLICT(platform, username) DO UPDATE SET
                            name = excluded.name,
                            url = excluded.url,
                            genre = excluded.genre,
                            viewers = excluded.viewers,
                            started_at = excluded.started_at,
                            updated_at = excluded.updated_at
                        """,
                        (username, display_name, watch_url, None, viewers, started_at, now),
                    )

                    thumbnail = str(info.get("thumbnail") or "").strip()
                    title = str(info.get("title") or "").strip()
                    conn.execute(
                        """
                        UPDATE favourite_djs
                        SET name = ?,
                            profile_pic = CASE
                                WHEN ? != '' THEN ?
                                ELSE profile_pic
                            END,
                            live_title = ?,
                            profile_updated_at = ?
                        WHERE platform = 'YouTube'
                          AND lower(username) = lower(?)
                        """,
                        (display_name, thumbnail, thumbnail, title, now, username),
                    )
                    conn.commit()

                    if existing_row is None:
                        print(
                            f"[YouTube] LIVE: @{username} "
                            f"({viewers} viewer(s))"
                        )
                finally:
                    conn.close()

        except asyncio.CancelledError:
            raise
        except Exception as error:
            print(f"[YouTube] MONITOR ERROR: {error}")

        await asyncio.sleep(YOUTUBE_CHECK_INTERVAL)


async def capture_dj_test_audio(job_id, username, sample_seconds):
    """Capture a temporary candidate WAV without touching the DJ database."""
    wav_path = DJ_TEST_DIR / f"{job_id}.wav"

    try:
        DJ_TEST_DIR.mkdir(parents=True, exist_ok=True)

        async with dj_test_lock:
            job = dj_test_jobs.get(job_id)
            if not job:
                return
            job["status"] = "capturing"
            job["updated_at"] = datetime.now(timezone.utc).isoformat()

        stream_info = await get_tiktok_stream(username)

        headers = dict(stream_info.get("headers") or {})
        cookies = dict(stream_info.get("cookies") or {})

        header_lines = []
        for key, value in headers.items():
            if key.lower() not in ("connection", "content-length", "host"):
                header_lines.append(f"{key}: {value}")

        if cookies:
            header_lines.append(
                "Cookie: " + "; ".join(
                    f"{key}={value}" for key, value in cookies.items()
                )
            )

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "5",
            "-rw_timeout", "15000000",
        ]

        if header_lines:
            cmd += ["-headers", "\r\n".join(header_lines) + "\r\n"]

        cmd += [
            "-i", stream_info["url"],
            "-t", str(sample_seconds),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-f", "wav",
            str(wav_path),
        ]

        def run_ffmpeg():
            return subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=sample_seconds + 35,
                check=False,
            )

        result = await asyncio.to_thread(run_ffmpeg)

        if result.returncode != 0:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                f"FFmpeg failed with exit code {result.returncode}: "
                f"{error or 'no error output'}"
            )

        if not wav_path.exists():
            raise RuntimeError("FFmpeg did not create the candidate WAV")

        size = wav_path.stat().st_size
        if size < 10000:
            raise RuntimeError(
                f"Candidate WAV is too small to analyse ({size} bytes)"
            )

        async with dj_test_lock:
            job = dj_test_jobs.get(job_id)
            if not job:
                return
            job["status"] = "ready"
            job["audio_path"] = str(wav_path)
            job["audio_bytes"] = size
            job["updated_at"] = datetime.now(timezone.utc).isoformat()

        print(
            f"[DJ Test] Captured {size:,} bytes from @{username}; "
            "waiting for genre detector"
        )

    except Exception as error:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass

        async with dj_test_lock:
            job = dj_test_jobs.get(job_id)
            if job:
                job["status"] = "error"
                job["error"] = str(error)
                job["updated_at"] = datetime.now(timezone.utc).isoformat()

        print(f"[DJ Test] @{username} failed: {error}")


async def cleanup_dj_test_jobs():
    """Delete expired in-memory DJ test jobs and temporary WAV files."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=DJ_TEST_JOB_TTL)

    async with dj_test_lock:
        expired = []
        for job_id, job in dj_test_jobs.items():
            updated = _parse_iso_datetime(job.get("updated_at"))
            if updated is None or updated < cutoff:
                expired.append(job_id)

        for job_id in expired:
            job = dj_test_jobs.pop(job_id, None) or {}
            audio_path = job.get("audio_path")
            if audio_path:
                try:
                    Path(audio_path).unlink(missing_ok=True)
                except OSError:
                    pass


# ---------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------

async def manual_relay_cleanup_monitor():
    """Remove unused temporary DJ relays after a short idle period."""
    IDLE_SECONDS = 120
    while True:
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - IDLE_SECONDS
            async with manual_relay_lock:
                stale = []
                for key, relay in list(manual_relays.items()):
                    last_used = relay.get("last_used")
                    try:
                        ts = last_used.timestamp() if last_used else 0
                    except Exception:
                        ts = 0
                    if ts < cutoff:
                        stale.append((key, relay))
                for key, relay in stale:
                    manual_relays.pop(key, None)
                    process = relay.get("process")
                    try:
                        if process and process.poll() is None:
                            process.terminate()
                            await asyncio.to_thread(process.wait, timeout=3)
                    except Exception:
                        try:
                            if process:
                                process.kill()
                        except Exception:
                            pass
                    shutil.rmtree(relay.get("relay_dir"), ignore_errors=True)
                    print(f"[Relay] Cleaned up idle manual relay @{key}")
        except Exception as error:
            print(f"[Relay] Manual relay cleanup error: {error}")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):

    init_db()

    monitor_task = asyncio.create_task(
        tiktok_monitor()
    )

    twitch_monitor_task = asyncio.create_task(
        twitch_monitor()
    )

    youtube_monitor_task = asyncio.create_task(
        youtube_monitor()
    )

    profile_refresh_task = asyncio.create_task(
        profile_refresh_monitor()
    )

    relay_task = asyncio.create_task(
        relay_monitor()
    )

    manual_cleanup_task = asyncio.create_task(
        manual_relay_cleanup_monitor()
    )

    print(
        "[TikTok] Background monitor started"
    )
    print(
        "[TikTok] Daily profile refresh started"
    )
    print(
        "[Twitch] Background monitor started"
    )
    print(
        "[YouTube] Background monitor started"
    )
    print(
        "[Relay] Background relay started"
    )

    try:

        yield

    finally:

        monitor_task.cancel()
        twitch_monitor_task.cancel()
        youtube_monitor_task.cancel()
        profile_refresh_task.cancel()
        relay_task.cancel()
        manual_cleanup_task.cancel()

        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

        try:
            await twitch_monitor_task
        except asyncio.CancelledError:
            pass

        try:
            await youtube_monitor_task
        except asyncio.CancelledError:
            pass

        try:
            await profile_refresh_task
        except asyncio.CancelledError:
            pass

        try:
            await relay_task
        except asyncio.CancelledError:
            pass

        try:
            await manual_cleanup_task
        except asyncio.CancelledError:
            pass

        stop_relay(disconnect_audio_clients=True)
        stop_manual_relays()

        print(
            "[TikTok] Background monitor stopped"
        )
        print(
            "[Twitch] Background monitor stopped"
        )
        print(
            "[Relay] Background relay stopped"
        )


app = FastAPI(
    title="RadioRouteR",
    lifespan=lifespan,
)


# ---------------------------------------------------------
# CORS
# ---------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://radiorrr.com",
        "https://www.radiorrr.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------
# Basic API
# ---------------------------------------------------------

@app.get("/")
def root():

    return {
        "name": "RadioRouteR",
        "status": "online",
    }


# Engine liveness only: no Docker socket access or stream probes.
ENGINE_HEARTBEAT_DB = str(Path(DB_PATH).with_name("engine-heartbeats.db"))
ENGINE_HEARTBEAT_TTL = 90
ENGINE_WORKERS = ("genre-detector", "genre-candidate-scout")


def engine_heartbeat_connection():
    conn = sqlite3.connect(ENGINE_HEARTBEAT_DB, timeout=2)
    conn.execute("CREATE TABLE IF NOT EXISTS heartbeats (service TEXT PRIMARY KEY, seen REAL NOT NULL)")
    return conn


@app.post("/api/engine-heartbeat/{service}")
def receive_engine_heartbeat(service: str, request: Request):
    import secrets
    token = os.getenv("ENGINE_HEARTBEAT_TOKEN", "")
    if not token:
        raise HTTPException(status_code=503, detail="Engine reporting not configured")
    supplied = request.headers.get("Authorization", "")
    if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if service not in ENGINE_WORKERS:
        raise HTTPException(status_code=404, detail="Unknown engine")
    with closing(engine_heartbeat_connection()) as conn, conn:
        conn.execute("INSERT OR REPLACE INTO heartbeats (service, seen) VALUES (?, ?)", (service, time.time()))
    return {"ok": True}


@app.get("/api/engines")
def engine_status(response: Response):
    response.headers["Cache-Control"] = "no-store"
    now = time.time()
    with closing(engine_heartbeat_connection()) as conn, conn:
        seen = dict(conn.execute("SELECT service, seen FROM heartbeats"))
    engines = {"radiorouter": {"running": True}}
    for service in ENGINE_WORKERS:
        age = max(0, now - seen[service]) if service in seen else None
        engines[service] = {"running": age is not None and age <= ENGINE_HEARTBEAT_TTL}
    return {"engines": engines}


@app.get("/health")
def health():

    return {
        "status": "ok",
    }


# Anonymous browser IDs are retained only while playback heartbeats are fresh.
VIDEO_VIEWER_TTL = 75
VIDEO_VIEWER_DB = str(Path(DB_PATH).with_name("video-viewers.db"))


class VideoViewerHeartbeat(BaseModel):
    viewer_id: UUID
    session_id: UUID
    active: bool


def update_video_viewers(heartbeat=None):
    now = time.time()
    with closing(sqlite3.connect(VIDEO_VIEWER_DB, timeout=5)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS viewers (viewer_id TEXT NOT NULL, session_id TEXT NOT NULL, seen REAL NOT NULL, PRIMARY KEY (viewer_id, session_id))")
        conn.execute("CREATE INDEX IF NOT EXISTS viewers_seen ON viewers (seen)")
        conn.execute("DELETE FROM viewers WHERE seen <= ?", (now - VIDEO_VIEWER_TTL,))
        if heartbeat is not None:
            key = (str(heartbeat.viewer_id), str(heartbeat.session_id))
            if heartbeat.active:
                conn.execute("INSERT OR REPLACE INTO viewers VALUES (?, ?, ?)", (*key, now))
            else:
                conn.execute("DELETE FROM viewers WHERE viewer_id = ? AND session_id = ?", key)
        return conn.execute("SELECT COUNT(DISTINCT viewer_id) FROM viewers").fetchone()[0]


@app.post("/api/video-viewers/heartbeat")
def video_viewer_heartbeat(heartbeat: VideoViewerHeartbeat, request: Request):
    if request.headers.get("origin") not in {"https://radiorrr.com", "https://www.radiorrr.com"}:
        raise HTTPException(status_code=403, detail="Website origin required")
    update_video_viewers(heartbeat)
    return Response(status_code=204)


@app.get("/api/status")
async def api_status():
    """Read-only public status for the Radio RRR Tools panel."""
    video_healthy = (
        relay_process is not None
        and relay_process.poll() is None
        and bool(relay_username)
        and relay_output_is_healthy(
            RELAY_PLAYLIST,
            RELAY_DIR,
        )
    )

    audio_healthy = (
        video_healthy
        and audio_process is not None
        and audio_process.poll() is None
    )

    async with audio_clients_lock:
        listener_count = len(audio_clients)

    try:
        website_viewers = await asyncio.to_thread(update_video_viewers)
    except sqlite3.Error:
        website_viewers = None

    return {
        "status": "ok",
        "relay_username": relay_username,
        "relay_platform": relay_platform,
        "video": {
            "healthy": video_healthy,
            "playlist_ready": RELAY_PLAYLIST.exists(),
            "website_viewers": website_viewers,
        },
        "audio": {
            "healthy": audio_healthy,
            "listeners": listener_count,
            "bitrate_kbps": 128,
        },
    }


@app.get("/radio.mp3")
async def radio_mp3():
    """Continuous shared 128-kbps MP3 internet radio stream."""
    if relay_process is None or relay_process.poll() is not None:
        return PlainTextResponse(
            "Radio RRR is currently offline",
            status_code=503,
        )

    start_audio_relay()

    queue = asyncio.Queue(maxsize=AUDIO_QUEUE_SIZE)

    async with audio_clients_lock:
        audio_clients.add(queue)

    async def stream_audio():
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        except asyncio.CancelledError:
            raise
        finally:
            async with audio_clients_lock:
                audio_clients.discard(queue)

    return StreamingResponse(
        stream_audio(),
        media_type="audio/mpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "icy-name": "Radio RRR",
            "icy-description": "Radio RRR Live",
            "icy-br": "128",
        },
    )

@app.post("/api/live/switch")
async def switch_live_relay(dj: str = None, platform: str = None):
    """
    Switch the authoritative Radio RRR relay to a specific live DJ.

    The requested DJ is started in an isolated candidate relay and fully
    validated before promotion, so the existing station relay stays on air
    if the candidate cannot be opened or does not produce healthy HLS.
    """
    username = str(dj or "").lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="DJ username is required")

    requested_platform = str(platform or "TikTok").strip()
    platform_key = requested_platform.casefold()
    if platform_key not in {"tiktok", "twitch", "youtube"}:
        raise HTTPException(status_code=400, detail="Unsupported DJ platform")

    key = relay_identity_key(requested_platform, username)

    async with relay_lock:
        live_djs = await get_live_djs()
        live_dj = next(
            (
                item for item in live_djs
                if relay_identity_key(
                    item.get("platform") or "TikTok",
                    item.get("username"),
                ) == key
            ),
            None,
        )

        if not live_dj:
            raise HTTPException(status_code=404, detail="DJ is no longer live")

        current_username = str(relay_username or "").lstrip("@").strip()
        current_key = relay_identity_key(
            relay_platform or "TikTok",
            current_username,
        )

        if (
            current_key == key
            and relay_process is not None
            and relay_process.poll() is None
            and relay_output_is_healthy(RELAY_PLAYLIST, RELAY_DIR)
        ):
            return {
                "ok": True,
                "relay": live_dj,
                "message": "DJ is already the active Radio RRR relay",
            }

        try:
            print(
                f"[Relay] Manual global switch requested: "
                f"{requested_platform} @{username}"
            )

            stream_info = await get_platform_stream(
                username,
                requested_platform,
            )
            await validate_and_promote_relay(
                stream_info,
                username,
                requested_platform,
            )

            print(
                f"[Relay] Global relay switched and validated for "
                f"{requested_platform} @{username}"
            )

            return {
                "ok": True,
                "relay": live_dj,
                "message": (
                    f"Radio RRR relay switched to "
                    f"{requested_platform} @{username}"
                ),
            }

        except Exception as error:
            # validate_and_promote_relay() leaves the previous relay untouched
            # when candidate startup/validation fails.
            print(
                f"[Relay] Manual global switch failed for "
                f"{requested_platform} @{username}: {error}"
            )
            raise HTTPException(
                status_code=503,
                detail=str(error),
            )


@app.get("/api/live-stream")
async def live_stream(dj: str = None, platform: str = None):
    if dj:
        requested_platform = str(platform or "TikTok").strip()
        platform_key = requested_platform.casefold()

        if platform_key not in {"tiktok", "twitch", "youtube"}:
            return PlainTextResponse(
                "Unsupported DJ platform",
                status_code=400,
            )

        try:
            relay = await ensure_manual_relay(
                dj,
                requested_platform,
            )
        except Exception as error:
            return PlainTextResponse(str(error), status_code=503)

        playlist_path = relay["playlist"]
        encoded_dj = quote(dj.lstrip("@").strip(), safe="")

        if platform_key == "tiktok":
            # Preserve the existing public URL shape for TikTok clients.
            playlist_prefix = f"/api/live-stream/{encoded_dj}/"
        else:
            playlist_prefix = (
                f"/api/live-stream/platform/{quote(requested_platform, safe='')}/"
                f"{encoded_dj}/"
            )
    else:
        playlist_path = RELAY_PLAYLIST
        playlist_prefix = "/api/live-stream/"

    if not playlist_path.exists():
        return PlainTextResponse(
            "#EXTM3U\n#EXT-X-VERSION:3\n",
            media_type="application/vnd.apple.mpegurl",
            status_code=503,
        )

    if not dj and not relay_output_is_healthy(
        RELAY_PLAYLIST,
        RELAY_DIR,
    ):
        return PlainTextResponse(
            "Radio RRR relay is not ready",
            status_code=503,
        )

    playlist = playlist_path.read_text()

    if dj:
        lines = []
        for line in playlist.splitlines():
            if line.endswith(".ts") and not line.startswith("/"):
                line = playlist_prefix + line
            lines.append(line)
        playlist = "\n".join(lines) + "\n"
    else:
        playlist = rewrite_public_hls_playlist(
            playlist,
            playlist_prefix,
        )

    return Response(
        content=playlist,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/api/live-stream/{filename}")
def live_stream_segment(filename: str):
    safe_name = Path(filename).name
    target = RELAY_DIR / safe_name

    # Read the segment before constructing the response. FileResponse opens
    # the path later, which leaves a race where FFmpeg can delete the segment
    # after the exists() check but before Starlette opens it, producing HTTP
    # 500 instead of a clean 404 and causing HLS.js to stall.
    try:
        segment = target.read_bytes()
    except (FileNotFoundError, IsADirectoryError, OSError):
        return PlainTextResponse("Not found", status_code=404)

    return Response(
        content=segment,
        media_type="video/mp2t",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/api/live-stream/{dj}/{filename}")
def manual_live_stream_segment(dj: str, filename: str):
    # Backward-compatible TikTok segment route.
    relay = manual_relays.get(f"tiktok:{dj.lower()}")
    if not relay:
        return PlainTextResponse("Not found", status_code=404)

    relay["last_used"] = datetime.now(timezone.utc)
    safe_name = Path(filename).name
    target = relay["relay_dir"] / safe_name
    if not target.exists() or not target.is_file():
        return PlainTextResponse("Not found", status_code=404)

    return FileResponse(
        target,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/api/live-stream/platform/{platform}/{dj}/{filename}")
def manual_platform_live_stream_segment(
    platform: str,
    dj: str,
    filename: str,
):
    platform_key = str(platform or "").casefold()
    if platform_key not in {"tiktok", "twitch", "youtube"}:
        return PlainTextResponse("Not found", status_code=404)

    relay = manual_relays.get(
        f"{platform_key}:{dj.lower()}"
    )
    if not relay:
        return PlainTextResponse("Not found", status_code=404)

    relay["last_used"] = datetime.now(timezone.utc)
    safe_name = Path(filename).name
    target = relay["relay_dir"] / safe_name
    if not target.exists() or not target.is_file():
        return PlainTextResponse("Not found", status_code=404)

    return FileResponse(
        target,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


# ---------------------------------------------------------
# AI Genre Detection API
# ---------------------------------------------------------



# ---------------------------------------------------------
# Public BPM detector tool API
# ---------------------------------------------------------

@app.post("/api/tools/bpm")
async def public_bpm_detector(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON request")

    try:
        stream_url = await asyncio.to_thread(
            _validate_public_stream_url,
            data.get("url"),
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    BPM_TOOL_DIR.mkdir(parents=True, exist_ok=True)
    job_id = uuid4().hex
    wav_path = BPM_TOOL_DIR / f"{job_id}.wav"

    try:
        async with bpm_tool_semaphore:
            bpm, confidence = await asyncio.to_thread(
                _capture_and_detect_bpm,
                stream_url,
                wav_path,
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="The stream timed out while sampling audio")
    except RuntimeError as error:
        raise HTTPException(status_code=422, detail=str(error))
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "ok": True,
        "bpm": bpm,
        "confidence": confidence,
        "sample_seconds": BPM_TOOL_SAMPLE_SECONDS,
    }


# ---------------------------------------------------------
# Admin DJ candidate test API
# ---------------------------------------------------------

@app.post("/api/admin/dj-test")
async def admin_start_dj_test(request: Request):
    """Start a temporary TikTok candidate test without adding the DJ."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON request")

    username = str(data.get("username") or "").strip().lstrip("@")
    if not username:
        raise HTTPException(status_code=400, detail="TikTok username is required")

    # Conservative username validation: TikTok usernames use letters, digits,
    # underscores and periods. Reject arbitrary URL/path input here.
    if not re.fullmatch(r"[A-Za-z0-9._]{2,32}", username):
        raise HTTPException(status_code=400, detail="Invalid TikTok username")

    try:
        sample_seconds = int(data.get("sample_seconds") or DJ_TEST_SAMPLE_SECONDS)
    except (TypeError, ValueError):
        sample_seconds = DJ_TEST_SAMPLE_SECONDS

    sample_seconds = max(
        10,
        min(sample_seconds, DJ_TEST_MAX_SAMPLE_SECONDS),
    )

    await cleanup_dj_test_jobs()

    job_id = uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    async with dj_test_lock:
        dj_test_jobs[job_id] = {
            "id": job_id,
            "username": username,
            "sample_seconds": sample_seconds,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "genres": [],
            "error": None,
        }

    asyncio.create_task(
        capture_dj_test_audio(job_id, username, sample_seconds)
    )

    return {
        "ok": True,
        "job_id": job_id,
        "username": username,
        "sample_seconds": sample_seconds,
        "status": "queued",
    }


@app.get("/api/admin/dj-test/{job_id}")
async def admin_get_dj_test(job_id: str):
    await cleanup_dj_test_jobs()

    async with dj_test_lock:
        job = dj_test_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="DJ test job not found")

        return {
            key: value
            for key, value in job.items()
            if key != "audio_path"
        }


@app.get("/api/admin/dj-test-jobs/next")
async def admin_next_dj_test_job():
    """Claim the oldest ready candidate WAV for the detector container."""
    await cleanup_dj_test_jobs()
    now = datetime.now(timezone.utc)

    async with dj_test_lock:
        # Recover a detector claim if the detector disappeared mid-analysis.
        for job in dj_test_jobs.values():
            if job.get("status") != "analysing":
                continue
            claimed_at = _parse_iso_datetime(job.get("claimed_at"))
            if claimed_at and (now - claimed_at).total_seconds() > 120:
                job["status"] = "ready"
                job["claimed_at"] = None

        ready = sorted(
            (
                job for job in dj_test_jobs.values()
                if job.get("status") == "ready"
            ),
            key=lambda item: item.get("created_at") or "",
        )

        if not ready:
            return {"job": None}

        job = ready[0]
        job["status"] = "analysing"
        job["claimed_at"] = now.isoformat()
        job["updated_at"] = now.isoformat()

        return {
            "job": {
                "id": job["id"],
                "username": job["username"],
                "sample_seconds": job["sample_seconds"],
                "audio_url": (
                    f"/api/admin/dj-test-audio/{job['id']}"
                ),
            }
        }


@app.get("/api/admin/dj-test-audio/{job_id}")
async def admin_dj_test_audio(job_id: str):
    async with dj_test_lock:
        job = dj_test_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="DJ test job not found")

        audio_path = job.get("audio_path")

    if not audio_path:
        raise HTTPException(status_code=404, detail="Candidate audio is not ready")

    path = Path(audio_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Candidate audio file is missing")

    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/admin/dj-test-jobs/{job_id}/result")
async def admin_dj_test_result(job_id: str, request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON request")

    genres = data.get("genres")
    error = str(data.get("error") or "").strip() or None

    if not isinstance(genres, list):
        genres = []

    async with dj_test_lock:
        job = dj_test_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="DJ test job not found")

        job["genres"] = genres
        job["error"] = error
        job["status"] = "error" if error else "complete"
        job["updated_at"] = datetime.now(timezone.utc).isoformat()
        audio_path = job.get("audio_path")
        job["audio_path"] = None

    if audio_path:
        try:
            Path(audio_path).unlink(missing_ok=True)
        except OSError:
            pass

    if error:
        print(f"[DJ Test] @{job['username']} detector error: {error}")
    else:
        print(
            f"[DJ Test] @{job['username']} complete "
            f"({len(genres)} genre result(s))"
        )

    return {"ok": True}


@app.post("/api/admin/freeze-test/start")
async def admin_start_freeze_test():
    """Start a passive event-loop/HLS diagnostic for the DJ admin page."""
    now_monotonic = time.monotonic()
    now_iso = datetime.now(timezone.utc).isoformat()

    with freeze_test_lock:
        already_active = bool(freeze_test_state["active"])

        if not already_active:
            freeze_test_state["active"] = True
            freeze_test_state["started_at"] = now_iso
            freeze_test_state["finished_at"] = None
            freeze_test_state["ends_monotonic"] = (
                now_monotonic + FREEZE_TEST_DURATION
            )
            freeze_test_state["last_tick_monotonic"] = now_monotonic
            freeze_test_state["loop_thread_id"] = threading.get_ident()
            freeze_test_state["events"] = []
            freeze_test_state["current_event"] = None
            loop_thread_id = freeze_test_state["loop_thread_id"]

    if already_active:
        return _freeze_test_public_state()

    asyncio.create_task(_freeze_test_heartbeat())

    threading.Thread(
        target=_freeze_test_watchdog,
        args=(loop_thread_id,),
        name="rrr-freeze-watchdog",
        daemon=True,
    ).start()

    print(
        f"[Freeze Test] Started {FREEZE_TEST_DURATION}s diagnostic "
        f"(block threshold {FREEZE_TEST_BLOCK_THRESHOLD:.1f}s)"
    )

    return _freeze_test_public_state()


@app.post("/api/admin/freeze-test/stop")
async def admin_stop_freeze_test():
    """Stop the current passive freeze diagnostic early."""
    now_monotonic = time.monotonic()

    with freeze_test_lock:
        if freeze_test_state["active"]:
            _freeze_test_finish_current_locked(now_monotonic)
            freeze_test_state["active"] = False
            freeze_test_state["finished_at"] = datetime.now(
                timezone.utc
            ).isoformat()

    print("[Freeze Test] Stopped")
    return _freeze_test_public_state()


@app.get("/api/admin/freeze-test")
async def admin_get_freeze_test():
    """Return the current freeze-test results and relay health snapshot."""
    return _freeze_test_public_state()


# ---------------------------------------------------------
# Admin DJ management API
# ---------------------------------------------------------

def _admin_dj_account(value):
    """Accept a TikTok/Twitch/YouTube username or URL and return DJ identity."""
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(
            status_code=400,
            detail="DJ username or TikTok/Twitch/YouTube profile URL is required",
        )

    # TikTok profile/LIVE URL.
    match = re.search(
        r"(?:https?://)?(?:www\.)?tiktok\.com/@([^/?#]+)",
        raw,
        re.IGNORECASE,
    )
    if match:
        username = match.group(1).strip()
        if not re.fullmatch(r"[A-Za-z0-9._]{2,32}", username):
            raise HTTPException(
                status_code=400,
                detail="Invalid TikTok username or profile URL",
            )
        return {
            "platform": "TikTok",
            "username": username,
            "profile_url": f"https://www.tiktok.com/@{username}",
            "live_url": f"https://www.tiktok.com/@{username}/live",
        }

    # Twitch channel URL. Strip common non-channel paths defensively.
    match = re.search(
        r"(?:https?://)?(?:www\.)?twitch\.tv/([^/?#]+)",
        raw,
        re.IGNORECASE,
    )
    if match:
        username = match.group(1).strip().lower()
        if username in {"directory", "downloads", "jobs", "p", "settings", "videos"}:
            raise HTTPException(
                status_code=400,
                detail="That Twitch URL does not point to a channel",
            )
        if not re.fullmatch(r"[A-Za-z0-9_]{2,25}", username):
            raise HTTPException(
                status_code=400,
                detail="Invalid Twitch username or channel URL",
            )
        channel_url = f"https://www.twitch.tv/{username}"
        return {
            "platform": "Twitch",
            "username": username,
            "profile_url": channel_url,
            "live_url": channel_url,
        }

    # YouTube handle/channel URL. These are permanent identities and do not
    # require a network lookup just to add the favourite.
    match = re.search(
        r"(?:https?://)?(?:www\.)?(?:youtube\.com|youtube-nocookie\.com)/@([^/?#]+)",
        raw,
        re.IGNORECASE,
    )
    if match:
        username = match.group(1).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{2,100}", username):
            raise HTTPException(status_code=400, detail="Invalid YouTube handle URL")
        channel_url = f"https://www.youtube.com/@{username}"
        return {
            "platform": "YouTube",
            "username": username,
            "profile_url": channel_url,
            "live_url": channel_url + "/live",
        }

    match = re.search(
        r"(?:https?://)?(?:www\.)?youtube\.com/channel/([^/?#]+)",
        raw,
        re.IGNORECASE,
    )
    if match:
        username = match.group(1).strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", username):
            raise HTTPException(status_code=400, detail="Invalid YouTube channel URL")
        channel_url = f"https://www.youtube.com/channel/{username}"
        return {
            "platform": "YouTube",
            "username": username,
            "profile_url": channel_url,
            "live_url": channel_url + "/live",
        }

    # A pasted YouTube video/live URL is resolved asynchronously by the admin
    # endpoints so the permanent favourite becomes its owning channel.
    if re.search(
        r"(?:https?://)?(?:www\.)?(?:youtube\.com/(?:watch\?|live/)|youtu\.be/)",
        raw,
        re.IGNORECASE,
    ):
        return {
            "platform": "YouTube",
            "username": "",
            "profile_url": raw,
            "live_url": raw,
            "source_url": raw,
            "needs_resolve": True,
        }

    # Keep bare handles backwards-compatible: a plain @name/name means TikTok.
    username = (
        raw.lstrip("@")
        .split("/", 1)[0]
        .split("?", 1)[0]
        .split("#", 1)[0]
        .strip()
    )
    if not re.fullmatch(r"[A-Za-z0-9._]{2,32}", username):
        raise HTTPException(
            status_code=400,
            detail=(
                "Enter a valid TikTok handle or paste a TikTok/Twitch/YouTube "
                "channel URL"
            ),
        )

    return {
        "platform": "TikTok",
        "username": username,
        "profile_url": f"https://www.tiktok.com/@{username}",
        "live_url": f"https://www.tiktok.com/@{username}/live",
    }


@app.get("/api/admin/djs")
def admin_list_djs():
    """Return enabled and disabled Radio RRR DJs for the LAN admin page."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT username, name, platform, genre, enabled, created_at,
                   bio, genre_keywords, followers, following, profile_pic,
                   verified, live_title, profile_updated_at
            FROM favourite_djs
            ORDER BY enabled DESC, name COLLATE NOCASE, username COLLATE NOCASE
        """).fetchall()
    finally:
        conn.close()

    return {
        "djs": [
            {
                **dict(row),
                "enabled": bool(row["enabled"]),
            }
            for row in rows
        ]
    }


@app.post("/api/admin/djs/add")
async def admin_add_dj(request: Request):
    """Add or re-enable a TikTok or Twitch DJ without doing a network lookup."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON request")

    account = _admin_dj_account(data.get("username"))
    if account.get("platform") == "YouTube" and account.get("needs_resolve"):
        account = await resolve_youtube_account(account)
    platform = account["platform"]
    username = account["username"]
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    try:
        existing = conn.execute(
            """
            SELECT username, name, platform, enabled
            FROM favourite_djs
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        ).fetchone()

        if existing is None:
            actual_username = username

            conn.execute(
                """
                INSERT INTO favourite_djs
                (
                    username,
                    name,
                    platform,
                    profile_url,
                    live_url,
                    enabled,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    actual_username,
                    account.get("name") or actual_username,
                    platform,
                    account["profile_url"],
                    account["live_url"],
                    now,
                ),
            )
            action = "added"
            message = (
                f"{platform} @{actual_username} was added to Radio RRR."
            )
        else:
            actual_username = existing["username"]
            was_enabled = bool(existing["enabled"])

            if not was_enabled:
                conn.execute(
                    """
                    UPDATE favourite_djs
                    SET enabled = 1
                    WHERE lower(username) = lower(?)
                      AND lower(platform) = lower(?)
                    """,
                    (actual_username, platform),
                )
                action = "re_enabled"
                message = (
                    f"{platform} @{actual_username} already existed and has been "
                    "re-enabled. Existing profile and genre history was preserved."
                )
            else:
                action = "already_enabled"
                message = (
                    f"{platform} @{actual_username} was already enabled. "
                    "No changes were needed."
                )

        if platform == "TikTok":
            _ensure_tiktok_stats_row(
                conn,
                actual_username,
                now,
            )
            conn.execute(
                """
                UPDATE tiktok_live_stats
                SET auto_dormant = 0,
                    next_check_at = ?,
                    last_result = 'manual_enabled'
                WHERE lower(username) = lower(?)
                """,
                (now, actual_username),
            )

        conn.commit()

    except sqlite3.Error as error:
        conn.rollback()
        print(
            f"[Admin DJ] DATABASE ERROR "
            f"{platform} @{username}: {error}"
        )
        raise HTTPException(status_code=500, detail=f"Database error: {error}")
    finally:
        conn.close()

    return {
        "ok": True,
        "action": action,
        "message": message,
        "platform": platform,
        "username": actual_username,
        "profile_url": account["profile_url"],
        "live_url": account["live_url"],
        "previously_existed": existing is not None,
        "previously_enabled": (
            bool(existing["enabled"]) if existing is not None else False
        ),
    }


@app.post("/api/admin/djs/disable")
async def admin_disable_dj(request: Request):
    """Disable one platform-specific DJ while preserving stored profile data."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON request")

    account = _admin_dj_account(data.get("username"))
    if account.get("platform") == "YouTube" and account.get("needs_resolve"):
        account = await resolve_youtube_account(account)
    platform = account["platform"]
    username = account["username"]

    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT username, name, platform, enabled
            FROM favourite_djs
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"{platform} @{username} is not in the DJ database",
            )

        conn.execute(
            """
            UPDATE favourite_djs
            SET enabled = 0
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        )

        conn.execute(
            """
            DELETE FROM live_djs
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        )

        if platform == "TikTok":
            conn.execute(
                """
                UPDATE tiktok_live_stats
                SET auto_dormant = 0,
                    next_check_at = NULL,
                    last_result = 'manual_disabled'
                WHERE lower(username) = lower(?)
                """,
                (username,),
            )

        conn.commit()
    finally:
        conn.close()

    if platform == "TikTok":
        offline_miss_counts.pop(str(row["username"]), None)
        profile_picture_refresh_attempted.pop(str(row["username"]), None)

    return {
        "ok": True,
        "action": "disabled",
        "platform": row["platform"],
        "username": row["username"],
        "name": row["name"],
    }


@app.post("/api/admin/djs/delete")
async def admin_delete_dj(request: Request):
    """Permanently delete one platform-specific DJ after DELETE confirmation."""
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON request")

    account = _admin_dj_account(data.get("username"))
    if account.get("platform") == "YouTube" and account.get("needs_resolve"):
        account = await resolve_youtube_account(account)
    platform = account["platform"]
    username = account["username"]
    confirmation = str(data.get("confirmation") or "").strip()

    if confirmation != "DELETE":
        raise HTTPException(
            status_code=400,
            detail='Type DELETE to confirm permanent deletion',
        )

    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT username, name, platform
            FROM favourite_djs
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        ).fetchone()

        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"{platform} @{username} is not in the DJ database",
            )

        conn.execute(
            """
            DELETE FROM favourite_djs
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        )
        conn.execute(
            """
            DELETE FROM live_djs
            WHERE lower(username) = lower(?)
              AND lower(platform) = lower(?)
            """,
            (username, platform),
        )
        if platform == "TikTok":
            conn.execute(
                """
                DELETE FROM tiktok_live_stats
                WHERE lower(username) = lower(?)
                """,
                (username,),
            )
        conn.commit()
    finally:
        conn.close()

    if platform == "TikTok":
        offline_miss_counts.pop(str(row["username"]), None)
        profile_picture_refresh_attempted.pop(str(row["username"]), None)

    return {
        "ok": True,
        "action": "deleted",
        "platform": row["platform"],
        "username": row["username"],
        "name": row["name"],
    }


# ---------------------------------------------------------
# Scheduler admin
# ---------------------------------------------------------


ADMIN_HOME_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radio RRR — Admin</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#08090c;color:#f5f5f5}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#090a0e,#11131a);min-height:100vh}
.wrap{max-width:980px;margin:auto;padding:34px 20px 60px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:28px}
.brand{display:flex;align-items:center;gap:16px}.brand-logo{width:68px;height:68px;object-fit:contain;border-radius:12px;filter:drop-shadow(0 0 12px rgba(0,255,220,.18));background:#0b0d11;padding:5px}
h1{margin:0;font-size:31px;letter-spacing:-.5px}.sub{color:#9297a3;margin-top:5px}.pill{font-size:12px;color:#9da3b0;background:#181b23;border:1px solid #292e38;padding:7px 10px;border-radius:999px}
.menu{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.menu-card{display:block;text-decoration:none;color:inherit;background:#101218;border:1px solid #242934;border-radius:16px;padding:24px;min-height:190px;transition:.15s ease}
.menu-card:hover{transform:translateY(-2px);border-color:#3a4250;background:#14171e}.icon{font-size:30px;margin-bottom:18px}.menu-card h2{margin:0 0 8px;font-size:21px}.menu-card p{margin:0;color:#9297a3;line-height:1.55;font-size:14px}.meta{margin-top:17px;color:#cbd0d9;font-size:12px}
@media(max-width:700px){.menu{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand">
      <img class="brand-logo" src="https://radiorrr.com/logo.png" alt="Radio RRR logo">
      <div><h1>Radio RRR Admin</h1><div class="sub">Programming and DJ control centre</div></div>
    </div>
    <div class="pill">NAS admin</div>
  </div>

  <div class="menu">
    <a class="menu-card" href="/admin/djs">
      <div class="icon">🎧</div>
      <h2>DJ Management</h2>
      <p>Test TikTok LIVE candidates, add or re-enable DJs, disable them while keeping their data, or permanently delete a DJ record.</p>
      <div class="meta">Candidate test · Add · Disable · Delete</div>
    </a>

    <a class="menu-card" href="/admin/schedule">
      <div class="icon">📅</div>
      <h2>Scheduler</h2>
      <p>Edit Radio RRR programming blocks, target genres, weights and avoid-genres across the weekly schedule.</p>
      <div class="meta">Programs · Genres · Weights · Avoid rules</div>
    </a>
  </div>
</div>
</body>
</html>"""


DJ_ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radio RRR — DJ Management</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#08090c;color:#f5f5f5}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#090a0e,#11131a);min-height:100vh}
.wrap{max-width:1050px;margin:auto;padding:28px 20px 60px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:22px}
.brand{display:flex;align-items:center;gap:16px}.brand-logo{width:64px;height:64px;object-fit:contain;border-radius:12px;filter:drop-shadow(0 0 12px rgba(0,255,220,.18));background:#0b0d11;padding:5px}
.brand-copy h1{margin:0;font-size:30px;letter-spacing:-.5px}.sub{color:#9297a3;margin-top:5px}.pill{font-size:12px;color:#9da3b0;background:#181b23;border:1px solid #292e38;padding:7px 10px;border-radius:999px}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}.nav a{color:#c9cdd5;text-decoration:none;border:1px solid #292e38;background:#12151b;border-radius:9px;padding:9px 12px;font-size:13px}.nav a.active{background:#f5f5f5;color:#08090c;border-color:#f5f5f5}
.panel{background:#101218;border:1px solid #242934;border-radius:14px;padding:19px;margin-bottom:18px}.panel-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:7px}.panel h2{margin:0;font-size:18px}.copy{color:#9297a3;font-size:13px;line-height:1.5;margin:0 0 15px}
.form{display:grid;grid-template-columns:minmax(220px,1fr) 120px auto;gap:10px;align-items:end}.field label{display:block;font-size:11px;color:#8e94a0;text-transform:uppercase;letter-spacing:.08em;margin-bottom:6px}
.field input{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #303641;background:#0b0d11;color:#fff;font-size:15px}
.btn{border:1px solid #303641;background:#171a21;color:#f5f5f5;border-radius:9px;padding:10px 14px;cursor:pointer;text-decoration:none}.btn:hover{background:#20242d}.btn.primary{background:#f5f5f5;color:#08090c;border-color:#f5f5f5}.btn.warning{color:#ffd28a;border-color:#654c24}.btn.danger{color:#ff9d9d;border-color:#6b2638}.btn:disabled{opacity:.55;cursor:not-allowed}
.result{display:none;margin-top:16px;border-top:1px solid #242934;padding-top:15px}.result.show{display:block}.state{font-size:13px;color:#cdd1d9;margin-bottom:10px}.error{color:#ff9d9d}
.genres{display:grid;gap:7px}.genre{display:grid;grid-template-columns:minmax(180px,1fr) 70px;gap:12px;align-items:center;background:#171a21;border:1px solid #282d36;border-radius:8px;padding:9px 11px}.confidence{text-align:right;font-variant-numeric:tabular-nums;color:#9fd3ff}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.manage-grid{display:grid;grid-template-columns:minmax(220px,1fr) auto auto auto;gap:10px;align-items:end}.note{margin-top:12px;color:#858b97;font-size:12px;line-height:1.45}
.manage-detected{min-height:20px;margin-top:7px;font-size:12px;color:#9da3b0}.manage-detected strong{color:#dfe3ea}.manage-result{display:none;margin-top:14px;padding:12px 14px;border:1px solid #303641;border-radius:10px;background:#151820;font-size:13px;line-height:1.5}.manage-result.show{display:block}.manage-result.success{border-color:#285843;color:#a9ebc7;background:#101a16}.manage-result.info{border-color:#31506b;color:#acd8ff;background:#101820}.manage-result.warning{border-color:#6a5528;color:#ffd28a;background:#1b1710}.manage-result.error{border-color:#71303a;color:#ffb1b1;background:#1b1114}.manage-result .manage-result-title{font-weight:750;color:inherit}.manage-result .manage-result-detail{margin-top:3px;color:#aeb4bf}
.scanner-controls{display:grid;grid-template-columns:minmax(220px,1fr) auto auto;gap:10px;align-items:end}.scanner-note{padding:10px 12px;border:1px solid #2b313c;border-radius:9px;background:#151820;color:#aab0bb;font-size:12px;line-height:1.5;margin:12px 0}
.scanner-summary{display:flex;gap:7px;flex-wrap:wrap;margin:14px 0 10px}.scanner-results{display:grid;gap:10px}.scanner-empty{padding:15px;border:1px dashed #303641;border-radius:9px;color:#858b97;font-size:13px}
.scanner-result{border:1px solid #2a303a;border-radius:10px;background:#14171e;padding:13px}.scanner-result.high{border-color:#71303a}.scanner-result.medium{border-color:#6a5528}.scanner-result.low{border-color:#285843}
.scanner-result-head{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}.scanner-account{font-weight:750}.scanner-score{font-size:12px;font-weight:750}.scanner-score.high{color:#ff9d9d}.scanner-score.medium{color:#ffd28a}.scanner-score.low{color:#8ee5b6}
.scanner-verdict{margin:7px 0;color:#aeb4bf;font-size:12px;line-height:1.45}.scanner-reasons{margin:7px 0 0;padding-left:18px;color:#c9cdd5;font-size:12px;line-height:1.55}.scanner-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:11px}.scanner-actions a{display:inline-flex;align-items:center;border:1px solid #303641;background:#171a21;color:#dfe2e8;border-radius:8px;padding:7px 10px;text-decoration:none;font-size:12px}.scanner-actions button{font-size:12px;padding:7px 10px}
.diag-controls{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.diag-summary{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-top:14px}.diag-metric{background:#151820;border:1px solid #2b313c;border-radius:9px;padding:10px}.diag-metric .label{display:block;color:#858b97;font-size:10px;text-transform:uppercase;letter-spacing:.07em}.diag-metric .value{display:block;margin-top:4px;font-size:17px;font-weight:750;font-variant-numeric:tabular-nums}.diag-state{margin-top:12px;padding:11px 12px;border:1px solid #2b313c;border-radius:9px;background:#151820;color:#aeb4bf;font-size:12px;line-height:1.55}.diag-events{display:grid;gap:9px;margin-top:12px}.diag-event{border:1px solid #71303a;background:#1b1114;border-radius:9px;padding:11px}.diag-event-head{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:#ffb1b1;font-size:12px;font-weight:750}.diag-event-meta{margin-top:6px;color:#c9cdd5;font-size:11px;line-height:1.5}.diag-stack{margin:8px 0 0;max-height:180px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#090b0f;border:1px solid #292e38;border-radius:7px;padding:9px;color:#aeb4bf;font:11px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}.diag-good{color:#8ee5b6}.diag-bad{color:#ff9d9d}
.status{position:fixed;right:20px;bottom:20px;background:#171a21;border:1px solid #303641;padding:11px 15px;border-radius:10px;display:none;z-index:20}.status.show{display:block}
.overlay{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;padding:20px;z-index:10}.overlay.open{display:flex}.modal{width:min(500px,100%);background:#11141a;border:1px solid #303641;border-radius:16px;padding:22px}.modal h2{margin:0 0 10px}.modal p{color:#a7acb6;line-height:1.5}.modal-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:18px}.small{font-size:12px;color:#858b97}
@media(max-width:760px){.top{align-items:flex-start;flex-direction:column}.form,.manage-grid,.scanner-controls{grid-template-columns:1fr}.panel-head{align-items:flex-start;flex-direction:column}.scanner-result-head{align-items:flex-start;flex-direction:column}.diag-summary{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand"><img class="brand-logo" src="https://radiorrr.com/logo.png" alt="Radio RRR logo"><div class="brand-copy"><h1>Radio RRR DJ Management</h1><div class="sub">Candidate testing and DJ database controls</div></div></div>
    <div class="pill">SQLite DJ database</div>
  </div>

  <nav class="nav"><a href="/admin">← Admin Menu</a><a class="active" href="/admin/djs">DJ Management</a><a href="/admin/schedule">Scheduler</a></nav>

  <section class="panel" aria-labelledby="djTestTitle">
    <div class="panel-head"><h2 id="djTestTitle">🎧 DJ Candidate Test</h2><span class="pill">Test only · database unchanged</span></div>
    <p class="copy">Sample a TikTok LIVE account and run it through the existing Radio RRR AI genre detector before deciding whether to add it.</p>
    <div class="form">
      <div class="field"><label for="djTestUsername">TikTok username</label><input id="djTestUsername" placeholder="@djcaptain__" autocomplete="off"></div>
      <div class="field"><label for="djTestSeconds">Sample</label><input id="djTestSeconds" type="number" min="10" max="90" step="5" value="30"></div>
      <button class="btn primary" id="djTestButton" type="button" onclick="startDjTest()">Test DJ</button>
    </div>
    <div class="result" id="djTestResult">
      <div class="state" id="djTestState"></div>
      <div class="genres" id="djTestGenres"></div>
      <div class="actions" id="djTestActions"></div>
    </div>
  </section>

  <section class="panel" aria-labelledby="freezeTestTitle">
    <div class="panel-head"><h2 id="freezeTestTitle">🩺 Playback Freeze Test</h2><span class="pill">Passive · 60 seconds</span></div>
    <p class="copy">Watch the RadioRouter event loop and active HLS relay while the station keeps playing. If the server freezes for at least one second, the test captures the blocked Python stack and relay state so the exact cause can be identified.</p>
    <div class="diag-controls">
      <button class="btn primary" id="freezeStartButton" type="button" onclick="startFreezeTest()">Start 60s Test</button>
      <button class="btn" id="freezeStopButton" type="button" onclick="stopFreezeTest()" disabled>Stop</button>
      <button class="btn" id="freezeCopyButton" type="button" onclick="copyFreezeTest()" disabled>Copy Results</button>
    </div>
    <div class="diag-summary">
      <div class="diag-metric"><span class="label">Requests</span><span class="value" id="freezeRequests">0</span></div>
      <div class="diag-metric"><span class="label">Average</span><span class="value" id="freezeAverage">—</span></div>
      <div class="diag-metric"><span class="label">Maximum</span><span class="value" id="freezeMaximum">—</span></div>
      <div class="diag-metric"><span class="label">&gt;250 ms</span><span class="value" id="freezeSlow">0</span></div>
      <div class="diag-metric"><span class="label">&gt;1 sec</span><span class="value" id="freezeHard">0</span></div>
      <div class="diag-metric"><span class="label">Backend stalls</span><span class="value" id="freezeEvents">0</span></div>
    </div>
    <div class="diag-state" id="freezeState">Ready. Start the test while the live video/audio is playing.</div>
    <textarea id="freezeCopyOutput" style="display:none;width:100%;min-height:220px;margin-top:12px;background:#090b0f;color:#d7dbe3;border:1px solid #39414e;border-radius:9px;padding:10px;font:11px/1.45 ui-monospace,SFMono-Regular,Consolas,monospace" readonly></textarea>
    <div class="diag-events" id="freezeEventList"></div>
  </section>

  <section class="panel" aria-labelledby="djManageTitle">
    <div class="panel-head"><h2 id="djManageTitle">🛠️ DJ Management</h2><span class="pill">Radio RRR favourites</span></div>
    <p class="copy">Manage a TikTok, Twitch or YouTube DJ using the same operational meanings as the NAS commands: add/re-enable, disable while keeping data, or permanently delete the favourite/profile record.</p>
    <div class="manage-grid">
      <div class="field"><label for="manageUsername">DJ username or TikTok/Twitch/YouTube URL</label><input id="manageUsername" placeholder="@username or paste TikTok/Twitch/YouTube URL" autocomplete="off"><div class="manage-detected" id="manageDetected"></div></div>
      <button class="btn primary" id="addDjButton" type="button" onclick="addDj()">Add / Re-enable</button>
      <button class="btn warning" type="button" onclick="disableDj()">Disable</button>
      <button class="btn danger" type="button" onclick="confirmDeleteDj()">Delete</button>
    </div>
    <div class="manage-result" id="manageResult" role="status" aria-live="polite"></div>
    <div class="note"><strong>Tip:</strong> paste a TikTok profile/LIVE URL, Twitch channel URL, or YouTube channel/video URL and Radio RRR will detect the platform and account automatically. A bare @username is treated as TikTok. <strong>Disable</strong> keeps the DJ's database/profile data. <strong>Delete</strong> permanently removes the favourite/profile record and requires typing DELETE to confirm.</div>
  </section>

  <section class="panel" aria-labelledby="djScannerTitle">
    <div class="panel-head"><h2 id="djScannerTitle">🔍 DJ Account Scanner</h2><span class="pill">Bot-trait review</span></div>
    <p class="copy">Check Radio RRR DJs for publicly observable account signals that can look bot-like or suspicious.</p>
    <div class="scanner-note"><strong>Scanner:</strong> a high score is a review/removal suggestion, not proof that an account is fake. Always inspect the TikTok profile before disabling or deleting a DJ.</div>
    <div class="scanner-controls">
      <div class="field"><label for="scannerHandle">Single account check</label><input id="scannerHandle" placeholder="@username or TikTok profile URL" autocomplete="off"></div>
      <button class="btn" type="button" onclick="scanOneAccount()">Scan Account</button>
      <button class="btn primary" id="scanRrrDjs" type="button" onclick="scanRrrFavouriteDjs()">Scan DJ List</button>
    </div>
    <div class="actions"><button class="btn" type="button" onclick="clearScanner()">Clear Results</button></div>
    <div class="scanner-summary" id="scannerSummary"></div>
    <div class="scanner-results" id="scannerResults"><div class="scanner-empty">Click <strong>Scan DJ List</strong> to analyse the currently enabled Radio RRR DJs.</div></div>
  </section>
</div>

<div class="status" id="status"></div>
<div class="overlay" id="overlay"><div class="modal">
  <h2>Permanently delete DJ?</h2>
  <p id="deleteCopy"></p>
  <div class="field"><label for="deleteConfirmation">Type DELETE to confirm</label><input id="deleteConfirmation" autocomplete="off"></div>
  <div class="modal-actions"><button class="btn" type="button" onclick="closeDelete()">Cancel</button><button class="btn danger" type="button" onclick="deleteDj()">Delete permanently</button></div>
</div></div>

<script>
const $=id=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
async function api(url,opt){let r=await fetch(url,opt);if(!r.ok){let t=await r.text();try{const j=JSON.parse(t);throw new Error(j.detail||j.message||t)}catch(e){if(e instanceof SyntaxError)throw new Error(t);throw e}}return r.json()}
function toast(t){$('status').textContent=t;$('status').classList.add('show');setTimeout(()=>$('status').classList.remove('show'),2400)}
function normalizeTikTokHandle(input){let s=String(input||'').trim();if(!s)return '';try{if(/^https?:\/\//i.test(s)){const u=new URL(s);const m=u.pathname.match(/\/@([^/?#]+)/);if(m)s=m[1]}}catch(e){}return s.replace(/^@/,'').split(/[/?#]/)[0].trim()}
function usernameFrom(id){const u=normalizeTikTokHandle($(id).value);if(!u)throw new Error('Enter a TikTok username or profile URL.');if(!/^[A-Za-z0-9._]{2,32}$/.test(u))throw new Error('Could not find a valid TikTok username in that value.');return u}
function parseManageAccount(input){const raw=String(input||'').trim();if(!raw)throw new Error('Enter a DJ username or paste a TikTok/Twitch/YouTube URL.');let platform='TikTok',username='';try{if(/^https?:\/\//i.test(raw)){const u=new URL(raw);const host=u.hostname.toLowerCase().replace(/^www\./,'');if(host==='tiktok.com'){const m=u.pathname.match(/\/@([^/?#]+)/);if(!m)throw new Error('Could not find a TikTok username in that URL.');username=m[1]}else if(host==='twitch.tv'){username=u.pathname.split('/').filter(Boolean)[0]||'';platform='Twitch'}else if(host==='youtube.com'||host==='youtube-nocookie.com'||host==='youtu.be'){platform='YouTube';const hm=u.pathname.match(/^\/@([^/?#]+)/);const cm=u.pathname.match(/^\/channel\/([^/?#]+)/);if(hm)username=hm[1];else if(cm)username=cm[1];else if(host==='youtu.be'||u.pathname.startsWith('/watch')||u.pathname.startsWith('/live/'))username='video';else throw new Error('Could not identify a YouTube channel or video in that URL.')}else throw new Error('Only TikTok, Twitch and YouTube URLs are supported here.')}}catch(e){if(/^https?:\/\//i.test(raw))throw e}if(!username)username=raw.replace(/^@/,'').split(/[/?#]/)[0].trim();if(platform==='YouTube'&&username==='video')return{raw,platform,username};const valid=platform==='Twitch'?/^[A-Za-z0-9_]{2,25}$/:platform==='YouTube'?/^[A-Za-z0-9._-]{2,100}$/:/^[A-Za-z0-9._]{2,32}$/;if(!valid.test(username))throw new Error(`Could not find a valid ${platform} account in that value.`);if(platform==='Twitch')username=username.toLowerCase();return{raw,platform,username}}
function manageValue(){const value=$('manageUsername').value.trim();parseManageAccount(value);return value}
function syncManageUsername(username){$('manageUsername').value='@'+username;updateManageDetected()}
function updateManageDetected(){const box=$('manageDetected');try{const account=parseManageAccount($('manageUsername').value);box.innerHTML=`Detected: <strong>${esc(account.platform)} @${esc(account.username)}</strong>`}catch(e){box.textContent=$('manageUsername').value.trim()?e.message:''}}
function showManageResult(type,title,detail=''){const box=$('manageResult');box.className='manage-result show '+type;box.innerHTML=`<div class="manage-result-title">${esc(title)}</div>${detail?`<div class="manage-result-detail">${esc(detail)}</div>`:''}`}
function clearManageResult(){$('manageResult').className='manage-result';$('manageResult').innerHTML=''}

let freezeRunning=false,freezePollTimer=null,freezeLatencyTimer=null,freezeBackendState=null;
let freezeSamples={count:0,total:0,max:0,slow:0,hard:0};

function renderFreezeMetrics(){
  $('freezeRequests').textContent=String(freezeSamples.count);
  $('freezeAverage').textContent=freezeSamples.count?`${Math.round(freezeSamples.total/freezeSamples.count)} ms`:'—';
  $('freezeMaximum').textContent=freezeSamples.count?`${Math.round(freezeSamples.max)} ms`:'—';
  $('freezeSlow').textContent=String(freezeSamples.slow);
  $('freezeHard').textContent=String(freezeSamples.hard);
}

function renderFreezeState(data){
  freezeBackendState=data;
  const events=[...(data.events||[])];
  if(data.current_event)events.push(data.current_event);
  $('freezeEvents').textContent=String(events.length);

  const relay=data.relay||{};
  const segAge=relay.latest_segment_age_seconds==null?'—':`${Number(relay.latest_segment_age_seconds).toFixed(1)}s`;
  const loopLag=Number(data.event_loop_lag_seconds||0).toFixed(2);
  const remaining=Number(data.seconds_remaining||0).toFixed(1);
  const health=(relay.process_running&&relay.playlist_exists)?'<span class="diag-good">relay running</span>':'<span class="diag-bad">relay not healthy</span>';
  $('freezeState').innerHTML=data.active
    ? `Running · ${remaining}s remaining · event-loop lag ${loopLag}s · ${health} · @${esc(relay.username||'none')} · newest segment age ${segAge}`
    : `Finished · ${events.length} backend stall${events.length===1?'':'s'} captured · ${health} · @${esc(relay.username||'none')} · newest segment age ${segAge}`;

  $('freezeEventList').innerHTML=events.map((event,index)=>{
    const r=event.relay||{};
    const age=r.latest_segment_age_seconds==null?'—':`${Number(r.latest_segment_age_seconds).toFixed(2)}s`;
    const duration=Number(event.duration_seconds||event.detected_after_seconds||0).toFixed(2);
    return `<div class="diag-event">
      <div class="diag-event-head"><span>Stall ${index+1}</span><span>${duration}s</span></div>
      <div class="diag-event-meta">${esc(event.started_at||'')} · relay @${esc(r.username||'none')} · process ${r.process_running?'running':'stopped'} · latest segment age ${age}</div>
      <pre class="diag-stack">${esc(event.stack||'No stack captured.')}</pre>
    </div>`;
  }).join('');

  if(!data.active){
    freezeRunning=false;
    $('freezeStartButton').disabled=false;
    $('freezeStopButton').disabled=true;
    $('freezeCopyButton').disabled=false;
  }
}

async function sampleFreezeLatency(){
  if(!freezeRunning)return;
  const started=performance.now();
  try{
    const response=await fetch('/api/status?_freeze='+Date.now(),{cache:'no-store'});
    await response.text();
  }catch(e){}
  const elapsed=performance.now()-started;
  freezeSamples.count+=1;
  freezeSamples.total+=elapsed;
  freezeSamples.max=Math.max(freezeSamples.max,elapsed);
  if(elapsed>=250)freezeSamples.slow+=1;
  if(elapsed>=1000)freezeSamples.hard+=1;
  renderFreezeMetrics();
  if(freezeRunning)freezeLatencyTimer=setTimeout(sampleFreezeLatency,200);
}

async function pollFreezeTest(){
  if(!freezeRunning)return;
  try{
    const data=await api('/api/admin/freeze-test?_rrr='+Date.now());
    renderFreezeState(data);
    if(data.active)freezePollTimer=setTimeout(pollFreezeTest,750);
  }catch(e){
    $('freezeState').textContent=e.message||'Could not read freeze-test status.';
    if(freezeRunning)freezePollTimer=setTimeout(pollFreezeTest,1000);
  }
}

async function startFreezeTest(){
  if(freezeRunning)return;
  freezeSamples={count:0,total:0,max:0,slow:0,hard:0};
  freezeBackendState=null;
  renderFreezeMetrics();
  $('freezeEvents').textContent='0';
  $('freezeEventList').innerHTML='';
  $('freezeStartButton').disabled=true;
  $('freezeStopButton').disabled=false;
  $('freezeCopyButton').disabled=true;
  $('freezeState').textContent='Starting 60-second diagnostic…';
  try{
    const data=await api('/api/admin/freeze-test/start',{method:'POST'});
    freezeRunning=Boolean(data.active);
    renderFreezeState(data);
    if(freezeRunning){
      sampleFreezeLatency();
      pollFreezeTest();
    }
  }catch(e){
    freezeRunning=false;
    $('freezeStartButton').disabled=false;
    $('freezeStopButton').disabled=true;
    $('freezeState').textContent=e.message||'Could not start freeze test.';
  }
}

async function stopFreezeTest(){
  freezeRunning=false;
  if(freezePollTimer)clearTimeout(freezePollTimer);
  if(freezeLatencyTimer)clearTimeout(freezeLatencyTimer);
  try{
    const data=await api('/api/admin/freeze-test/stop',{method:'POST'});
    renderFreezeState(data);
  }catch(e){
    $('freezeState').textContent=e.message||'Could not stop freeze test.';
  }
}

async function copyFreezeTest(){
  let latest=freezeBackendState;
  try{latest=await api('/api/admin/freeze-test?_rrr='+Date.now())}catch(e){}
  const payload={
    browser_latency:{
      requests:freezeSamples.count,
      average_ms:freezeSamples.count?Number((freezeSamples.total/freezeSamples.count).toFixed(2)):null,
      maximum_ms:freezeSamples.count?Number(freezeSamples.max.toFixed(2)):null,
      over_250ms:freezeSamples.slow,
      over_1s:freezeSamples.hard
    },
    backend:latest
  };
  const output=JSON.stringify(payload,null,2);
  const box=$('freezeCopyOutput');
  box.value=output;

  try{
    if(navigator.clipboard&&window.isSecureContext){
      await navigator.clipboard.writeText(output);
      box.style.display='none';
      toast('Freeze-test results copied.');
      return;
    }
  }catch(e){}

  try{
    box.style.display='block';
    box.focus();
    box.select();
    box.setSelectionRange(0,box.value.length);
    if(document.execCommand('copy')){
      box.style.display='none';
      toast('Freeze-test results copied.');
      return;
    }
  }catch(e){}

  box.style.display='block';
  box.focus();
  box.select();
  box.setSelectionRange(0,box.value.length);
  $('freezeState').textContent='Clipboard access is blocked by this browser. Results are selected below — press Ctrl+C to copy them.';
}

let djTestJobId=null,djTestUsername=null,djTestTimer=null;
function djTestSetState(text,isError=false){$('djTestResult').classList.add('show');$('djTestState').className='state'+(isError?' error':'');$('djTestState').textContent=text}
function renderDjTestGenres(items){$('djTestGenres').innerHTML=(items||[]).map(item=>{const raw=String(item.genre||'');const label=raw.includes('---')?raw.split('---',2)[1]:raw;const confidence=Number(item.confidence||0)*100;return `<div class="genre"><span>${esc(label)}</span><span class="confidence">${confidence.toFixed(2)}%</span></div>`}).join('')}
async function startDjTest(){
  let username;try{username=usernameFrom('djTestUsername')}catch(e){return alert(e.message)}
  const sampleSeconds=Math.max(10,Math.min(90,Number($('djTestSeconds').value)||30));
  if(djTestTimer){clearTimeout(djTestTimer);djTestTimer=null}
  $('djTestButton').disabled=true;$('djTestGenres').innerHTML='';$('djTestActions').innerHTML='';syncManageUsername(username);djTestSetState(`Starting test for @${username}…`);
  try{const result=await api('/api/admin/dj-test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,sample_seconds:sampleSeconds})});djTestJobId=result.job_id;djTestUsername=result.username;pollDjTest()}
  catch(e){$('djTestButton').disabled=false;djTestSetState(e.message||'Could not start DJ test',true)}
}
async function pollDjTest(){
  if(!djTestJobId)return;
  try{
    const job=await api('/api/admin/dj-test/'+encodeURIComponent(djTestJobId));
    if(job.status==='queued'||job.status==='capturing')djTestSetState(`Sampling @${job.username}… this takes about ${job.sample_seconds}s.`);
    else if(job.status==='ready')djTestSetState('Audio captured. Waiting for the AI genre detector…');
    else if(job.status==='analysing')djTestSetState(`Analysing @${job.username} with the Radio RRR genre model…`);
    else if(job.status==='complete'){djTestSetState(`Test complete for @${job.username}. Nothing has been added to the database.`);renderDjTestGenres(job.genres||[]);$('djTestActions').innerHTML=`<button class="btn" type="button" onclick="startDjTest()">Test Again</button><button class="btn primary" type="button" onclick="addTestedDj()">Add DJ</button>`;$('djTestButton').disabled=false;djTestTimer=null;return}
    else if(job.status==='error'){djTestSetState(job.error||'DJ test failed.',true);$('djTestButton').disabled=false;djTestTimer=null;return}
    djTestTimer=setTimeout(pollDjTest,1500)
  }catch(e){djTestSetState(e.message||'Could not read DJ test status',true);$('djTestButton').disabled=false;djTestTimer=null}
}
async function addTestedDj(){if(!djTestUsername)return;$('manageUsername').value='@'+djTestUsername;await addDj()}
async function addDj(){let value,account;try{value=manageValue();account=parseManageAccount(value)}catch(e){showManageResult('error','Cannot add DJ',e.message);return}const button=$('addDjButton');button.disabled=true;showManageResult('info',`Adding ${account.platform} @${account.username}…`,'Updating the Radio RRR DJ database.');try{const result=await api('/api/admin/djs/add',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:value})});const actual=result.username||account.username;const platform=result.platform||account.platform;$('manageUsername').value=result.profile_url||value;updateManageDetected();if(result.action==='added')showManageResult('success',`Added ${platform} @${actual}`,result.message||'The DJ is now enabled in Radio RRR.');else if(result.action==='re_enabled')showManageResult('success',`Re-enabled ${platform} @${actual}`,result.message||'The existing DJ has been re-enabled.');else if(result.action==='already_enabled')showManageResult('warning',`${platform} @${actual} was already enabled`,result.message||'No enable-state change was required.');else showManageResult('success',`Updated ${platform} @${actual}`,result.message||'The DJ record was updated.');toast(result.message||`Updated ${platform} @${actual}`)}catch(e){showManageResult('error',`Could not add ${account.platform} @${account.username}`,e.message||'Unknown backend error.')}finally{button.disabled=false}}
async function disableDj(){let value,account;try{value=manageValue();account=parseManageAccount(value)}catch(e){showManageResult('error','Cannot disable DJ',e.message);return}if(!confirm(`Disable ${account.platform} @${account.username}? Their database/profile data will be kept.`))return;try{const result=await api('/api/admin/djs/disable',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:value})});showManageResult('warning',`Disabled ${result.platform} @${result.username}`,'The DJ remains in the database and can be re-enabled later.');toast(`Disabled ${result.platform} @${result.username}`)}catch(e){showManageResult('error',`Could not disable ${account.platform} @${account.username}`,e.message||'Unknown backend error.')}}
let deleteDjValue=null,deleteDjAccount=null;
function confirmDeleteDj(){try{deleteDjValue=manageValue();deleteDjAccount=parseManageAccount(deleteDjValue)}catch(e){return alert(e.message)}$('deleteCopy').textContent=`This permanently removes ${deleteDjAccount.platform} @${deleteDjAccount.username}'s favourite/profile database record.`;$('deleteConfirmation').value='';$('overlay').classList.add('open');setTimeout(()=>$('deleteConfirmation').focus(),0)}
function closeDelete(){$('overlay').classList.remove('open');deleteDjValue=null;deleteDjAccount=null}
async function deleteDj(){if(!deleteDjValue||!deleteDjAccount)return;const value=deleteDjValue,account=deleteDjAccount;const confirmation=$('deleteConfirmation').value.trim();if(confirmation!=='DELETE')return alert('Type DELETE exactly to confirm.');try{const result=await api('/api/admin/djs/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:value,confirmation})});closeDelete();$('manageUsername').value='';updateManageDetected();showManageResult('success',`Deleted ${result.platform} @${result.username}`,'The favourite/profile record was permanently removed.');toast(`Deleted ${result.platform} @${result.username}`)}catch(e){showManageResult('error',`Could not delete ${account.platform} @${account.username}`,e.message||'Unknown backend error.')}}

$('manageUsername').addEventListener('input',()=>{updateManageDetected();clearManageResult()});
$('manageUsername').addEventListener('paste',()=>setTimeout(updateManageDetected,0));

function scanAccountSignals(account){
  const handle=normalizeTikTokHandle(account?.username??account?.unique_id??account?.handle??account);let score=8;const reasons=[];
  if(!handle)return{handle:'',score:30,verdict:'Medium',level:'medium',reasons:['Could not extract a TikTok username.']};
  if(/\d{5,}$/.test(handle)){score+=28;reasons.push('Username ends with a large block of digits.')}else if(/\d{4}$/.test(handle)){score+=18;reasons.push('Username ends with four digits.')}
  const separators=(handle.match(/[._]/g)||[]).length;if(separators>=3){score+=10;reasons.push('Username contains many separators.')}
  if(handle.length<=4){score+=10;reasons.push('Very short username.')}
  if(/^(user|account|official|real|dj)\d+/i.test(handle)){score+=12;reasons.push('Generic/generated-looking username pattern.')}
  if(/(free|promo|airdrop|crypto|giveaway|cash|adult|sex|dm|whatsapp|telegram)/i.test(handle)){score+=25;reasons.push('Username contains a common spam/scam keyword.')}
  const followers=Number(account?.followers??account?.follower_count??account?.followerCount),following=Number(account?.following??account?.following_count??account?.followingCount),likes=Number(account?.likes??account?.like_count??account?.likeCount),videos=Number(account?.videos??account?.video_count??account?.videoCount);
  if(Number.isFinite(followers)&&Number.isFinite(following)){if(following>=5000&&followers<=200){score+=22;reasons.push('Very high following count with very few followers.')}else if(following>=2000&&followers<=50){score+=25;reasons.push('Extreme following/follower imbalance.')}if(following>500&&followers>0&&followers/following<0.02){score+=12;reasons.push('Extremely low follower/following ratio.')}}
  if(Number.isFinite(videos)){if(videos===0){score+=16;reasons.push('No public videos.')}else if(videos<=2){score+=8;reasons.push('Very small public content footprint.')}}
  if(Number.isFinite(likes)&&likes===0){score+=6;reasons.push('No visible likes/activity reported.')}
  if(reasons.length===0)reasons.push('No strong bot-like signals found from the available data.');
  score=Math.max(0,Math.min(100,score));let verdict='Low',level='low';if(score>=65){verdict='High';level='high'}else if(score>=35){verdict='Medium';level='medium'}return{handle,score,verdict,level,reasons}
}
function renderScannerSummary(results){const high=results.filter(r=>r.level==='high').length,medium=results.filter(r=>r.level==='medium').length,low=results.filter(r=>r.level==='low').length;$('scannerSummary').innerHTML=`<span class="pill">Scanned: ${results.length}</span><span class="pill">🔴 High risk: ${high}</span><span class="pill">🟠 Review: ${medium}</span><span class="pill">🟢 Low risk: ${low}</span>`}
function renderScannerResults(results){
  if(!results.length){$('scannerSummary').innerHTML='';$('scannerResults').innerHTML='<div class="scanner-empty">No accounts to scan.</div>';return}
  results.sort((a,b)=>b.score-a.score);renderScannerSummary(results);
  $('scannerResults').innerHTML=results.map(result=>{const reasons=result.reasons.map(r=>`<li>${esc(r)}</li>`).join('');const url='https://www.tiktok.com/@'+encodeURIComponent(result.handle);const verdict=result.level==='high'?'Strong enough signals to suggest manual removal review.':result.level==='medium'?'Some suspicious signals — inspect the profile before deciding.':'No strong bot-like signals detected from the available data.';return `<div class="scanner-result ${result.level}"><div class="scanner-result-head"><div class="scanner-account">@${esc(result.handle)}</div><div class="scanner-score ${result.level}">${result.score}/100 · ${esc(result.verdict)} RISK</div></div><div class="scanner-verdict">${verdict}</div><ul class="scanner-reasons">${reasons}</ul><div class="scanner-actions"><a href="${url}" target="_blank" rel="noopener">Open TikTok</a><button class="btn" type="button" onclick="loadScannerDj('${encodeURIComponent(result.handle)}')">Review in DJ Management</button></div></div>`}).join('')
}
function loadScannerDj(encoded){const handle=decodeURIComponent(encoded);$('manageUsername').value='@'+handle;toast(`Loaded @${handle} into DJ Management for manual review.`);$('manageUsername').scrollIntoView({behavior:'smooth',block:'center'})}
async function scanRrrFavouriteDjs(){$('scannerResults').innerHTML='<div class="scanner-empty">Loading the Radio RRR DJ list…</div>';$('scannerSummary').innerHTML='';try{const data=await api('/api/admin/djs');const enabled=(Array.isArray(data.djs)?data.djs:[]).filter(d=>d.enabled);const results=enabled.map(scanAccountSignals).filter(r=>r.handle);renderScannerResults(results);if(!results.length)$('scannerResults').innerHTML='<div class="scanner-empty">No enabled Radio RRR DJs were returned.</div>'}catch(e){$('scannerResults').innerHTML=`<div class="scanner-empty error">${esc(e.message||'Could not load the DJ list for scanning.')}</div>`}}
function scanOneAccount(){const handle=normalizeTikTokHandle($('scannerHandle').value);if(!handle){toast('Enter a TikTok @handle or profile URL first.');return}renderScannerResults([scanAccountSignals({username:handle})])}
function clearScanner(){$('scannerSummary').innerHTML='';$('scannerResults').innerHTML='<div class="scanner-empty">Scanner cleared. Click <strong>Scan DJ List</strong> to scan again.</div>'}
$('scannerHandle').addEventListener('keydown',e=>{if(e.key==='Enter')scanOneAccount()});
$('overlay').addEventListener('click',e=>{if(e.target.id==='overlay')closeDelete()});
</script>
</body>
</html>
"""


ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radio RRR — Scheduler Admin</title>
<style>
:root{font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#08090c;color:#f5f5f5}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#090a0e,#11131a);min-height:100vh}
.wrap{max-width:1250px;margin:auto;padding:28px 20px 60px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:24px}
.brand{display:flex;align-items:center;gap:16px}.brand-logo{width:64px;height:64px;object-fit:contain;border-radius:12px;filter:drop-shadow(0 0 12px rgba(0,255,220,.18));background:#0b0d11;padding:5px}.brand-copy h1{margin:0;font-size:30px;letter-spacing:-.5px}.sub{color:#9297a3;margin-top:5px}.pill{font-size:12px;color:#9da3b0;background:#181b23;border:1px solid #292e38;padding:7px 10px;border-radius:999px}
.tabs{display:flex;gap:8px;overflow:auto;padding-bottom:12px}.tab{border:1px solid #292e38;background:#12151b;color:#c9cdd5;border-radius:10px;padding:10px 15px;cursor:pointer;white-space:nowrap}.tab.active{background:#f5f5f5;color:#0b0c10;border-color:#f5f5f5}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 18px}.btn{border:1px solid #303641;background:#171a21;color:#f5f5f5;border-radius:9px;padding:10px 14px;cursor:pointer}.btn:hover{background:#20242d}.btn.primary{background:#f5f5f5;color:#08090c;border-color:#f5f5f5}.btn.danger{color:#ff9d9d}
.card{background:#101218;border:1px solid #242934;border-radius:14px;overflow:hidden}.row{display:grid;grid-template-columns:155px minmax(180px,1fr) minmax(260px,1.5fr) 90px;gap:18px;align-items:center;padding:17px 18px;border-top:1px solid #242934}.row:first-child{border-top:0}.head{background:#151820;color:#7f8591;font-size:11px;text-transform:uppercase;letter-spacing:.12em}.time{font-variant-numeric:tabular-nums;color:#dfe2e8}.name{font-weight:650}.genres{display:flex;gap:6px;flex-wrap:wrap}.genre{font-size:12px;padding:5px 8px;border-radius:999px;background:#1b1e27;color:#cdd1d9}.actions{display:flex;justify-content:flex-end;gap:7px}.icon{width:34px;height:34px;padding:0}
.empty{padding:35px;text-align:center;color:#858b97}.status{position:fixed;right:20px;bottom:20px;background:#171a21;border:1px solid #303641;padding:11px 15px;border-radius:10px;display:none}.status.show{display:block}
.overlay{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;padding:20px;z-index:10}.overlay.open{display:flex}.modal{width:min(620px,100%);max-height:90vh;overflow:auto;background:#11141a;border:1px solid #303641;border-radius:16px;padding:22px}.modal h2{margin:0 0 20px}.field{margin:14px 0}.field label{display:block;font-size:12px;color:#8e94a0;text-transform:uppercase;letter-spacing:.08em;margin-bottom:7px}.field input,.field select{width:100%;padding:11px 12px;border-radius:8px;border:1px solid #303641;background:#0b0d11;color:#fff;font-size:15px}.genre-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.avoid{border-color:#6b2638;background:#24151b}.section-label{font-size:12px;color:#ff8ba7;text-transform:uppercase;letter-spacing:.08em;margin:12px 0 7px}.check{display:flex;gap:8px;align-items:center;background:#171a21;border:1px solid #282d36;padding:9px;border-radius:8px;font-size:13px}.weight-input{width:72px!important;margin-left:auto;padding:6px 7px!important;font-size:13px!important;text-align:right}.modal-actions{display:flex;justify-content:space-between;gap:10px;margin-top:22px}.left-actions{display:flex;gap:8px}.small{font-size:12px;color:#858b97}

@media(max-width:760px){.row{grid-template-columns:1fr 1fr;gap:8px}.head{display:none}.genres{grid-column:1/-1}.actions{justify-content:flex-start}.genre-grid{grid-template-columns:1fr}.top{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top"><div class="brand"><img class="brand-logo" src="https://radiorrr.com/logo.png" alt="Radio RRR logo"><div class="brand-copy"><h1>Radio RRR Scheduler</h1><div class="sub">Programming control centre</div></div></div><div class="pill">SQLite schedule</div></div>
  <div class="toolbar"><a class="btn" href="/admin" style="text-decoration:none">← Admin Menu</a><a class="btn" href="/admin/djs" style="text-decoration:none">DJ Management</a></div>
  <div class="tabs" id="tabs"></div>
  <div class="toolbar"><button class="btn primary" onclick="addSlot()">＋ Add Slot</button><button class="btn" onclick="copyDay()">Copy Day</button><button class="btn" onclick="manageGenres()">Manage Genres</button><button class="btn" onclick="load()">Refresh</button></div>
  <div class="card" id="schedule"></div>
</div>
<div class="status" id="status"></div>
<div class="overlay" id="overlay"><div class="modal" id="modal"></div></div>
<script>
const DAYS=['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'];
let currentDay='Monday', schedule=[], genres=[], antiGenres=[];
const $=id=>document.getElementById(id);
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function toast(t){$('status').textContent=t;$('status').classList.add('show');setTimeout(()=>$('status').classList.remove('show'),2200)}
function renderTabs(){ $('tabs').innerHTML=DAYS.map(d=>`<button class="tab ${d===currentDay?'active':''}" onclick="currentDay='${d}';render()">${d}</button>`).join('') }
function render(){renderTabs();let rows=schedule.filter(x=>x.day===currentDay);$('schedule').innerHTML=`<div class="row head"><div>Time</div><div>Program</div><div>Genres</div><div></div></div>`+ (rows.length?rows.map(x=>`<div class="row"><div class="time">${esc(x.start)} – ${esc(x.end)}</div><div class="name">${esc(x.name)}</div><div class="genres">${x.genres.map(g=>`<span class="genre">${esc(g)}</span>`).join('')}${(x.anti_genres||[]).map(g=>`<span class="genre avoid">🚫 ${esc(g)}</span>`).join('')}</div><div class="actions"><button class="btn icon" onclick="editSlot(${x.id})">✎</button><button class="btn icon danger" onclick="deleteSlot(${x.id})">×</button></div></div>`).join(''):`<div class="empty">No schedule blocks for ${currentDay}.</div>`)}
async function api(url,opt){let r=await fetch(url,opt);if(!r.ok)throw new Error(await r.text());return r.json()}
async function load(){try{let d=await api('/api/schedule');schedule=d.schedule;genres=d.genres;antiGenres=d.anti_genres||[];render();toast('Schedule loaded')}catch(e){toast('Could not load schedule');console.error(e)}}
function close(){ $('overlay').classList.remove('open') }
function openModal(html){$('modal').innerHTML=html;$('overlay').classList.add('open')}
function genreChecks(selected=[],weights={}){const fallback=selected.length?(100/selected.length):0;return genres.map(g=>{const checked=selected.includes(g);const raw=Object.prototype.hasOwnProperty.call(weights||{},g)?Number(weights[g]):(checked?fallback:0);const value=raw>0?Number(raw.toFixed(2)):'';return `<label class="check"><input type="checkbox" name="genre" value="${esc(g)}" ${checked?'checked':''}> <span>${esc(g)}</span><input class="weight-input" type="number" name="genre_weight" data-genre="${esc(g)}" min="0" max="100" step="0.1" value="${value}" placeholder="Weight" title="Relative target weight"></label>`}).join('')}
function selectedGenreWeights(){const out={};document.querySelectorAll('input[name=genre]:checked').forEach(box=>{const input=document.querySelector(`input[name=genre_weight][data-genre="${CSS.escape(box.value)}"]`);const value=input?Number(input.value):0;if(Number.isFinite(value)&&value>0)out[box.value]=value});return out}
function antiGenreChecks(selected=[]){return antiGenres.map(g=>`<label class="check avoid"><input type="checkbox" name="anti_genre" value="${esc(g)}" ${selected.includes(g)?'checked':''}> 🚫 ${esc(g)}</label>`).join('')}
function defaultAntiForNewSlot(){const base=['Spoken Word','Talk','Talking','Dialogue','Comedy','Audiobook','Radioplay','Education','Poetry','Religious','Field Recording'];const name=($('fname')?.value||'').toLowerCase();if(name.includes('dinner warm'))base.push('Hardcore','Gabber','Speedcore','Hardstyle');return base.filter(g=>antiGenres.some(x=>x.toLowerCase()===g.toLowerCase()))}
function editSlot(id){
  let x=schedule.find(v=>v.id===id);
  if(!x)return;
  const weekdayDefault=['Monday','Tuesday','Wednesday','Thursday'].includes(x.day);
  const selectedDays=weekdayDefault?['Monday','Tuesday','Wednesday','Thursday']:[x.day];
  const dayChecks=DAYS.map(d=>`<label class="check"><input type="checkbox" name="edit_day" value="${d}" ${selectedDays.includes(d)?'checked':''}> ${d}</label>`).join('');
  openModal(`<h2>Edit Schedule Slot</h2><div class="field"><label>Apply to days</label><div class="genre-grid">${dayChecks}</div><div class="small" style="margin-top:8px">Select every day that should receive these changes. Mon–Thu are pre-selected for weekday slots.</div></div><div class="field"><label>Start</label><input id="fstart" type="time" value="${esc(x.start)}"></div><div class="field"><label>End</label><input id="fend" type="time" value="${esc(x.end==='00:00'?'00:00':x.end)}"></div><div class="field"><label>Program name</label><input id="fname" value="${esc(x.name)}"></div><div class="field"><label>Genres</label><div class="genre-grid">${genreChecks(x.genres,x.genre_weights||{})}</div><div class="small" style="margin-top:8px">Includes genres detected automatically from DJ audio, plus your configured Radio RRR genres. Weight values are relative percentages; they are normalised to 100 when matching.</div></div><div class="field"><div class="section-label">🚫 Avoid Genres / Content</div><div class="genre-grid">${antiGenreChecks(x.anti_genres||[])}</div><div class="small" style="margin-top:8px">These genres are penalised or blocked when the live AI detects them. Talking/spoken content is included here too.</div></div><div class="modal-actions"><div class="left-actions"><button class="btn danger" onclick="deleteSlot(${id});close()">Delete</button></div><div class="left-actions"><button class="btn" onclick="close()">Cancel</button><button class="btn primary" onclick="saveSlot(${id})">Save</button></div></div>`)}
async function saveSlot(id){
  let original=schedule.find(v=>v.id===id);
  if(!original)return;
  const selectedDays=[...document.querySelectorAll('input[name=edit_day]:checked')].map(x=>x.value);
  if(!selectedDays.length)return alert('Select at least one day.');
  const targetIds=schedule.filter(v=>selectedDays.includes(v.day)&&v.start===original.start&&v.end===original.end).map(v=>v.id);
  if(!targetIds.includes(id))targetIds.push(id);
  if(targetIds.length!==selectedDays.length){
    const foundDays=new Set(schedule.filter(v=>targetIds.includes(v.id)).map(v=>v.day));
    const missing=selectedDays.filter(d=>!foundDays.has(d));
    return alert('Could not find the matching schedule slot on: '+missing.join(', ')+'. Nothing was changed.');
  }
  const body={start:$('fstart').value,end:$('fend').value,name:$('fname').value.trim(),genres:[...document.querySelectorAll('input[name=genre]:checked')].map(x=>x.value),genre_weights:selectedGenreWeights(),anti_genres:[...document.querySelectorAll('input[name=anti_genre]:checked')].map(x=>x.value),ids:targetIds};
  try{await api('/api/admin/schedule/multi-update',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});close();await load();toast('Saved '+selectedDays.length+' day'+(selectedDays.length===1?'':'s'));}catch(e){alert(e.message)}}
function addSlot(){const defaults=antiGenreChecks(['Spoken Word','Talk','Talking','Dialogue','Comedy','Audiobook','Radioplay','Education','Poetry','Religious','Field Recording']);openModal(`<h2>Add Schedule Slot</h2><div class="field"><label>Day</label><select id="fday">${DAYS.map(d=>`<option ${d===currentDay?'selected':''}>${d}</option>`).join('')}</select></div><div class="field"><label>Start</label><input id="fstart" type="time" value="00:00"></div><div class="field"><label>End</label><input id="fend" type="time" value="04:00"></div><div class="field"><label>Program name</label><input id="fname" placeholder="Program name"></div><div class="field"><label>Genres</label><div class="genre-grid">${genreChecks()}</div><div class="small" style="margin-top:8px">The list automatically includes genres detected by the AI genre system. Add weight values to prioritise selected genres; if no weights are entered, targets remain equal.</div></div><div class="field"><div class="section-label">🚫 Avoid Genres / Content</div><div class="genre-grid">${defaults}</div><div class="small" style="margin-top:8px">Talking/spoken content is pre-selected. Add any music genres you don't want in this programme.</div></div><div class="modal-actions"><span></span><div class="left-actions"><button class="btn" onclick="close()">Cancel</button><button class="btn primary" onclick="createSlot()">Add</button></div></div>`)}
async function createSlot(){let body={day:$('fday').value,start:$('fstart').value,end:$('fend').value,name:$('fname').value.trim(),genres:[...document.querySelectorAll('input[name=genre]:checked')].map(x=>x.value),genre_weights:selectedGenreWeights(),anti_genres:[...document.querySelectorAll('input[name=anti_genre]:checked')].map(x=>x.value)};try{await api('/api/admin/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});close();await load();toast('Slot added')}catch(e){alert(e.message)}}
async function deleteSlot(id){if(!confirm('Delete this schedule slot?'))return;try{await api('/api/admin/schedule/'+id,{method:'DELETE'});await load();toast('Deleted')}catch(e){alert(e.message)}}
function copyDay(){openModal(`<h2>Copy Day</h2><div class="field"><label>Copy from</label><select id="from">${DAYS.map(d=>`<option ${d===currentDay?'selected':''}>${d}</option>`).join('')}</select></div><div class="field"><label>Copy to</label><div class="genre-grid">${DAYS.filter(d=>d!==currentDay).map(d=>`<label class="check"><input type="checkbox" name="copyto" value="${d}"> ${d}</label>`).join('')}</div></div><div class="modal-actions"><span></span><div class="left-actions"><button class="btn" onclick="close()">Cancel</button><button class="btn primary" onclick="doCopy()">Copy</button></div></div>`)}
async function doCopy(){let from=$('from').value,to=[...document.querySelectorAll('input[name=copyto]:checked')].map(x=>x.value);if(!to.length)return alert('Select at least one destination day.');try{await api('/api/admin/schedule/copy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from,to})});close();await load();toast('Day copied')}catch(e){alert(e.message)}}
function manageGenres(){openModal(`<h2>Manage Genres</h2><div class="field"><label>Current genres</label><div class="genre-grid">${genres.map(g=>`<div class="check"><span style="flex:1">${esc(g)}</span><button class="btn icon danger" onclick="removeGenre(${JSON.stringify(g)})">×</button></div>`).join('')}</div></div><div class="field"><label>Add genre</label><input id="newgenre" placeholder="e.g. Garage"></div><div class="modal-actions"><span></span><div class="left-actions"><button class="btn" onclick="close()">Close</button><button class="btn primary" onclick="addGenre()">Add Genre</button></div></div>`)}
async function addGenre(){let name=$('newgenre').value.trim();if(!name)return;if(genres.some(g=>g.toLowerCase()===name.toLowerCase()))return alert('That genre already exists.');try{await api('/api/admin/genres',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});let d=await api('/api/schedule');genres=d.genres;antiGenres=d.anti_genres||[];manageGenres();toast('Genre added')}catch(e){alert(e.message)}}
async function removeGenre(name){if(!confirm('Remove '+name+' from the genre list? Existing schedule slots keep their current label.'))return;try{await api('/api/admin/genres/'+encodeURIComponent(name),{method:'DELETE'});let d=await api('/api/schedule');genres=d.genres;antiGenres=d.anti_genres||[];manageGenres();toast('Genre removed')}catch(e){alert(e.message)}}

let djTestJobId=null,djTestUsername=null,djTestTimer=null;
function djTestSetState(text,isError=false){
  $('djTestResult').classList.add('show');
  $('djTestState').className='djtest-state'+(isError?' djtest-error':'');
  $('djTestState').textContent=text;
}
function renderDjTestGenres(items){
  $('djTestGenres').innerHTML=(items||[]).map(item=>{
    const raw=String(item.genre||'');
    const label=raw.includes('---')?raw.split('---',2)[1]:raw;
    const confidence=Number(item.confidence||0)*100;
    return `<div class="djtest-genre"><span>${esc(label)}</span><span class="djtest-confidence">${confidence.toFixed(2)}%</span></div>`;
  }).join('');
}
async function startDjTest(){
  const username=$('djTestUsername').value.trim().replace(/^@/,'');
  const sampleSeconds=Math.max(10,Math.min(90,Number($('djTestSeconds').value)||30));
  if(!username)return alert('Enter a TikTok username.');
  if(djTestTimer){clearTimeout(djTestTimer);djTestTimer=null}
  $('djTestButton').disabled=true;
  $('djTestGenres').innerHTML='';
  $('djTestActions').innerHTML='';
  djTestSetState(`Starting test for @${username}…`);
  try{
    const result=await api('/api/admin/dj-test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,sample_seconds:sampleSeconds})});
    djTestJobId=result.job_id;djTestUsername=result.username;
    pollDjTest();
  }catch(e){
    $('djTestButton').disabled=false;
    djTestSetState(e.message||'Could not start DJ test',true);
  }
}
async function pollDjTest(){
  if(!djTestJobId)return;
  try{
    const job=await api('/api/admin/dj-test/'+encodeURIComponent(djTestJobId));
    if(job.status==='queued'||job.status==='capturing'){
      djTestSetState(`Sampling @${job.username}… this takes about ${job.sample_seconds}s.`);
    }else if(job.status==='ready'){
      djTestSetState(`Audio captured. Waiting for the AI genre detector…`);
    }else if(job.status==='analysing'){
      djTestSetState(`Analysing @${job.username} with the Radio RRR genre model…`);
    }else if(job.status==='complete'){
      djTestSetState(`Test complete for @${job.username}. Nothing has been added to the database.`);
      renderDjTestGenres(job.genres||[]);
      $('djTestActions').innerHTML=`<button class="btn" type="button" onclick="startDjTest()">Test Again</button><button class="btn primary" type="button" onclick="addTestedDj()">Add DJ</button>`;
      $('djTestButton').disabled=false;
      djTestTimer=null;
      return;
    }else if(job.status==='error'){
      djTestSetState(job.error||'DJ test failed.',true);
      $('djTestButton').disabled=false;
      djTestTimer=null;
      return;
    }
    djTestTimer=setTimeout(pollDjTest,1500);
  }catch(e){
    djTestSetState(e.message||'Could not read DJ test status',true);
    $('djTestButton').disabled=false;
    djTestTimer=null;
  }
}
async function addTestedDj(){
  if(!djTestUsername)return;
  if(!confirm(`Add @${djTestUsername} to the Radio RRR DJ database?`))return;
  try{
    const result=await api('/api/favourites/import-tiktok',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:djTestUsername})});
    if(result.status!=='ok')throw new Error(result.message||'Could not add DJ');
    toast(`Added @${djTestUsername}`);
    $('djTestActions').innerHTML=`<span class="small">@${esc(djTestUsername)} added to Radio RRR.</span>`;
  }catch(e){alert(e.message)}
}
$('overlay').addEventListener('click',e=>{if(e.target.id==='overlay')close()});load();
</script>
</body></html>"""

@app.get("/admin", response_class=HTMLResponse)
def admin_home():
    return HTMLResponse(ADMIN_HOME_HTML)


@app.get("/admin/djs", response_class=HTMLResponse)
def dj_admin():
    return HTMLResponse(DJ_ADMIN_HTML)


@app.get("/admin/schedule", response_class=HTMLResponse)
def scheduler_admin():
    return HTMLResponse(ADMIN_HTML)


def _invalidate_schedule_cache():
    schedule_cache["fetched_at"] = None
    schedule_cache["blocks"] = None


def _dynamic_scheduler_genres(conn=None):
    """
    Return the scheduler genre picker from both configured RRR genres and
    the genres actually produced by the AI detector.

    AI labels may be hierarchical, e.g.
        Electronic---Electro House
    The scheduler stores the useful child label:
        Electro House

    The existing schedule_genres table is deliberately retained so custom
    station genres such as 80s can still be added even if AI has never
    detected them.
    """
    owns_connection = conn is None
    if owns_connection:
        conn = get_db()

    names = {}
    source = {}

    try:
        # Existing/manual Radio RRR genres.
        for row in conn.execute("""
            SELECT name
            FROM schedule_genres
            WHERE enabled = 1
        """).fetchall():
            name = str(row["name"] or "").strip()
            if name:
                key = name.casefold()
                names.setdefault(key, name)
                source.setdefault(key, "configured")

        # Every genre currently known by the latest AI result for each DJ.
        # ai_genre_detection is the current/latest table, rather than the
        # high-frequency historical observations table.
        try:
            ai_rows = conn.execute("""
                SELECT genres_json
                FROM ai_genre_detection
            """).fetchall()
        except sqlite3.OperationalError:
            ai_rows = []

        for row in ai_rows:
            try:
                detected = json.loads(row["genres_json"] or "[]")
            except Exception:
                continue

            if not isinstance(detected, list):
                continue

            for item in detected:
                if isinstance(item, dict):
                    raw = item.get("genre")
                else:
                    raw = item

                name = str(raw or "").strip()
                if not name:
                    continue

                # Flatten Essentia/Discogs hierarchical labels for the UI.
                if "---" in name:
                    name = name.split("---", 1)[1].strip()

                if not name:
                    continue

                key = name.casefold()

                # Prefer the first human-readable spelling we encounter.
                names.setdefault(key, name)

                if source.get(key) != "configured":
                    source[key] = "ai"

        return [
            {
                "name": names[key],
                "source": source.get(key, "configured"),
            }
            for key in sorted(names, key=lambda k: names[k].casefold())
        ]
    finally:
        if owns_connection:
            conn.close()


@app.get("/api/schedule")
def public_schedule():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, day, start_minutes, end_minutes, name, genres_json, genre_weights_json, anti_genres_json, enabled
        FROM schedule
        WHERE enabled = 1
        ORDER BY CASE day
            WHEN 'Monday' THEN 0 WHEN 'Tuesday' THEN 1 WHEN 'Wednesday' THEN 2
            WHEN 'Thursday' THEN 3 WHEN 'Friday' THEN 4 WHEN 'Saturday' THEN 5
            WHEN 'Sunday' THEN 6 ELSE 7 END, start_minutes, id
    """).fetchall()

    dynamic_genres = _dynamic_scheduler_genres(conn)
    conn.close()

    return {
        "schedule": [
            {
                "id": r["id"],
                "day": r["day"],
                "start": _minutes_to_time(r["start_minutes"]),
                "end": _minutes_to_time(r["end_minutes"]),
                "name": r["name"],
                "genres": json.loads(r["genres_json"] or "[]"),
                "genre_weights": json.loads(r["genre_weights_json"] or "{}"),
                "anti_genres": json.loads(r["anti_genres_json"] or "[]"),
                "enabled": bool(r["enabled"]),
            }
            for r in rows
        ],
        "genres": [item["name"] for item in dynamic_genres],
        "genre_sources": dynamic_genres,
        "anti_genres": sorted(set(
            [str(x).strip() for x in DEFAULT_ANTI_GENRES if str(x).strip()]
            + [item["name"] for item in dynamic_genres]
        ), key=str.casefold),
    }


@app.post("/api/admin/schedule")
async def admin_add_schedule(request: Request):
    data = await request.json()
    try:
        day = str(data.get("day", "")).strip()
        start = _time_to_minutes(data.get("start"))
        end = _time_to_minutes(data.get("end"))
        name = str(data.get("name", "")).strip()
        genres = [str(g).strip() for g in data.get("genres", []) if str(g).strip()]
        genre_weights = {
            str(g).strip(): float(w)
            for g, w in (data.get("genre_weights") or {}).items()
            if str(g).strip() in genres and float(w) > 0
        }
        anti_genres = [str(g).strip() for g in data.get("anti_genres", []) if str(g).strip()]
        if not anti_genres:
            anti_genres = list(DEFAULT_ANTI_GENRES)
        if "dinner warm" in name.casefold():
            anti_genres = sorted(set(anti_genres + DEFAULT_DINNER_WARMUP_ANTI_GENRES), key=str.casefold)
        if day not in SCHEDULE_DAYS or not name or not genres or (end <= start and end != 0):
            raise ValueError("Day, name, at least one genre and a valid time range are required")
        conn = get_db(); now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO schedule (day,start_minutes,end_minutes,name,genres_json,genre_weights_json,anti_genres_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?,1,?,?)", (day,start,end,name,json.dumps(genres),json.dumps(genre_weights),json.dumps(anti_genres),now,now))
        conn.commit(); conn.close(); _invalidate_schedule_cache()
        return {"status":"ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/admin/schedule/multi-update")
async def admin_multi_update_schedule(request: Request):
    """Update the same schedule slot across multiple existing days atomically."""
    data = await request.json()
    try:
        ids = []
        for value in data.get("ids", []):
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        ids = list(dict.fromkeys(ids))

        start = _time_to_minutes(data.get("start"))
        end = _time_to_minutes(data.get("end"))
        name = str(data.get("name", "")).strip()
        genres = [str(g).strip() for g in data.get("genres", []) if str(g).strip()]
        genre_weights = {
            str(g).strip(): float(w)
            for g, w in (data.get("genre_weights") or {}).items()
            if str(g).strip() in genres and float(w) > 0
        }
        anti_genres = [str(g).strip() for g in data.get("anti_genres", []) if str(g).strip()]

        if "dinner warm" in name.casefold():
            anti_genres = sorted(
                set(anti_genres + DEFAULT_DINNER_WARMUP_ANTI_GENRES),
                key=str.casefold,
            )

        if not ids or not name or not genres or (end <= start and end != 0):
            raise ValueError("At least one schedule slot, a name, one genre and a valid time range are required")

        conn = get_db()
        try:
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT id, day FROM schedule WHERE id IN ({placeholders})",
                ids,
            ).fetchall()

            found = {int(row["id"]): row["day"] for row in rows}
            missing_ids = [schedule_id for schedule_id in ids if schedule_id not in found]
            if missing_ids:
                raise ValueError("One or more selected schedule slots no longer exist")

            now = datetime.now(timezone.utc).isoformat()
            for schedule_id in ids:
                conn.execute(
                    """
                    UPDATE schedule
                    SET start_minutes=?, end_minutes=?, name=?, genres_json=?,
                        genre_weights_json=?, anti_genres_json=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        start,
                        end,
                        name,
                        json.dumps(genres),
                        json.dumps(genre_weights),
                        json.dumps(anti_genres),
                        now,
                        schedule_id,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        _invalidate_schedule_cache()
        return {"status": "ok", "updated": len(ids), "days": [found[i] for i in ids]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))



@app.put("/api/admin/schedule/{schedule_id}")
async def admin_update_schedule(schedule_id: int, request: Request):
    data = await request.json()
    try:
        day = str(data.get("day", "")).strip(); start = _time_to_minutes(data.get("start")); end = _time_to_minutes(data.get("end"))
        name = str(data.get("name", "")).strip(); genres = [str(g).strip() for g in data.get("genres", []) if str(g).strip()]
        genre_weights = {str(g).strip(): float(w) for g, w in (data.get("genre_weights") or {}).items() if str(g).strip() in genres and float(w) > 0}
        anti_genres = [str(g).strip() for g in data.get("anti_genres", []) if str(g).strip()]
        if "dinner warm" in name.casefold():
            anti_genres = sorted(set(anti_genres + DEFAULT_DINNER_WARMUP_ANTI_GENRES), key=str.casefold)
        if day not in SCHEDULE_DAYS or not name or not genres or (end <= start and end != 0):
            raise ValueError("Day, name, at least one genre and a valid time range are required")
        conn=get_db(); now=datetime.now(timezone.utc).isoformat()
        cur=conn.execute("UPDATE schedule SET day=?,start_minutes=?,end_minutes=?,name=?,genres_json=?,genre_weights_json=?,anti_genres_json=?,updated_at=? WHERE id=?",(day,start,end,name,json.dumps(genres),json.dumps(genre_weights),json.dumps(anti_genres),now,schedule_id))
        conn.commit(); conn.close()
        if cur.rowcount == 0: raise ValueError("Schedule slot not found")
        _invalidate_schedule_cache(); return {"status":"ok"}
    except Exception as e: raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/admin/schedule/{schedule_id}")
def admin_delete_schedule(schedule_id: int):
    conn=get_db(); cur=conn.execute("DELETE FROM schedule WHERE id=?",(schedule_id,)); conn.commit(); conn.close()
    if cur.rowcount == 0: raise HTTPException(status_code=404, detail="Schedule slot not found")
    _invalidate_schedule_cache(); return {"status":"ok"}


@app.post("/api/admin/schedule/copy")
async def admin_copy_schedule(request: Request):
    data=await request.json(); source=str(data.get("from","")).strip(); targets=[str(x).strip() for x in data.get("to",[])]
    if source not in SCHEDULE_DAYS or not targets or any(x not in SCHEDULE_DAYS for x in targets): raise HTTPException(status_code=400, detail="Invalid copy day")
    conn=get_db(); rows=conn.execute("SELECT start_minutes,end_minutes,name,genres_json,genre_weights_json,anti_genres_json,enabled FROM schedule WHERE day=? ORDER BY start_minutes,id",(source,)).fetchall(); now=datetime.now(timezone.utc).isoformat()
    for target in targets:
        if target == source: continue
        conn.execute("DELETE FROM schedule WHERE day=?",(target,))
        for r in rows:
            conn.execute("INSERT INTO schedule (day,start_minutes,end_minutes,name,genres_json,genre_weights_json,anti_genres_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(target,r["start_minutes"],r["end_minutes"],r["name"],r["genres_json"],r["genre_weights_json"],r["anti_genres_json"],r["enabled"],now,now))
    conn.commit(); conn.close(); _invalidate_schedule_cache(); return {"status":"ok"}


@app.post("/api/admin/genres")
async def admin_add_genre(request: Request):
    data=await request.json(); name=str(data.get("name","")).strip()
    if not name or len(name)>80: raise HTTPException(status_code=400, detail="Invalid genre name")
    conn=get_db()
    try: conn.execute("INSERT INTO schedule_genres (name,enabled,created_at) VALUES (?,1,?)",(name,datetime.now(timezone.utc).isoformat())); conn.commit()
    except sqlite3.IntegrityError: raise HTTPException(status_code=409, detail="Genre already exists")
    finally: conn.close()
    return {"status":"ok"}


@app.delete("/api/admin/genres/{name}")
def admin_delete_genre(name: str):
    conn=get_db(); cur=conn.execute("UPDATE schedule_genres SET enabled=0 WHERE name=?",(name,)); conn.commit(); conn.close()
    if cur.rowcount == 0: raise HTTPException(status_code=404, detail="Genre not found")
    return {"status":"ok"}


@app.post("/api/live-stream-release")
async def release_live_stream(dj: str = None, platform: str = None):
    """
    Release a temporary manual DJ relay without interrupting active listeners.

    Candidate-scout analysis shares the same per-DJ manual relay used by
    browser listeners. Do not terminate or delete that relay here because the
    browser may still be consuming it. The existing idle cleanup monitor will
    remove the relay after 120 seconds without segment activity.
    """
    username = str(dj or "").lstrip("@").strip()
    if not username:
        return {"ok": True, "released": False}

    platform_key = str(platform or "TikTok").strip().casefold()
    if platform_key not in {"tiktok", "twitch", "youtube"}:
        return {"ok": True, "released": False}

    key = f"{platform_key}:{username.lower()}"

    async with manual_relay_lock:
        relay = manual_relays.get(key)

        if relay:
            # Preserve the shared manual relay for any active browser viewer.
            # Segment requests continue updating last_used; if nobody is using
            # the relay, manual_relay_cleanup_monitor() removes it after the
            # normal 120-second idle period.
            relay["last_used"] = datetime.now(timezone.utc)

    return {
        "ok": True,
        "released": bool(relay),
        "deferred_cleanup": bool(relay),
    }



@app.post("/api/ai-genre")
async def ai_genre(request: Request):

    try:
        payload = await request.json()
    except Exception:
        return Response(
            content=json.dumps({"error": "Invalid JSON"}),
            media_type="application/json",
            status_code=400,
        )

    username = str(payload.get("username") or "").lstrip("@").strip()
    platform = str(payload.get("platform") or "TikTok").strip()
    detected_at = str(
        payload.get("detected_at")
        or datetime.now(timezone.utc).isoformat()
    ).strip()
    genres = payload.get("genres")

    if not username:
        return Response(
            content=json.dumps({"error": "username required"}),
            media_type="application/json",
            status_code=400,
        )

    if platform.casefold() not in {"tiktok", "twitch", "youtube"}:
        return Response(
            content=json.dumps({"error": "unsupported platform"}),
            media_type="application/json",
            status_code=400,
        )

    platform = ("Twitch" if platform.casefold() == "twitch" else "YouTube" if platform.casefold() == "youtube" else "TikTok")

    if not isinstance(genres, list):
        genres = []

    conn = get_db()
    ensure_ai_platform_schema(conn)

    conn.execute("""
        INSERT INTO ai_genre_detection
        (platform, username, detected_at, genres_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(platform, username) DO UPDATE SET
            detected_at = excluded.detected_at,
            genres_json = excluded.genres_json
    """, (platform, username, detected_at, json.dumps(genres)))

    conn.execute("""
        INSERT INTO ai_genre_observations
        (platform, username, detected_at, genres_json)
        VALUES (?, ?, ?, ?)
    """, (platform, username, detected_at, json.dumps(genres)))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "platform": platform,
        "username": username,
        "detected_at": detected_at,
        "genres": genres,
    }


# ------------------------------------------------------------
# AI GENRE GET API
# Returns the latest stored AI genre analysis for a DJ.
# ------------------------------------------------------------
@app.get("/api/ai-genre")
def get_ai_genre(username: str, platform: str = "TikTok"):
    username = str(username or "").strip().lstrip("@").lower()

    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    platform = ("Twitch" if str(platform).casefold() == "twitch" else "YouTube" if str(platform).casefold() == "youtube" else "TikTok")
    conn = get_db()

    row = conn.execute("""
        SELECT platform, username, detected_at, genres_json
        FROM ai_genre_detection
        WHERE lower(platform) = lower(?)
          AND lower(ltrim(username, '@')) = ?
    """, (platform, username)).fetchone()

    conn.close()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No AI genre result found for @{username}"
        )

    try:
        genres = json.loads(row["genres_json"])
    except Exception:
        genres = []

    return {
        "platform": row["platform"],
        "username": row["username"],
        "detected_at": row["detected_at"],
        "genres": genres
    }


# ------------------------------------------------------------
# AI GENRE HISTORY API
# Returns historical AI genre observations for a DJ.
# ------------------------------------------------------------
@app.get("/api/ai-genre/history")
def get_ai_genre_history(username: str, limit: int = 100, platform: str = "TikTok"):
    username = str(username or "").strip().lstrip("@").lower()

    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    # Keep the development/debug endpoint bounded.
    limit = max(1, min(int(limit), 500))

    platform = ("Twitch" if str(platform).casefold() == "twitch" else "YouTube" if str(platform).casefold() == "youtube" else "TikTok")
    conn = get_db()

    try:
        rows = conn.execute("""
            SELECT id, platform, username, detected_at, genres_json
            FROM ai_genre_observations
            WHERE lower(platform) = lower(?)
              AND lower(ltrim(username, '@')) = ?
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
        """, (platform, username, limit)).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    observations = []

    for row in rows:
        try:
            genres = json.loads(row["genres_json"] or "[]")
        except Exception:
            genres = []

        observations.append({
            "id": row["id"],
            "platform": row["platform"],
            "username": row["username"],
            "detected_at": row["detected_at"],
            "genres": genres,
        })

    return {
        "platform": platform,
        "username": username,
        "count": len(observations),
        "observations": observations,
    }


# ------------------------------------------------------------
# AI GENRE LEARNED PROFILE API
# Builds a rolling genre profile from historical AI observations.
#
# The latest /api/ai-genre result answers:
#     "What is this DJ playing right now?"
#
# This endpoint answers:
#     "What does this DJ generally play?"
#
# Each historical observation contributes its detected genres weighted
# by the AI confidence supplied for that observation. The resulting
# scores are then normalised into percentages.
#
# Parent/child labels such as:
#     Electronic---Schranz
# are grouped under the child genre:
#     Schranz
#
# This is intentionally read-only for now. It does not modify
# favourite_djs.genre or affect Router selection.
# ------------------------------------------------------------
@app.get("/api/ai-genre/profile")
def get_ai_genre_profile(
    username: str,
    days: int = 30,
    limit: int = 5,
    platform: str = "TikTok",
):
    username = str(username or "").strip().lstrip("@").lower()
    platform = ("Twitch" if str(platform).casefold() == "twitch" else "YouTube" if str(platform).casefold() == "youtube" else "TikTok")

    if not username:
        raise HTTPException(
            status_code=400,
            detail="username is required",
        )

    # days=0 means use all available history.
    days = max(0, min(int(days), 3650))
    limit = max(1, min(int(limit), 20))

    conn = get_db()

    try:
        if days > 0:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=days)
            ).isoformat()

            rows = conn.execute(
                """
                SELECT id, detected_at, genres_json
                FROM ai_genre_observations
                WHERE lower(platform) = lower(?)
                  AND lower(ltrim(username, '@')) = ?
                  AND detected_at >= ?
                ORDER BY detected_at ASC, id ASC
                """,
                (platform, username, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, detected_at, genres_json
                FROM ai_genre_observations
                WHERE lower(platform) = lower(?)
                  AND lower(ltrim(username, '@')) = ?
                ORDER BY detected_at ASC, id ASC
                """,
                (platform, username),
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    # Raw AI observations can arrive every few seconds. Treat detections
    # inside the same five-minute UTC window as one representative sample
    # so continuous monitoring of the same song does not overweight the
    # learned DJ profile.
    #
    # Within each five-minute window, average the confidence for each
    # genre across the detections that occurred during that window.
    # Each completed window therefore contributes at most one sample.
    five_minute_samples = []

    current_bucket = None
    bucket_genres = {}
    bucket_display_names = {}
    bucket_last_detected = None

    def flush_bucket():
        nonlocal bucket_genres
        nonlocal bucket_display_names
        nonlocal bucket_last_detected

        if not bucket_genres:
            bucket_genres = {}
            bucket_display_names = {}
            bucket_last_detected = None
            return

        sample = {}

        for key, values in bucket_genres.items():
            if not values:
                continue

            sample[key] = {
                "confidence": sum(values) / len(values),
                "display_name": bucket_display_names.get(key, key),
                "last_detected_at": bucket_last_detected,
            }

        if sample:
            five_minute_samples.append(sample)

        bucket_genres = {}
        bucket_display_names = {}
        bucket_last_detected = None

    for row in rows:
        try:
            detected_at = datetime.fromisoformat(
                str(row["detected_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue

        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)

        # Floor the timestamp to a five-minute UTC bucket.
        bucket_start = detected_at.replace(
            minute=(detected_at.minute // 5) * 5,
            second=0,
            microsecond=0,
        )

        if current_bucket is None:
            current_bucket = bucket_start
        elif bucket_start != current_bucket:
            flush_bucket()
            current_bucket = bucket_start

        try:
            genres = json.loads(row["genres_json"] or "[]")
        except Exception:
            continue

        if not isinstance(genres, list) or not genres:
            continue

        bucket_last_detected = row["detected_at"]

        # Avoid counting the same genre more than once in a single
        # detection. If aliases/duplicates are present, keep the first.
        seen_in_detection = set()

        for item in genres:
            if isinstance(item, dict):
                raw_genre = item.get("genre")
                confidence = item.get("confidence")
            else:
                raw_genre = item
                confidence = None

            value = str(raw_genre or "").strip()

            if not value:
                continue

            # Group Parent---Child labels by their child genre.
            if "---" in value:
                value = value.split("---", 1)[1].strip()

            if not value:
                continue

            key = value.casefold()

            if key in seen_in_detection:
                continue

            seen_in_detection.add(key)

            # Confidence may be supplied as 0-1 or 0-100.
            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError):
                confidence_value = 1.0

            if confidence_value > 1.0:
                confidence_value /= 100.0

            confidence_value = max(
                0.0,
                min(1.0, confidence_value),
            )

            # Older observations without confidence remain valid and
            # receive neutral weight.
            if confidence is None:
                confidence_value = 1.0

            bucket_genres.setdefault(key, []).append(
                confidence_value
            )

            if key not in bucket_display_names:
                bucket_display_names[key] = value

    # Include the final bucket.
    flush_bucket()

    # Each five-minute sample has equal overall weight. Within a sample,
    # the AI confidence determines how strongly each genre contributes.
    score_by_genre = {}
    samples_by_genre = {}
    last_detected_by_genre = {}
    samples_used = len(five_minute_samples)

    for sample in five_minute_samples:
        for key, data in sample.items():
            score = data["confidence"]

            score_by_genre[key] = (
                score_by_genre.get(key, 0.0)
                + score
            )

            samples_by_genre[key] = (
                samples_by_genre.get(key, 0)
                + 1
            )

            last_detected_by_genre[key] = data["last_detected_at"]

    total_score = sum(score_by_genre.values())

    profile = []

    if total_score > 0:
        ranked = sorted(
            score_by_genre.items(),
            key=lambda item: (-item[1], item[0]),
        )

        # Display names are already normalised while building each sample.
        display_names = {}

        for sample in five_minute_samples:
            for key, data in sample.items():
                display_names.setdefault(
                    key,
                    data["display_name"],
                )

        for key, score in ranked[:limit]:
            profile.append({
                "genre": display_names.get(key, key),
                "score": round(score, 4),
                "percentage": round(
                    (score / total_score) * 100,
                    1,
                ),
                "samples": samples_by_genre.get(key, 0),
                "last_detected_at": last_detected_by_genre.get(key),
            })

    return {
        "platform": platform,
        "username": username,
        "days": days,
        "observations_used": len(rows),
        "samples_used": samples_used,
        "sample_interval_minutes": 5,
        "genres": profile,
    }


@app.post("/api/ai-genre/database")
async def ai_genre_database(request: Request):

    try:
        payload = await request.json()
    except Exception:
        return Response(
            content=json.dumps({"error": "Invalid JSON"}),
            media_type="application/json",
            status_code=400,
        )

    username = str(
        payload.get("username") or ""
    ).lstrip("@").strip()

    platform = str(payload.get("platform") or "TikTok").strip()
    platform = ("Twitch" if platform.casefold() == "twitch" else "YouTube" if platform.casefold() == "youtube" else "TikTok")

    genre = str(
        payload.get("genre") or ""
    ).strip()

    if not username or not genre:
        return Response(
            content=json.dumps({
                "error": "username and genre required"
            }),
            media_type="application/json",
            status_code=400,
        )

    # Keep only the broad primary genre.
    if "---" in genre:
        genre = genre.split("---", 1)[0].strip()

    conn = get_db()

    conn.execute("""
        UPDATE favourite_djs
        SET genre = ?
        WHERE lower(platform) = lower(?)
          AND lower(ltrim(username, '@')) = lower(?)
    """, (
        genre,
        platform,
        username,
    ))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "platform": platform,
        "username": username,
        "genre": genre,
    }


# ---------------------------------------------------------
# Live DJ API
# ---------------------------------------------------------


# ------------------------------------------------------------
# GENRE MATCHER LEARNED PROFILE HELPER — STAGE 2
# Uses the same five-minute bucketing model as /api/ai-genre/profile.
# Read-only. Does not modify the database or affect relay selection.
# ------------------------------------------------------------
def get_learned_genre_profile(username: str, platform: str = "TikTok", days: int = 30, limit: int = 20):
    username = str(username or "").strip().lstrip("@").lower()
    platform = ("Twitch" if str(platform).casefold() == "twitch" else "YouTube" if str(platform).casefold() == "youtube" else "TikTok")

    if not username:
        return {
            "observations_used": 0,
            "samples_used": 0,
            "genres": [],
        }

    days = max(0, min(int(days), 3650))
    limit = max(1, min(int(limit), 20))

    conn = get_db()

    try:
        if days > 0:
            cutoff = (
                datetime.now(timezone.utc)
                - timedelta(days=days)
            ).isoformat()

            rows = conn.execute(
                """
                SELECT id, detected_at, genres_json
                FROM ai_genre_observations
                WHERE lower(platform) = lower(?)
                  AND lower(ltrim(username, "@")) = ?
                  AND detected_at >= ?
                ORDER BY detected_at ASC, id ASC
                """,
                (platform, username, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, detected_at, genres_json
                FROM ai_genre_observations
                WHERE lower(platform) = lower(?)
                  AND lower(ltrim(username, "@")) = ?
                ORDER BY detected_at ASC, id ASC
                """,
                (platform, username),
            ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    five_minute_samples = []

    current_bucket = None
    bucket_genres = {}
    bucket_display_names = {}
    bucket_last_detected = None

    def flush_bucket():
        nonlocal bucket_genres
        nonlocal bucket_display_names
        nonlocal bucket_last_detected

        if not bucket_genres:
            bucket_genres = {}
            bucket_display_names = {}
            bucket_last_detected = None
            return

        sample = {}

        for key, values in bucket_genres.items():
            if not values:
                continue

            sample[key] = {
                "confidence": sum(values) / len(values),
                "display_name": bucket_display_names.get(key, key),
                "last_detected_at": bucket_last_detected,
            }

        if sample:
            five_minute_samples.append(sample)

        bucket_genres = {}
        bucket_display_names = {}
        bucket_last_detected = None

    for row in rows:
        try:
            detected_at = datetime.fromisoformat(
                str(row["detected_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError):
            continue

        if detected_at.tzinfo is None:
            detected_at = detected_at.replace(tzinfo=timezone.utc)

        bucket_start = detected_at.replace(
            minute=(detected_at.minute // 5) * 5,
            second=0,
            microsecond=0,
        )

        if current_bucket is None:
            current_bucket = bucket_start
        elif bucket_start != current_bucket:
            flush_bucket()
            current_bucket = bucket_start

        try:
            genres = json.loads(row["genres_json"] or "[]")
        except Exception:
            continue

        if not isinstance(genres, list) or not genres:
            continue

        bucket_last_detected = row["detected_at"]
        seen_in_detection = set()

        for item in genres:
            if isinstance(item, dict):
                raw_genre = item.get("genre")
                confidence = item.get("confidence")
            else:
                raw_genre = item
                confidence = None

            value = str(raw_genre or "").strip()

            if not value:
                continue

            if "---" in value:
                value = value.split("---", 1)[1].strip()

            if not value:
                continue

            key = value.casefold()

            if key in seen_in_detection:
                continue

            seen_in_detection.add(key)

            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError):
                confidence_value = 1.0

            if confidence_value > 1.0:
                confidence_value /= 100.0

            confidence_value = max(
                0.0,
                min(1.0, confidence_value),
            )

            if confidence is None:
                confidence_value = 1.0

            bucket_genres.setdefault(
                key, []
            ).append(confidence_value)

            if key not in bucket_display_names:
                bucket_display_names[key] = value

    flush_bucket()

    score_by_genre = {}
    samples_by_genre = {}
    last_detected_by_genre = {}

    for sample in five_minute_samples:
        for key, data in sample.items():
            score = data["confidence"]

            score_by_genre[key] = (
                score_by_genre.get(key, 0.0)
                + score
            )

            samples_by_genre[key] = (
                samples_by_genre.get(key, 0)
                + 1
            )

            last_detected_by_genre[key] = data["last_detected_at"]

    total_score = sum(score_by_genre.values())

    profile = []

    if total_score > 0:
        ranked = sorted(
            score_by_genre.items(),
            key=lambda item: (-item[1], item[0]),
        )

        display_names = {}

        for sample in five_minute_samples:
            for key, data in sample.items():
                display_names.setdefault(
                    key,
                    data["display_name"],
                )

        for key, score in ranked[:limit]:
            profile.append({
                "genre": display_names.get(key, key),
                "score": round(score, 4),
                "percentage": round(
                    (score / total_score) * 100,
                    1,
                ),
                "samples": samples_by_genre.get(key, 0),
                "last_detected_at": last_detected_by_genre.get(key),
            })

    return {
        "observations_used": len(rows),
        "samples_used": len(five_minute_samples),
        "genres": profile,
    }


# Learned profiles are historical catalogue data and do not need to be
# recomputed on every /api/live poll. Keep a short in-process cache so repeated
# API requests do not reopen and reprocess the same SQLite history hundreds of
# times per minute.
LEARNED_GENRE_PROFILE_CACHE_TTL = 60.0
learned_genre_profile_cache = {}
learned_genre_profile_cache_lock = threading.Lock()


def get_cached_learned_genre_profile(
    username: str,
    platform: str = "TikTok",
    days: int = 30,
    limit: int = 20,
):
    normalized_username = (
        str(username or "")
        .strip()
        .lstrip("@")
        .lower()
    )
    normalized_platform = (
        "Twitch"
        if str(platform).casefold() == "twitch"
        else "YouTube"
        if str(platform).casefold() == "youtube"
        else "TikTok"
    )
    normalized_days = max(0, min(int(days), 3650))
    normalized_limit = max(1, min(int(limit), 20))

    key = (
        normalized_platform.casefold(),
        normalized_username,
        normalized_days,
        normalized_limit,
    )
    now = time.monotonic()

    with learned_genre_profile_cache_lock:
        cached = learned_genre_profile_cache.get(key)
        if cached and cached["expires_at"] > now:
            value = cached["value"]
            return {
                "observations_used": value["observations_used"],
                "samples_used": value["samples_used"],
                "genres": [
                    dict(item)
                    for item in value["genres"]
                ],
            }

    value = get_learned_genre_profile(
        normalized_username,
        platform=normalized_platform,
        days=normalized_days,
        limit=normalized_limit,
    )

    stored = {
        "observations_used": int(
            value.get("observations_used", 0) or 0
        ),
        "samples_used": int(
            value.get("samples_used", 0) or 0
        ),
        "genres": [
            dict(item)
            for item in value.get("genres", [])
            if isinstance(item, dict)
        ],
    }

    with learned_genre_profile_cache_lock:
        learned_genre_profile_cache[key] = {
            "expires_at": now + LEARNED_GENRE_PROFILE_CACHE_TTL,
            "value": stored,
        }

    return {
        "observations_used": stored["observations_used"],
        "samples_used": stored["samples_used"],
        "genres": [
            dict(item)
            for item in stored["genres"]
        ],
    }


def _load_live_favourites_with_learned_profiles():
    """
    Load favourite DJs and historical learned genres away from the FastAPI
    event loop. This is intentionally synchronous because it runs via
    asyncio.to_thread() from /api/live.
    """
    conn = get_db()
    try:
        favourite_rows = conn.execute("""
            SELECT *
            FROM favourite_djs
            WHERE enabled = 1
            ORDER BY name COLLATE NOCASE
        """).fetchall()
    finally:
        conn.close()

    favourites = [
        dict(row)
        for row in favourite_rows
    ]

    for favourite in favourites:
        learned = get_cached_learned_genre_profile(
            favourite.get("username"),
            platform=favourite.get("platform") or "TikTok",
            days=0,
            limit=20,
        )

        learned_genres = [
            str(item.get("genre") or "").strip()
            for item in learned.get("genres", [])
            if isinstance(item, dict)
            and str(item.get("genre") or "").strip()
        ]

        if learned_genres:
            favourite["rrr_learned_genres"] = learned_genres
            favourite["rrr_learned_samples"] = int(
                learned.get("samples_used", 0) or 0
            )

    return favourites


# ------------------------------------------------------------
# GENRE MATCHER DIAGNOSTIC API — STAGE 2.1
# Diagnostic only. Does NOT switch or start any relay.
# ------------------------------------------------------------
@app.get("/api/genre-match")
async def genre_match():
    """
    Stage 3 diagnostic view of the same selector used by the relay monitor.
    The endpoint reports the proposed default DJ and current relay without
    modifying relay state.
    """
    live_djs, enriched_live_djs, ranked = (
        await build_genre_match_candidates()
    )

    current_username = str(
        relay_username or ""
    ).lstrip("@").strip()
    current_platform = str(
        relay_platform or "TikTok"
    ).strip()

    now = datetime.now(timezone.utc)
    displayed_ranked = stage3_adjusted_ranking(ranked, enriched_live_djs, now)
    current_key = relay_identity_key(current_platform, current_username)
    current_item = next((
        item for item in displayed_ranked
        if relay_identity_key(
            item.get("platform") or "TikTok",
            item.get("username"),
        ) == current_key
    ), None)
    candidate, reason = select_stage3_candidate(ranked, enriched_live_djs, now=now)
    healthy = (
        relay_process is not None and relay_process.poll() is None
        and bool(relay_username)
        and relay_output_is_healthy(RELAY_PLAYLIST, RELAY_DIR)
    )
    _, reason = stage3_switch_decision(
        candidate,
        current_item,
        current_username,
        healthy,
        current_platform,
    )
    for item in displayed_ranked:
        key = relay_identity_key(
            item.get("platform") or "TikTok",
            item.get("username"),
        )
        blocked_until = relay_failed_until.get(key)
        item["relay_cooldown_until"] = (
            blocked_until.isoformat() if blocked_until and now < blocked_until else None
        )

    for index, item in enumerate(displayed_ranked, start=1):
        item["rank"] = index

    return {
        "ok": True,
        "stage": 3,
        "diagnostic_only": True,
        "automatic_selection_enabled": STAGE3_ENABLED,
        "live_count": len(enriched_live_djs),
        "targets": [
            {"genre": genre, "weight": weight}
            for genre, weight in await get_schedule_targets()
        ],
        "anti_genres": await get_schedule_anti_genres(),
        "current_program": await get_active_schedule_block(),
        "current_relay": current_username or None,
        "current_relay_platform": (
            current_platform if current_username else None
        ),
        "proposed_relay": candidate,
        "decision_reason": reason,
        "ranked": displayed_ranked,
    }


@app.get("/api/live")
async def live(all_platforms: bool = True):

    # /api/live is platform-neutral. TikTok, Twitch and any future supported
    # platforms are returned through the same public endpoint. Keep the legacy
    # all_platforms query parameter accepted for backwards compatibility, but
    # it no longer changes which platforms are visible.
    all_live_djs = await get_live_djs()
    live_djs = all_live_djs

    # Favourite/profile enrichment includes SQLite reads and historical
    # genre aggregation for every enabled DJ. Keep that work off the Uvicorn
    # event loop so /api/live cannot stall HLS/API servicing.
    favourites = await asyncio.to_thread(
        _load_live_favourites_with_learned_profiles
    )

    # Keep favourite_djs as the canonical profile store.
    favourite_genres = {
        (
            str(favourite.get("platform") or "TikTok").casefold(),
            str(favourite.get("username") or "").lstrip("@").strip().lower(),
        ): favourite
        for favourite in favourites
    }

    # Load latest AI audio detection results.
    ai_genres = {}

    ai_conn = get_db()

    try:
        ai_rows = ai_conn.execute("""
            SELECT platform, username, detected_at, genres_json
            FROM ai_genre_detection
        """).fetchall()

        for row in ai_rows:

            identity = (
                str(row["platform"] or "TikTok").casefold(),
                str(row["username"] or "").lstrip("@").strip().lower(),
            )

            try:
                genres = json.loads(
                    row["genres_json"] or "[]"
                )
            except Exception:
                genres = []

            if isinstance(genres, list):
                ai_genres[identity] = {
                    "detected_at": row["detected_at"],
                    "genres": genres,
                }

    except sqlite3.OperationalError:
        pass

    ai_conn.close()

    enriched_all_live_djs = []

    for dj in all_live_djs:

        enriched = dict(dj)

        username = str(
            dj.get("username") or ""
        ).lstrip("@").strip().lower()

        identity = (
            str(dj.get("platform") or "TikTok").casefold(),
            username,
        )
        favourite = favourite_genres.get(identity)
        ai = ai_genres.get(identity)

        # Profile/favourite data.
        if favourite:

            for field in ("genre", "genre_keywords"):

                value = favourite.get(field)

                if value is not None and str(value).strip():

                    value = str(value).strip()

                    if field == "genre" and "---" in value:
                        value = value.split("---", 1)[0].strip()

                    enriched[field] = value

        # AI audio detection overrides profile genre while LIVE.
        if ai and ai.get("genres"):

            top = ai["genres"][0]

            if isinstance(top, dict):

                raw_genre = str(
                    top.get("genre") or ""
                ).strip()

                confidence = top.get("confidence")

                if raw_genre:

                    if "---" in raw_genre:

                        primary, subgenre = raw_genre.split(
                            "---", 1
                        )

                        primary = primary.strip()
                        subgenre = subgenre.strip()

                        enriched["genre"] = primary
                        enriched["ai_genre"] = subgenre

                    else:

                        enriched["genre"] = raw_genre
                        enriched["ai_genre"] = raw_genre

                    enriched["ai_genre_raw"] = raw_genre
                    enriched["ai_genres"] = ai["genres"]
                    enriched["ai_genre_confidence"] = confidence
                    enriched["ai_genre_detected_at"] = (
                        ai["detected_at"]
                    )

        enriched_all_live_djs.append(enriched)

    visible_keys = {
        relay_identity_key(
            dj.get("platform") or "TikTok",
            dj.get("username"),
        )
        for dj in live_djs
    }
    live_djs = [
        dj for dj in enriched_all_live_djs
        if relay_identity_key(
            dj.get("platform") or "TikTok",
            dj.get("username"),
        ) in visible_keys
    ]

    live_lookup = {
        (
            str(dj.get("platform") or "").casefold(),
            str(dj.get("username") or "").casefold(),
        ): dj
        for dj in enriched_all_live_djs
    }

    for favourite in favourites:

        live_match = live_lookup.get(
            (
                str(favourite.get("platform") or "").casefold(),
                str(favourite.get("username") or "").casefold(),
            )
        )

        if live_match:

            favourite["live"] = True

            favourite["viewers"] = (
                live_match.get("viewers", 0)
            )

            favourite["started_at"] = (
                live_match.get("started_at")
            )

            favourite["updated_at"] = (
                live_match.get("updated_at")
            )

            if live_match.get("url"):

                favourite["live_url"] = (
                    live_match["url"]
                )

        else:

            favourite["live"] = False
            favourite["viewers"] = 0
            favourite["started_at"] = None
            favourite["updated_at"] = None

    # The relay may deliberately choose a different DJ from live_djs
    # because some TikTok streams are inaccessible (for example,
    # age-restricted accounts). Expose the actual relay DJ to the website
    # so the displayed name always matches the video being streamed.
    relay_dj = next(
        (
            dj for dj in enriched_all_live_djs
            if relay_username
            and relay_identity_key(
                dj.get("platform") or "TikTok",
                dj.get("username"),
            ) == relay_identity_key(
                relay_platform or "TikTok",
                relay_username,
            )
        ),
        None,
    )

    return {
        "live": live_djs,
        "favourites": favourites,
        "relay": relay_dj,
    }


# ---------------------------------------------------------
# Favourite DJs
# ---------------------------------------------------------

@app.get("/api/favourites")
def favourites():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM favourite_djs
        WHERE enabled = 1
        ORDER BY name COLLATE NOCASE
    """).fetchall()

    conn.close()

    favourites = []

    for row in rows:

        dj = dict(row)

        # Display only the primary genre.
        # Preserve the full database value internally.
        genre = dj.get("genre")

        if genre and "---" in genre:
            dj["genre"] = genre.split("---", 1)[0].strip()

        favourites.append(dj)

    return {
        "favourites": favourites
    }


@app.post("/api/favourites")
async def add_favourite(request: Request):

    data = await request.json()

    username = data.get("username")
    name = data.get("name")
    platform = data.get(
        "platform",
        "TikTok"
    )
    profile_url = data.get(
        "profile_url"
    )
    live_url = data.get(
        "live_url"
    )
    genre = data.get(
        "genre"
    )

    if not username or not name or not profile_url:

        return {
            "status": "error",
            "message": (
                "username, name and profile_url "
                "are required"
            ),
        }

    username = username.lstrip("@")

    conn = get_db()

    conn.execute("""
        INSERT INTO favourite_djs
        (
            username,
            name,
            platform,
            profile_url,
            live_url,
            genre,
            enabled,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?)

        ON CONFLICT(platform, username) DO UPDATE SET
            name = excluded.name,
            profile_url = excluded.profile_url,
            live_url = excluded.live_url,
            genre = excluded.genre,
            enabled = 1
    """, (
        username,
        name,
        platform,
        profile_url,
        live_url,
        genre,
        datetime.now(
            timezone.utc
        ).isoformat(),
    ))

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "username": username,
    }


@app.post("/api/favourites/import-tiktok")
async def import_tiktok_favourite(request: Request):

    try:
        data = await request.json()
    except Exception:
        return {
            "status": "error",
            "message": "Invalid JSON request",
        }

    username = data.get("username")

    if not username:
        return {
            "status": "error",
            "message": "username is required",
        }

    try:
        profile = await import_tiktok_dj(
            username
        )

        return {
            "status": "ok",
            "dj": profile,
        }

    except Exception as e:
        print(
            f"[TikTok] PROFILE IMPORT ERROR "
            f"@{str(username).lstrip('@')}: {e}"
        )

        return {
            "status": "error",
            "message": str(e),
            "username": str(username).lstrip("@"),
        }


# ---------------------------------------------------------
# Profile refresh endpoint
# ---------------------------------------------------------

@app.post("/api/favourites/{username}/refresh-profile")
async def refresh_tiktok_profile(username: str):

    username = username.lstrip("@")

    try:
        profile = await import_tiktok_dj(
            username
        )

        return {
            "status": "ok",
            "dj": profile,
        }

    except Exception as e:
        print(
            f"[TikTok] PROFILE REFRESH ERROR "
            f"@{username}: {e}"
        )

        return {
            "status": "error",
            "message": str(e),
            "username": username,
        }


@app.delete("/api/favourites/{username}")
def delete_favourite(
    username: str,
    platform: str = "TikTok",
):

    username = username.lstrip("@")
    platform = str(platform or "TikTok").strip()

    conn = get_db()

    result = conn.execute(
        """
        DELETE FROM favourite_djs
        WHERE lower(username) = lower(?)
          AND lower(platform) = lower(?)
        """,
        (
            username,
            platform,
        ),
    )

    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "deleted": result.rowcount > 0,
        "platform": platform,
        "username": username,
    }


# ---------------------------------------------------------
# Tik.Tools webhook
# ---------------------------------------------------------

@app.post("/hooks/tiktool")
async def tiktool_webhook(
    request: Request
):

    data = await request.json()

    event = data.get(
        "event"
    )

    # -----------------------------------------------------
    # LIVE START
    # -----------------------------------------------------

    if event == "live.start":

        creator = data.get(
            "creator",
            {}
        )

        live = data.get(
            "live",
            {}
        )

        username = creator.get(
            "unique_id"
        )

        name = creator.get(
            "nickname",
            username
        )

        if not username:

            return {
                "status": "error",
                "message": (
                    "No creator username supplied"
                ),
            }

        username = username.lstrip("@")

        now = datetime.now(
            timezone.utc
        ).isoformat()

        conn = get_db()

        conn.execute("""
            INSERT INTO live_djs
            (
                username,
                name,
                platform,
                url,
                genre,
                viewers,
                started_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)

            ON CONFLICT(platform, username) DO UPDATE SET
                name = excluded.name,
                url = excluded.url,
                viewers = excluded.viewers,
                updated_at = excluded.updated_at
        """, (
            username,
            name,
            "TikTok",
            live.get(
                "live_url"
            ),
            None,
            live.get(
                "viewer_count",
                0
            ),
            now,
            now,
        ))

        conn.commit()
        conn.close()

        return {
            "status": "ok",
            "event": "live.start",
            "dj": username,
        }


    # -----------------------------------------------------
    # LIVE END
    # -----------------------------------------------------

    if event == "live.end":

        creator = data.get(
            "creator",
            {}
        )

        username = creator.get(
            "unique_id"
        )

        if username:

            username = username.lstrip("@")

            conn = get_db()

            conn.execute(
                """
                DELETE FROM live_djs
                WHERE username = ?
                """,
                (
                    username,
                ),
            )

            conn.commit()
            conn.close()

        return {
            "status": "ok",
            "event": "live.end",
            "dj": username,
        }


    return {
        "status": "ignored",
        "event": event,
    }
