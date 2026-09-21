import os
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
from pydantic import BaseModel

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from TikTokLive import TikTokLiveClient
from radio_rrr_genre_matcher import rank_live_djs


DB_PATH = "/data/radiorouter.db"
CHECK_INTERVAL = 60

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

# A successful LIVE refresh keeps a row fresh. This is only a final
# safety net for rows that stop receiving successful refreshes because
# of an exception or monitor problem. Ten minutes gives the relay
# considerably more tolerance for temporary TikTok/API trouble.
LIVE_STALE_AFTER = 10 * 60

PROFILE_REFRESH_INTERVAL = 24 * 60 * 60
PROFILE_REFRESH_START_DELAY = 10
PROFILE_REFRESH_TIMEOUT = 60
PROFILE_REFRESH_CHECK_INTERVAL = 15 * 60

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

# Require the same candidate to win repeatedly before switching.
STAGE3_REQUIRED_WINS = 3
STAGE3_DECISION_INTERVAL = 30
STAGE3_FAILURE_COOLDOWN = 5 * 60

# A current relay remains protected if it still has a reasonable match.
STAGE3_CURRENT_MIN_SCORE = 5.0

# Genres/content explicitly avoided by a programme. These are editable per
# schedule slot in the Scheduler Admin. The non-music defaults cover common
# talking/Q&A content without requiring it to be treated as a music genre.
DEFAULT_ANTI_GENRES = [
    "Spoken Word", "Talk", "Talking", "Dialogue", "Comedy",
    "Audiobook", "Radioplay", "Education", "Poetry", "Religious",
    "Field Recording",
]
DEFAULT_DINNER_WARMUP_ANTI_GENRES = [
    "Hardcore", "Gabber", "Speedcore", "Hardstyle",
]
ANTI_GENRE_HARD_CONFIDENCE = 0.15
NON_MUSIC_ANTI_CONFIDENCE = 0.08


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
relay_lock = asyncio.Lock()
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

# Consecutive TikTok false-negative counters. These are intentionally
# in-memory: a process restart should not preserve a possibly stale
# offline count. A successful LIVE check always resets the counter.
offline_miss_counts = {}


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


def stop_relay(*, disconnect_audio_clients=False):
    global relay_process, relay_username, RELAY_PLAYLIST

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
    RELAY_PLAYLIST = RELAY_DIR / "index.m3u8"
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
        SELECT username, name, url, viewers, started_at, updated_at
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


async def get_tiktok_stream(username):
    client = TikTokLiveClient(unique_id=f"@{username}")

    # Authenticate TikTok web requests when a persistent session is available.
    session_file = Path("/data/tiktok_session.json")
    if session_file.exists():
        try:
            session = json.loads(session_file.read_text())
            session_id = session.get("sessionid")
            tt_target_idc = session.get("tt-target-idc")
            if session_id:
                client.web.set_session(session_id, tt_target_idc or None)
                print("[TikTok] Using authenticated session")
        except Exception as e:
            print(f"[TikTok] Session load warning: {e}")

    try:
        room_info = await client.web.fetch_room_info(
            unique_id=username
        )
    except Exception as e:
        if "Age restricted stream" not in str(e):
            raise
        print(f"[TikTok] Age-restricted stream detected for @{username} - using direct API fallback")
        return await get_tiktok_stream_fallback(
            username,
            headers=dict(client.web.headers),
            cookies=dict(client.web.cookies),
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

    headers = dict(client.web.headers)
    cookies = dict(client.web.cookies)

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

            if valid_segments:
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
    if not isolated:
        stop_relay()
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
        "-headers", http_headers,
        "-i", stream_url,
        "-c", "copy",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "30",
        "-hls_flags",
        "delete_segments+append_list+independent_segments",
        "-hls_segment_filename",
        str(RELAY_DIR / segment_pattern),
        str(playlist),
    ]

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


async def validate_and_promote_relay(stream_info, username):
    """Keep the broadcast intact until an isolated candidate produces HLS."""
    global relay_process, relay_username, RELAY_PLAYLIST
    process = None
    playlist = None
    promoted = False
    try:
        process, playlist = start_relay(stream_info, username, isolated=True)
        await wait_for_relay_ready(process, playlist, RELAY_DIR, timeout=15)
        if process.poll() is not None or not relay_output_is_healthy(playlist, RELAY_DIR):
            raise RuntimeError("Candidate stopped producing healthy HLS before promotion")

        previous_process = relay_process
        previous_playlist = RELAY_PLAYLIST
        stop_audio_relay()
        # No await between these assignments: requests see one complete relay.
        relay_process = process
        relay_username = username
        RELAY_PLAYLIST = playlist
        promoted = True

        # The validated FFmpeg keeps running; do not reconnect to TikTok.
        try:
            terminate_relay_process(previous_process)
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
                terminate_relay_process(process)
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

    process = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "warning",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_on_network_error", "1",
            "-reconnect_delay_max", "5",
            "-rw_timeout", "15000000",
            "-headers",
            "\\r\\n".join(header_lines) + "\\r\\n",
            "-i",
            stream_info["url"],
            "-c", "copy",
            "-f", "hls",
            "-hls_time", "2",
            "-hls_list_size", "30",
            "-hls_flags",
            "delete_segments+append_list+independent_segments",
            "-hls_segment_filename",
            str(relay_dir / "segment_%05d.ts"),
            str(playlist),
        ],
        stdout=subprocess.DEVNULL,
        stderr=None,
    )

    print(
        f"[Relay] Manual FFmpeg started for @{username}"
    )

    return process, playlist


async def ensure_manual_relay(username):
    username = username.lstrip("@").strip()

    async with manual_relay_lock:
        key = username.lower()

        existing = manual_relays.get(key)

        if existing and existing["process"].poll() is None:
            if relay_output_is_healthy(
                existing["playlist"],
                existing["relay_dir"],
            ):
                existing["last_used"] = datetime.now(timezone.utc)
                return existing

            print(
                f"[Relay] Existing manual relay for "
                f"@{username} is unhealthy; restarting"
            )

            try:
                existing["process"].terminate()
                existing["process"].wait(timeout=3)
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
                if str(dj.get("username") or "").lower()
                == key
            ),
            None,
        )

        if not live_dj:
            raise ValueError(
                "DJ is no longer live"
            )

        stream_info = await get_tiktok_stream(username)

        relay_dir = MANUAL_RELAY_DIR / key

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
                timeout=15,
            )

        except Exception as error:
            print(
                f"[Relay] Manual relay failed for "
                f"@{username}: {error}"
            )

            try:
                process.terminate()
                process.wait(timeout=3)
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
                f"Unable to start a working relay for "
                f"@{username}: {error}"
            )

        relay = {
            "process": process,
            "playlist": playlist,
            "relay_dir": relay_dir,
            "last_used": datetime.now(timezone.utc),
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


def apply_anti_genre_rules(ranked, enriched_live_djs, anti_genres):
    """Apply per-programme avoid rules to the ranked live-DJ candidates.

    Current live AI is used for exclusion/penalty; learned history is not used
    to ban a DJ because a DJ can legitimately change style between streams.
    """
    avoid_map = {_normalise_genre_label(x): str(x).strip() for x in anti_genres if str(x).strip()}
    if not avoid_map:
        return ranked

    live_lookup = {
        str(dj.get("username") or "").lstrip("@").strip().casefold(): dj
        for dj in enriched_live_djs
        if dj.get("username")
    }

    output = []
    for item in ranked:
        copy_item = dict(item)
        username = str(item.get("username") or "").lstrip("@").strip().casefold()
        dj = live_lookup.get(username, {})
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

            # Non-music content is treated more aggressively because it is
            # exactly what should cause a music programme to move on.
            threshold = NON_MUSIC_ANTI_CONFIDENCE if _is_non_music_label(raw) else ANTI_GENRE_HARD_CONFIDENCE
            if confidence >= threshold:
                hard_excluded = True

            max_penalty = max(max_penalty, confidence * (100.0 if _is_non_music_label(raw) else 70.0))

        base_score = float(copy_item.get("score") or 0.0)
        copy_item["anti_genre_hits"] = avoid_hits
        copy_item["anti_genre_excluded"] = hard_excluded
        copy_item["pre_anti_score"] = round(base_score, 2)

        if hard_excluded:
            # Keep the DJ visible in diagnostics, but make it impossible for
            # Stage 3 to select while the current audio is an avoided genre.
            copy_item["score"] = -1000.0
            copy_item["anti_genre_penalty"] = round(max_penalty, 2)
        else:
            penalty = min(35.0, max_penalty)
            copy_item["score"] = round(max(0.0, base_score - penalty), 2)
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
    live_djs = await get_live_djs()

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
        str(favourite.get("username") or "")
        .lstrip("@")
        .strip()
        .lower(): favourite
        for favourite in favourites
    }

    ai_genres = {}
    ai_conn = get_db()

    try:
        ai_rows = ai_conn.execute("""
            SELECT username, detected_at, genres_json
            FROM ai_genre_detection
        """).fetchall()

        for row in ai_rows:
            username = (
                str(row["username"] or "")
                .lstrip("@")
                .strip()
                .lower()
            )

            try:
                genres = json.loads(
                    row["genres_json"] or "[]"
                )
            except Exception:
                genres = []

            if isinstance(genres, list):
                ai_genres[username] = {
                    "detected_at": row["detected_at"],
                    "genres": genres,
                }
    except sqlite3.OperationalError:
        pass
    finally:
        ai_conn.close()

    enriched_live_djs = []

    for dj in live_djs:
        enriched = dict(dj)

        username = (
            str(dj.get("username") or "")
            .lstrip("@")
            .strip()
            .lower()
        )

        favourite = favourite_genres.get(username)
        ai = ai_genres.get(username)

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

        learned = get_learned_genre_profile(
            username,
            days=30,
            limit=20,
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

    ranked = rank_live_djs(
        enriched_live_djs,
        targets=targets,
    )
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


def stage3_adjusted_ranking(ranked, enriched_live_djs, now=None):
    """One score calculation for candidates, the current relay, and the UI."""
    now = now or datetime.now(timezone.utc)
    live_by_key = {
        str(dj.get("username") or "").lstrip("@").strip().lower(): dj
        for dj in enriched_live_djs if dj.get("username")
    }
    adjusted_ranked = []
    for item in ranked:
        key = str(item.get("username") or "").lstrip("@").strip().lower()
        dj = live_by_key.get(key)
        if not dj:
            continue
        freshness = ai_freshness_factor(dj, now)
        profile_weight = 0.20 + 0.25 * (1.0 - freshness)
        ai_weight = 0.70
        viewer_weight = max(0.0, 1.0 - ai_weight - profile_weight)
        score = round(
            float(item.get("ai_score") or 0.0) * freshness * ai_weight
            + float(item.get("profile_score") or 0.0) * profile_weight
            + float(item.get("viewer_bonus") or 0.0) * viewer_weight,
            2,
        )
        adjusted = dict(item)
        adjusted["raw_score"] = round(float(item.get("score") or 0.0), 2)
        adjusted["ai_freshness"] = round(freshness, 3)
        adjusted["profile_driven"] = (
            freshness == 0.0 and int(item.get("learned_samples") or 0) >= 3
        )
        adjusted["score"] = (
            -1000.0 if item.get("anti_genre_excluded") else
            round(max(0.0, score - float(item.get("anti_genre_penalty") or 0.0)), 2)
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
        key = str(item.get("username") or "").lstrip("@").strip().lower()
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


def stage3_switch_decision(candidate, current_item, current_username, healthy):
    if not candidate:
        return False, "no_eligible_candidate"
    candidate_key = str(candidate.get("username") or "").lstrip("@").strip().lower()
    current_key = str(current_username or "").lstrip("@").strip().lower()
    if not healthy:
        return True, "no_healthy_relay"
    if candidate_key == current_key:
        return False, "current_relay_is_best"
    candidate_score = float(candidate.get("score") or 0)
    current_score = float(current_item.get("score") or 0) if current_item else 0.0
    if current_score < STAGE3_CURRENT_MIN_SCORE:
        # A low score removes the large continuity margin, but must never
        # authorise a downgrade or a tie when a higher DJ is cooling down.
        return candidate_score > current_score, "below_quality_floor"
    if candidate_score >= current_score + STAGE3_SWITCH_MARGIN:
        return True, "better_match"
    return False, "current_relay_protected"


async def relay_monitor():
    global relay_process, relay_username

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

    stage3_candidate_username = None
    stage3_candidate_wins = 0

    while True:
        try:
            async with relay_lock:
                cleanup_retired_relay_outputs()
                live_djs = await get_live_djs()
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
                            if str(dj["username"]).lower()
                            == str(relay_username or "").lower()
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
                            stop_relay()

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
                        stop_relay()

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
                    stage3_candidate_username = None
                    stage3_candidate_wins = 0
                    if not relay_is_healthy:
                        stop_relay()
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
                        current_key = str(relay_username or "").lstrip("@").strip().lower()
                        current_item = next((
                            item for item in adjusted_ranked
                            if str(item.get("username") or "").lstrip("@").strip().lower() == current_key
                        ), None)
                        should_switch, reason = stage3_switch_decision(
                            candidate, current_item, relay_username, relay_is_healthy,
                        )
                        if not should_switch:
                            stage3_candidate_username = None
                            stage3_candidate_wins = 0
                        else:
                            candidate_username = str(candidate["username"]).lstrip("@").strip()
                            candidate_key = candidate_username.lower()
                            if stage3_candidate_username == candidate_key:
                                stage3_candidate_wins = min(
                                    STAGE3_REQUIRED_WINS, stage3_candidate_wins + 1,
                                )
                            else:
                                stage3_candidate_username = candidate_key
                                stage3_candidate_wins = 1
                            print(
                                f"[Stage 3] Candidate @{candidate_username} "
                                f"score={candidate['score']:.2f} "
                                f"wins={stage3_candidate_wins}/{STAGE3_REQUIRED_WINS} ({reason})"
                            )
                            if stage3_candidate_wins >= STAGE3_REQUIRED_WINS:
                                try:
                                    print(f"[Stage 3] Validating @{candidate_username} as new default relay")
                                    stream_info = await get_tiktok_stream(candidate_username)
                                    await validate_and_promote_relay(stream_info, candidate_username)
                                    selected = True
                                    relay_missing_since = None
                                    failed_until.pop(candidate_key, None)
                                    print(
                                        f"[Stage 3] Default relay switched to @{candidate_username} "
                                        f"(score={candidate['score']:.2f})"
                                    )
                                except Exception as error:
                                    failed_until[candidate_key] = (
                                        datetime.now(timezone.utc) + timedelta(seconds=failure_cooldown)
                                    )
                                    print(
                                        f"[Stage 3] Candidate @{candidate_username} failed validation: "
                                        f"{error}; skipping for {failure_cooldown}s"
                                    )
                                finally:
                                    stage3_candidate_username = None
                                    stage3_candidate_wins = 0
                    except Exception as error:
                        stage3_candidate_username = None
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

                        key = username.lower()
                        blocked_until = failed_until.get(key)

                        if blocked_until and now < blocked_until:
                            continue

                        try:
                            print(
                                f"[Relay] Safety fallback testing "
                                f"@{username}"
                            )

                            stream_info = await get_tiktok_stream(
                                username
                            )

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
                            start_audio_relay()

                            print(
                                f"[Relay] Safety fallback validated "
                                f"for @{username}"
                            )

                            selected = True
                            break

                        except Exception as error:
                            print(
                                f"[Relay] Safety fallback skipped "
                                f"@{username}: {error}"
                            )

                            failed_until[key] = (
                                datetime.now(timezone.utc)
                                + timedelta(
                                    seconds=failure_cooldown
                                )
                            )

                            stop_relay()

                if not selected and (
                    relay_process is None
                    or relay_process.poll() is not None
                    or not relay_output_is_healthy(
                        RELAY_PLAYLIST,
                        RELAY_DIR,
                    )
                ):
                    print(
                        "[Relay] No working "
                        "TikTok relays available"
                    )
                    stop_relay()

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

    client = TikTokLiveClient(
        unique_id=f"@{username}"
    )
    room_info = await client.web.fetch_room_info(
        unique_id=username
    )

    profile = extract_dj_profile(
        room_info,
        username,
    )

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
                """,
                (profile_pic, actual_username),
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

        ON CONFLICT(username) DO UPDATE SET
            name = excluded.name,
            platform = excluded.platform,
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
    """Refresh existing TikTok favourites whose profile data is stale."""
    if profile_refresh_job_lock.locked():
        print("[TikTok] DAILY PROFILE REFRESH already running; skipping")
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
        due_rows = [
            row for row in rows
            if profile_refresh_is_due(
                row["profile_updated_at"],
                now,
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

            try:
                await asyncio.wait_for(
                    import_tiktok_dj(username),
                    timeout=PROFILE_REFRESH_TIMEOUT,
                )
                print(
                    f"[TikTok] DAILY PROFILE REFRESHED @{username}"
                )
            except asyncio.TimeoutError:
                print(
                    f"[TikTok] DAILY PROFILE REFRESH TIMEOUT "
                    f"@{username} after {PROFILE_REFRESH_TIMEOUT}s"
                )
            except Exception as e:
                print(
                    f"[TikTok] DAILY PROFILE REFRESH ERROR "
                    f"@{username}: {e}"
                )

            await asyncio.sleep(0)


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
        SELECT id, day, start_minutes, end_minutes, name, genres_json, genre_weights_json, anti_genres_json, enabled
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
        }
        if row["day"] == "Sunday":
            profiles["sunday"].append(block)
        elif row["day"] in ("Friday", "Saturday"):
            profiles["fridaySaturday"].append(block)
        else:
            profiles["weekday"].append(block)
    return profiles

def init_db():
    conn = get_db()

    _seed_scheduler(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_genre_detection (
            username TEXT PRIMARY KEY,
            detected_at TEXT NOT NULL,
            genres_json TEXT NOT NULL
        )
    """)

    # Preserve every AI genre detection so RRR can build a historical
    # evidence base for each DJ. The existing ai_genre_detection table
    # remains the latest-result cache used by the live API.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_genre_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            detected_at TEXT NOT NULL,
            genres_json TEXT NOT NULL
        )
    """)

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
            username TEXT UNIQUE NOT NULL,
            name TEXT,
            platform TEXT NOT NULL,
            url TEXT,
            genre TEXT,
            viewers INTEGER DEFAULT 0,
            started_at TEXT,
            updated_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS favourite_djs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            live_url TEXT,
            genre TEXT,
            enabled INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)

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

            ON CONFLICT(username) DO UPDATE SET
                name = excluded.name,
                platform = excluded.platform,
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


# Prevent repeated profile refresh attempts while a DJ remains live.
profile_picture_refresh_attempted = set()


async def ensure_favourite_profile_picture(username):
    """Refresh a missing favourite DJ profile picture once per live session."""
    username = str(username or "").strip().lstrip("@")

    if not username or username in profile_picture_refresh_attempted:
        return

    conn = get_db()

    try:
        favourite = conn.execute(
            """
            SELECT profile_pic
            FROM favourite_djs
            WHERE lower(username) = lower(?)
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

    # Mark before awaiting the network request so a slow or failing refresh
    # cannot be started again by another live polling cycle.
    profile_picture_refresh_attempted.add(username)

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
        print(
            f"[TikTok] Could not refresh profile picture for @{username}: {e}"
        )


async def check_tiktok_dj(username, name, live_url):

    try:

        client = TikTokLiveClient(
            unique_id=f"@{username}"
        )

        is_live = await client.is_live()

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

                ON CONFLICT(username) DO UPDATE SET
                    name = excluded.name,
                    platform = excluded.platform,
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
                str(relay_username or "").lower()
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
            """, (
                username,
            )).fetchone()

            if existing and miss_count >= required:

                profile_picture_refresh_attempted.discard(username)

                conn.execute("""
                    DELETE FROM live_djs
                    WHERE username = ?
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

    except Exception as e:

        # Exceptions do not count as OFFLINE confirmations. A network,
        # API, or TikTokLive library error is fundamentally different
        # from a confirmed offline response.
        print(
            f"[TikTok] CHECK ERROR @{username}: {e}"
        )


async def tiktok_monitor():

    print(
        "[TikTok] Free LIVE monitor starting..."
    )

    await asyncio.sleep(5)

    while True:

        try:

            conn = get_db()

            rows = conn.execute("""
                SELECT
                    username,
                    name,
                    live_url
                FROM favourite_djs
                WHERE enabled = 1
                  AND platform = 'TikTok'
            """).fetchall()

            conn.close()

            for row in rows:

                await check_tiktok_dj(
                    row["username"],
                    row["name"],
                    row["live_url"],
                )

                await asyncio.sleep(2)

            # Safety cleanup for rows whose status has not been
            # successfully refreshed recently. This is deliberately
            # separate from check_tiktok_dj() so a TikTok exception
            # cannot leave a DJ stuck in LIVE forever.
            stale_cutoff = (
                datetime.now(timezone.utc)
                - timedelta(seconds=LIVE_STALE_AFTER)
            ).isoformat()

            conn = get_db()

            stale_rows = conn.execute("""
                SELECT username
                FROM live_djs
                WHERE updated_at IS NULL
                   OR updated_at < ?
            """, (stale_cutoff,)).fetchall()

            if stale_rows:
                # Never let the generic stale-row safety net remove the
                # DJ currently feeding a healthy relay. The active relay
                # has its own liveness confirmation protection.
                active_username = str(relay_username or "").lower()

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
                        WHERE username IN ({placeholders})
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

            # Keep the in-memory confirmation map bounded to enabled
            # TikTok favourites.
            enabled_usernames = {
                str(row["username"])
                for row in rows
            }
            for username in list(offline_miss_counts):
                if username not in enabled_usernames:
                    offline_miss_counts.pop(username, None)

            conn.close()

        except Exception as e:

            print(
                f"[TikTok] MONITOR ERROR: {e}"
            )

        await asyncio.sleep(
            CHECK_INTERVAL
        )


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
                            process.wait(timeout=3)
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
        "[Relay] Background relay started"
    )

    try:

        yield

    finally:

        monitor_task.cancel()
        profile_refresh_task.cancel()
        relay_task.cancel()
        manual_cleanup_task.cancel()

        try:
            await monitor_task
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
async def switch_live_relay(dj: str = None):
    """
    Switch the authoritative Radio RRR relay to a specific live DJ.

    The candidate relay is fully validated before it becomes authoritative.
    If the candidate fails, the previous relay is restored when possible.
    """
    global relay_process, relay_username

    username = str(dj or "").lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="DJ username is required")

    key = username.lower()

    async with relay_lock:
        live_djs = await get_live_djs()
        live_dj = next(
            (
                item for item in live_djs
                if str(item.get("username") or "").lstrip("@").lower() == key
            ),
            None,
        )

        if not live_dj:
            raise HTTPException(status_code=404, detail="DJ is no longer live")

        current_username = str(relay_username or "").lstrip("@").strip()
        current_key = current_username.lower()

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

        # Capture the current relay source before replacing it so we can
        # restore the station if the requested DJ fails relay validation.
        previous_username = current_username
        previous_stream_info = None

        if previous_username and current_key != key:
            try:
                previous_stream_info = await get_tiktok_stream(previous_username)
            except Exception as error:
                print(
                    f"[Relay] Could not prefetch previous relay "
                    f"@{previous_username} for rollback: {error}"
                )

        try:
            print(f"[Relay] Manual global switch requested: @{username}")

            stream_info = await get_tiktok_stream(username)

            process, playlist = start_relay(
                stream_info,
                username,
            )

            try:
                await wait_for_relay_ready(
                    process,
                    playlist,
                    RELAY_DIR,
                    timeout=15,
                )
            except Exception as error:
                print(
                    f"[Relay] Requested DJ @{username} failed "
                    f"relay validation: {error}"
                )
                stop_relay()
                raise RuntimeError(
                    f"Unable to start a working relay for @{username}: {error}"
                )

            relay_username = username
            start_audio_relay()

            print(
                f"[Relay] Global relay switched and validated for @{username}"
            )

            return {
                "ok": True,
                "relay": live_dj,
                "message": f"Radio RRR relay switched to @{username}",
            }

        except HTTPException:
            raise
        except Exception as error:
            # Never leave the station pointed at an unvalidated candidate.
            stop_relay()

            if previous_username and previous_stream_info:
                try:
                    print(
                        f"[Relay] Restoring previous relay "
                        f"@{previous_username}"
                    )

                    process, playlist = start_relay(
                        previous_stream_info,
                        previous_username,
                    )

                    await wait_for_relay_ready(
                        process,
                        playlist,
                        RELAY_DIR,
                        timeout=15,
                    )

                    relay_username = previous_username
                    start_audio_relay()

                    print(
                        f"[Relay] Previous relay restored: "
                        f"@{previous_username}"
                    )
                except Exception as rollback_error:
                    print(
                        f"[Relay] Rollback failed for "
                        f"@{previous_username}: {rollback_error}"
                    )
                    stop_relay()

            raise HTTPException(
                status_code=503,
                detail=str(error),
            )


@app.get("/api/live-stream")
async def live_stream(dj: str = None):
    if dj:
        try:
            relay = await ensure_manual_relay(dj)
        except Exception as error:
            return PlainTextResponse(str(error), status_code=503)
        playlist_path = relay["playlist"]
        playlist_prefix = "/api/live-stream/" + quote(dj.lstrip("@").strip(), safe="") + "/"
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
    lines = []
    for line in playlist.splitlines():
        if line.endswith(".ts") and not line.startswith("/"):
            line = playlist_prefix + line
        lines.append(line)
    playlist = "\n".join(lines) + "\n"

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

    if not target.exists() or not target.is_file():
        return PlainTextResponse("Not found", status_code=404)

    return FileResponse(
        target,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/api/live-stream/{dj}/{filename}")
def manual_live_stream_segment(dj: str, filename: str):
    relay = manual_relays.get(dj.lower())
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
# Scheduler admin
# ---------------------------------------------------------

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
$('overlay').addEventListener('click',e=>{if(e.target.id==='overlay')close()});load();
</script>
</body></html>"""

@app.get("/admin", response_class=HTMLResponse)
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
async def release_manual_live_stream(dj: str = None):
    """Release a temporary per-DJ relay used by the AI candidate scanner."""
    username = str(dj or "").lstrip("@").strip()
    if not username:
        raise HTTPException(status_code=400, detail="DJ username is required")

    key = username.lower()

    async with manual_relay_lock:
        relay = manual_relays.pop(key, None)
        if relay:
            process = relay.get("process")
            try:
                if process and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=3)
            except Exception:
                try:
                    if process:
                        process.kill()
                except Exception:
                    pass
            shutil.rmtree(relay.get("relay_dir"), ignore_errors=True)

    return {"ok": True, "username": username, "released": bool(relay)}


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

    username = str(
        payload.get("username") or ""
    ).lstrip("@").strip()

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

    if not isinstance(genres, list):
        genres = []

    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_genre_detection (
            username TEXT PRIMARY KEY,
            detected_at TEXT NOT NULL,
            genres_json TEXT NOT NULL
        )
    """)

    conn.execute("""
        INSERT INTO ai_genre_detection
        (username, detected_at, genres_json)
        VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            detected_at = excluded.detected_at,
            genres_json = excluded.genres_json
    """, (
        username,
        detected_at,
        json.dumps(genres),
    ))

    # Preserve this detection as historical evidence. Unlike
    # ai_genre_detection, this table never overwrites an earlier result.
    conn.execute("""
        INSERT INTO ai_genre_observations
        (username, detected_at, genres_json)
        VALUES (?, ?, ?)
    """, (
        username,
        detected_at,
        json.dumps(genres),
    ))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "username": username,
        "detected_at": detected_at,
        "genres": genres,
    }



# ------------------------------------------------------------
# AI GENRE GET API
# Returns the latest stored AI genre analysis for a DJ.
# ------------------------------------------------------------
@app.get("/api/ai-genre")
def get_ai_genre(username: str):
    username = str(username or "").strip().lstrip("@").lower()

    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    conn = get_db()

    row = conn.execute("""
        SELECT username, detected_at, genres_json
        FROM ai_genre_detection
        WHERE lower(ltrim(username, '@')) = ?
    """, (username,)).fetchone()

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
        "username": row["username"],
        "detected_at": row["detected_at"],
        "genres": genres
    }


# ------------------------------------------------------------
# AI GENRE HISTORY API
# Returns historical AI genre observations for a DJ.
# ------------------------------------------------------------
@app.get("/api/ai-genre/history")
def get_ai_genre_history(username: str, limit: int = 100):
    username = str(username or "").strip().lstrip("@").lower()

    if not username:
        raise HTTPException(status_code=400, detail="username is required")

    # Keep the development/debug endpoint bounded.
    limit = max(1, min(int(limit), 500))

    conn = get_db()

    try:
        rows = conn.execute("""
            SELECT id, username, detected_at, genres_json
            FROM ai_genre_observations
            WHERE lower(ltrim(username, '@')) = ?
            ORDER BY detected_at DESC, id DESC
            LIMIT ?
        """, (username, limit)).fetchall()
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
            "username": row["username"],
            "detected_at": row["detected_at"],
            "genres": genres,
        })

    return {
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
):
    username = str(username or "").strip().lstrip("@").lower()

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
                WHERE lower(ltrim(username, '@')) = ?
                  AND detected_at >= ?
                ORDER BY detected_at ASC, id ASC
                """,
                (username, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, detected_at, genres_json
                FROM ai_genre_observations
                WHERE lower(ltrim(username, '@')) = ?
                ORDER BY detected_at ASC, id ASC
                """,
                (username,),
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
        WHERE lower(ltrim(username, '@')) = lower(?)
    """, (
        genre,
        username,
    ))

    conn.commit()
    conn.close()

    return {
        "ok": True,
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
def get_learned_genre_profile(username: str, days: int = 30, limit: int = 20):
    username = str(username or "").strip().lstrip("@").lower()

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
                WHERE lower(ltrim(username, "@")) = ?
                  AND detected_at >= ?
                ORDER BY detected_at ASC, id ASC
                """,
                (username, cutoff),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, detected_at, genres_json
                FROM ai_genre_observations
                WHERE lower(ltrim(username, "@")) = ?
                ORDER BY detected_at ASC, id ASC
                """,
                (username,),
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

    now = datetime.now(timezone.utc)
    displayed_ranked = stage3_adjusted_ranking(ranked, enriched_live_djs, now)
    current_item = next((
        item for item in displayed_ranked
        if str(item.get("username") or "").lstrip("@").strip().lower() == current_username.lower()
    ), None)
    candidate, reason = select_stage3_candidate(ranked, enriched_live_djs, now=now)
    healthy = (
        relay_process is not None and relay_process.poll() is None
        and bool(relay_username)
        and relay_output_is_healthy(RELAY_PLAYLIST, RELAY_DIR)
    )
    _, reason = stage3_switch_decision(candidate, current_item, current_username, healthy)
    for item in displayed_ranked:
        key = str(item.get("username") or "").lstrip("@").strip().lower()
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
        "current_relay": current_username or None,
        "proposed_relay": candidate,
        "decision_reason": reason,
        "ranked": displayed_ranked,
    }


@app.get("/api/live")
async def live():

    # Use the same freshness rule as the relay layer so stale database
    # rows can never be presented to the website as LIVE.
    live_djs = await get_live_djs()

    conn = get_db()

    favourite_rows = conn.execute("""
        SELECT *
        FROM favourite_djs
        WHERE enabled = 1
        ORDER BY name COLLATE NOCASE
    """).fetchall()

    conn.close()

    favourites = [
        dict(row)
        for row in favourite_rows
    ]

    # Profile text is only the bootstrap genre source. Once Radio RRR has
    # detector history for a DJ, expose that learned history as the DJ's
    # persistent catalogue genres so it remains available while OFFLINE.
    # days=0 intentionally uses all stored observations rather than allowing
    # an established DJ profile to disappear after a period of inactivity.
    for favourite in favourites:
        learned = get_learned_genre_profile(
            favourite.get("username"),
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

    # Keep favourite_djs as the canonical profile store.
    favourite_genres = {
        str(favourite.get("username") or "")
        .lstrip("@")
        .strip()
        .lower(): favourite
        for favourite in favourites
    }

    # Load latest AI audio detection results.
    ai_genres = {}

    ai_conn = get_db()

    try:
        ai_rows = ai_conn.execute("""
            SELECT username, detected_at, genres_json
            FROM ai_genre_detection
        """).fetchall()

        for row in ai_rows:

            username = str(
                row["username"] or ""
            ).lstrip("@").strip().lower()

            try:
                genres = json.loads(
                    row["genres_json"] or "[]"
                )
            except Exception:
                genres = []

            if isinstance(genres, list):
                ai_genres[username] = {
                    "detected_at": row["detected_at"],
                    "genres": genres,
                }

    except sqlite3.OperationalError:
        pass

    ai_conn.close()

    enriched_live_djs = []

    for dj in live_djs:

        enriched = dict(dj)

        username = str(
            dj.get("username") or ""
        ).lstrip("@").strip().lower()

        favourite = favourite_genres.get(username)
        ai = ai_genres.get(username)

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

        enriched_live_djs.append(enriched)

    live_djs = enriched_live_djs

    live_lookup = {
        dj["username"].lower(): dj
        for dj in live_djs
    }

    for favourite in favourites:

        live_match = live_lookup.get(
            favourite["username"].lower()
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
            dj for dj in live_djs
            if relay_username
            and dj["username"].lower() == relay_username.lower()
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

        ON CONFLICT(username) DO UPDATE SET
            name = excluded.name,
            platform = excluded.platform,
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
    username: str
):

    username = username.lstrip("@")

    conn = get_db()

    result = conn.execute(
        """
        DELETE FROM favourite_djs
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
        "deleted": result.rowcount > 0,
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

            ON CONFLICT(username) DO UPDATE SET
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
