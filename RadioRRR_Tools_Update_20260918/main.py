from contextlib import asynccontextmanager
import asyncio
import httpx
import sqlite3
import json
import shutil
import subprocess
from urllib.parse import quote
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from TikTokLive import TikTokLiveClient


DB_PATH = "/data/radiorouter.db"
CHECK_INTERVAL = 60
PROFILE_REFRESH_INTERVAL = 24 * 60 * 60
PROFILE_REFRESH_START_DELAY = 10
PROFILE_REFRESH_TIMEOUT = 60
PROFILE_REFRESH_CHECK_INTERVAL = 15 * 60


# ---------------------------------------------------------
# LIVE DJ stream relay
# ---------------------------------------------------------

RELAY_DIR = Path("/tmp/radiorouter-live")
RELAY_PLAYLIST = RELAY_DIR / "index.m3u8"
MANUAL_RELAY_DIR = Path("/tmp/radiorouter-manual")
relay_process = None
relay_username = None
relay_lock = asyncio.Lock()

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


def stop_audio_relay():
    global audio_process, audio_task

    # Disconnect existing MP3 listeners cleanly when the DJ relay changes.
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


def stop_relay():
    global relay_process, relay_username

    stop_audio_relay()

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
    conn = get_db()

    rows = conn.execute("""
        SELECT username, name, url, viewers, started_at
        FROM live_djs
        ORDER BY viewers DESC, started_at ASC
    """).fetchall()

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
        async with audio_clients_lock:
            clients = list(audio_clients)

        for queue in clients:
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

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


def start_relay(stream_info, username):
    global relay_process, relay_username

    stream_url = stream_info["url"]
    stream_type = stream_info.get("type", "unknown")
    headers = stream_info.get("headers", {})
    cookies = stream_info.get("cookies", {})

    stop_relay()
    RELAY_DIR.mkdir(
        parents=True,
        exist_ok=True
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
        str(
            RELAY_DIR / "segment_%05d.ts"
        ),
        str(RELAY_PLAYLIST),
    ]

    relay_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=None,
    )

    # The MP3 encoder reads the local HLS output, avoiding a second TikTok
    # connection while providing a conventional internet-radio stream.
    start_audio_relay()

    relay_username = username

    print(
        f"[Relay] FFmpeg started for "
        f"@{username} ({stream_type})"
    )


def start_manual_relay(stream_info, username, relay_dir):
    relay_dir.mkdir(parents=True, exist_ok=True)
    playlist = relay_dir / "index.m3u8"
    header_lines = []

    for key, value in stream_info.get("headers", {}).items():
        if key.lower() not in ("connection", "content-length", "host"):
            header_lines.append(f"{key}: {value}")

    cookies = stream_info.get("cookies", {})
    if cookies:
        header_lines.append("Cookie: " + "; ".join(
            f"{key}={value}" for key, value in cookies.items()
        ))

    process = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "warning",
        "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5", "-rw_timeout", "15000000",
        "-headers", "\\r\\n".join(header_lines) + "\\r\\n",
        "-i", stream_info["url"], "-c", "copy", "-f", "hls",
        "-hls_time", "2", "-hls_list_size", "30", "-hls_flags",
        "delete_segments+append_list+independent_segments",
        "-hls_segment_filename", str(relay_dir / "segment_%05d.ts"),
        str(playlist),
    ], stdout=subprocess.DEVNULL, stderr=None)

    print(f"[Relay] Manual FFmpeg started for @{username}")
    return process, playlist


async def ensure_manual_relay(username):
    username = username.lstrip("@").strip()
    async with manual_relay_lock:
        existing = manual_relays.get(username.lower())
        if existing and existing["process"].poll() is None:
            existing["last_used"] = datetime.now(timezone.utc)
            return existing

        live_dj = next(
            (dj for dj in await get_live_djs()
             if str(dj.get("username") or "").lower() == username.lower()),
            None,
        )
        if not live_dj:
            raise ValueError("DJ is no longer live")

        stream_info = await get_tiktok_stream(username)
        relay_dir = MANUAL_RELAY_DIR / username.lower()
        process, playlist = start_manual_relay(stream_info, username, relay_dir)
        relay = {
            "process": process,
            "playlist": playlist,
            "relay_dir": relay_dir,
            "last_used": datetime.now(timezone.utc),
        }
        manual_relays[username.lower()] = relay
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


async def relay_monitor():
    print(
        "[Relay] Live DJ relay starting..."
    )

    while True:
        try:
            async with relay_lock:
                live_djs = await get_live_djs()

                # Keep the current relay running while its DJ remains live.
                if (
                    relay_process is not None
                    and relay_process.poll() is None
                ):
                    current = next(
                        (
                            dj for dj in live_djs
                            if dj["username"]
                            == relay_username
                        ),
                        None,
                    )

                    if current is not None:
                        pass
                    else:
                        print(
                            f"[Relay] @{relay_username} "
                            "is no longer live"
                        )
                        stop_relay()

                if (
                    relay_process is not None
                    and relay_process.poll() is None
                ):
                    # Current relay is healthy enough to keep trying.
                    pass
                elif not live_djs:
                    stop_relay()
                else:
                    selected = None

                    for dj in live_djs:
                        try:
                            print(
                                f"[Relay] Testing "
                                f"@{dj['username']}"
                            )

                            stream_info = (
                                await get_tiktok_stream(
                                    dj["username"]
                                )
                            )

                            selected = (
                                dj,
                                stream_info,
                            )

                            print(
                                f"[Relay] Stream available "
                                f"for @{dj['username']} "
                                f"({stream_info.get('type')})"
                            )

                            break

                        except Exception as e:
                            print(
                                f"[Relay] Skipping "
                                f"@{dj['username']}: {e}"
                            )

                    if selected is None:
                        print(
                            "[Relay] No accessible "
                            "TikTok streams"
                        )
                        stop_relay()
                    else:
                        dj, stream_info = selected

                        print(
                            f"[Relay] Switching to "
                            f"@{dj['username']}"
                        )

                        start_relay(
                            stream_info,
                            dj["username"],
                        )

            await asyncio.sleep(10)

        except asyncio.CancelledError:
            raise

        except Exception as e:
            print(
                f"[Relay] MONITOR ERROR: {e}"
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


def init_db():
    conn = get_db()

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

    # Radio RRR schedule.  The admin UI edits this table directly so the
    # station schedule no longer needs to be hard-coded into the website.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day_of_week INTEGER NOT NULL,
            start_minute INTEGER NOT NULL,
            end_minute INTEGER NOT NULL,
            name TEXT NOT NULL,
            genres_json TEXT NOT NULL DEFAULT '[]',
            enabled INTEGER NOT NULL DEFAULT 1,
            UNIQUE(day_of_week, start_minute, end_minute)
        )
    """)

    # Seed the schedule once from the current Radio RRR programming.
    # Python weekday: Monday=0 ... Sunday=6.
    if conn.execute("SELECT COUNT(*) FROM schedule").fetchone()[0] == 0:
        seed_schedule = {
            "weekday": [
                (0, 4, "Late Nights Insomnia", ["Chill", "Deep House", "Melodic", "Progressive"]),
                (4, 8, "Sunrise Sessions", ["Chill", "Downtempo", "Ambient", "Deep House"]),
                (8, 12, "Day Drive", ["80s", "Synthwave", "Chill Electro", "Nu-Disco"]),
                (12, 16, "Afternoon Beats", ["House", "Deep House", "Progressive", "Funky House"]),
                (16, 20, "Dinner Warm ups", ["House", "Tech House", "Trance", "Progressive"]),
                (20, 24, "Prime Time", ["Electro", "Techno", "Trance", "Progressive"]),
            ],
            "friday_saturday": [
                (0, 4, "After Dark", ["Techno", "Hard Techno", "Trance", "Psy-Trance"]),
                (4, 8, "Late Mornings", ["Techno", "Trance", "Progressive", "Psy-Trance"]),
                (8, 12, "Morning", ["Chill", "House", "Progressive", "Melodic"]),
                (12, 16, "Day Party", ["House", "Tech House", "Progressive", "Electro"]),
                (16, 20, "Prime Time", ["Tech House", "Techno", "Trance", "Progressive"]),
                (20, 24, "Party Night", ["Techno", "Trance", "BASSLINE", "Drum & Bass"]),
            ],
            "sunday": [
                (0, 4, "Late Night", ["Techno", "Trance", "Progressive", "Psy-Trance"]),
                (4, 8, "After Hours", ["Deep House", "Progressive", "Melodic", "Chill"]),
                (8, 12, "Sunday Morning", ["Chill", "Downtempo", "Deep House", "Ambient"]),
                (12, 16, "Sunday Session", ["House", "Deep House", "Progressive", "Organic House"]),
                (16, 20, "Sunday Sunset", ["Melodic", "Progressive", "Deep House", "Chill"]),
                (20, 24, "Sunday Night", ["Chill", "Deep House", "Progressive", "Trance"]),
            ],
        }
        for day in range(7):
            if day <= 3:
                blocks = seed_schedule["weekday"]
            elif day <= 5:
                blocks = seed_schedule["friday_saturday"]
            else:
                blocks = seed_schedule["sunday"]
            for start, end, name, genres in blocks:
                conn.execute("""
                    INSERT INTO schedule
                    (day_of_week, start_minute, end_minute, name, genres_json)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    day,
                    start * 60,
                    end * 60,
                    name,
                    json.dumps(genres),
                ))

    # Add profile metadata columns to existing databases without
    # recreating or losing the current favourite_djs records.
    ensure_profile_columns(conn)

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

            profile_picture_refresh_attempted.discard(username)

            conn.execute("""
                DELETE FROM live_djs
                WHERE username = ?
            """, (
                username,
            ))

            print(
                f"[TikTok] OFFLINE: @{username}"
            )

        conn.commit()
        conn.close()

    except Exception as e:

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

        stop_relay()
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


@app.get("/health")
def health():

    return {
        "status": "ok",
    }


@app.get("/api/status")
async def station_status():
    """Read-only station health used by the public Tools panel."""
    relay_running = (
        relay_process is not None
        and relay_process.poll() is None
    )

    playlist_fresh = False
    playlist_age_seconds = None

    if RELAY_PLAYLIST.exists():
        try:
            playlist_age_seconds = max(
                0.0,
                datetime.now(timezone.utc).timestamp()
                - RELAY_PLAYLIST.stat().st_mtime,
            )
            playlist_fresh = playlist_age_seconds <= 15
        except OSError:
            pass

    audio_running = (
        audio_process is not None
        and audio_process.poll() is None
    )

    async with audio_clients_lock:
        listener_count = len(audio_clients)

    return {
        "status": "ok",
        "relay_username": relay_username,
        "video": {
            "healthy": bool(relay_running and playlist_fresh),
            "process_running": relay_running,
            "playlist_fresh": playlist_fresh,
            "playlist_age_seconds": (
                round(playlist_age_seconds, 1)
                if playlist_age_seconds is not None
                else None
            ),
        },
        "audio": {
            "healthy": bool(relay_running and audio_running),
            "process_running": audio_running,
            "bitrate_kbps": 128,
            "listeners": listener_count,
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

@app.get("/api/live")
def live():

    conn = get_db()

    live_rows = conn.execute("""
        SELECT *
        FROM live_djs
        ORDER BY viewers DESC
    """).fetchall()

    favourite_rows = conn.execute("""
        SELECT *
        FROM favourite_djs
        WHERE enabled = 1
        ORDER BY name COLLATE NOCASE
    """).fetchall()

    conn.close()

    live_djs = [
        dict(row)
        for row in live_rows
    ]

    favourites = [
        dict(row)
        for row in favourite_rows
    ]

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
# Schedule / Admin
# ---------------------------------------------------------

ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Radio RRR Scheduler</title>
<style>
:root{color-scheme:dark;--bg:#090909;--panel:#141414;--panel2:#1c1c1c;--line:#303030;--text:#f3f3f3;--muted:#a7a7a7;--accent:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:Arial,Helvetica,sans-serif}
.wrap{max-width:1250px;margin:auto;padding:28px 22px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:25px}
h1{font-size:28px;margin:0 0 5px}.sub{color:var(--muted);font-size:14px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}
.tab,button{border:1px solid var(--line);background:var(--panel2);color:var(--text);border-radius:8px;padding:10px 14px;cursor:pointer}
.tab.active{background:#eee;color:#111}.actions{display:flex;gap:8px;flex-wrap:wrap}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}.row{display:grid;grid-template-columns:150px 1fr 1.6fr 130px;gap:15px;align-items:center;padding:15px 18px;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}.head{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em;background:#101010}
.slot-name{font-weight:700}.time{font-variant-numeric:tabular-nums}.genres{display:flex;gap:6px;flex-wrap:wrap}.pill{font-size:12px;padding:5px 8px;border:1px solid #3a3a3a;border-radius:99px;color:#ddd}
.edit{padding:7px 10px;font-size:12px}.empty{padding:35px;text-align:center;color:var(--muted)}
.modal{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;padding:20px}.modal.show{display:flex}.box{width:min(620px,100%);background:#171717;border:1px solid #3a3a3a;border-radius:14px;padding:22px}
label{display:block;font-size:12px;color:var(--muted);margin:13px 0 6px}input[type=text],input[type=time],select{width:100%;background:#0d0d0d;color:#fff;border:1px solid #3a3a3a;border-radius:7px;padding:11px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}.genrebox{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.genre{border:1px solid #3a3a3a;border-radius:7px;padding:8px 10px;background:#101010}.genre input{margin-right:6px}
.modal-actions{display:flex;justify-content:space-between;gap:8px;margin-top:22px}.danger{border-color:#713b3b}.status{color:#8fd18f;font-size:13px;min-height:18px;margin-top:12px}
@media(max-width:800px){.row{grid-template-columns:1fr 1fr}.head{display:none}.genres{grid-column:1/-1}.actions{grid-column:2;justify-content:flex-end}.time{font-size:13px}.grid2{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
<div class="top"><div><h1>Radio RRR Scheduler</h1><div class="sub">Edit the station schedule without touching the code.</div></div><div class="actions"><button onclick="copyDay()">Copy day</button><button onclick="newSlot()">+ Add slot</button></div></div>
<div class="tabs" id="tabs"></div>
<div class="card" id="schedule"></div>
<div class="status" id="status"></div>
</div>
<div class="modal" id="modal"><div class="box">
<h2 id="modalTitle">Edit schedule</h2>
<input id="editId" type="hidden">
<div class="grid2"><div><label>Start</label><input id="start" type="time"></div><div><label>End</label><input id="end" type="time"></div></div>
<label>Program / block name</label><input id="name" type="text" maxlength="80">
<label>Genres</label><div class="genrebox" id="genres"></div>
<div class="modal-actions"><button class="danger" id="deleteBtn" onclick="deleteSlot()">Delete</button><div class="actions"><button onclick="closeModal()">Cancel</button><button onclick="saveSlot()">Save</button></div></div>
</div></div>
<script>
const days=["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];let day=0,data=[],genreList=[];
function esc(s){return String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
async function load(){let r=await fetch("/api/schedule");let j=await r.json();data=j.schedule||[];genreList=j.genres||[];renderTabs();render()}
function renderTabs(){document.getElementById("tabs").innerHTML=days.map((d,i)=>`<button class="tab ${i===day?"active":""}" onclick="day=${i};renderTabs();render()">${d}</button>`).join("")}
function render(){let rows=data.filter(x=>x.day_of_week===day).sort((a,b)=>a.start_minute-b.start_minute);let el=document.getElementById("schedule");if(!rows.length){el.innerHTML='<div class="empty">No schedule blocks for this day.</div>';return}
el.innerHTML='<div class="row head"><div>Time</div><div>Program</div><div>Genres</div><div></div></div>'+rows.map(x=>`<div class="row"><div class="time">${fmt(x.start_minute)} – ${fmt(x.end_minute)}</div><div class="slot-name">${esc(x.name)}</div><div class="genres">${(x.genres||[]).map(g=>`<span class="pill">${esc(g)}</span>`).join("")}</div><div class="actions"><button class="edit" onclick="editSlot(${x.id})">Edit</button></div></div>`).join("")}
function fmt(m){let h=Math.floor(m/60)%24,mi=m%60;return String(h).padStart(2,"0")+":"+String(mi).padStart(2,"0")}
function openModal(x){document.getElementById("modal").classList.add("show");document.getElementById("editId").value=x?.id||"";document.getElementById("start").value=x?fmt(x.start_minute):"00:00";document.getElementById("end").value=x?fmt(x.end_minute):"04:00";document.getElementById("name").value=x?.name||"";document.getElementById("deleteBtn").style.display=x?"block":"none";document.getElementById("modalTitle").textContent=x?"Edit schedule":"Add schedule";let gs=x?.genres||[];document.getElementById("genres").innerHTML=genreList.map(g=>`<label class="genre"><input type="checkbox" value="${esc(g)}" ${gs.includes(g)?"checked":""}>${esc(g)}</label>`).join("")}
function closeModal(){document.getElementById("modal").classList.remove("show")}
function editSlot(id){openModal(data.find(x=>x.id===id))}
function newSlot(){openModal(null)}
function minutes(v){let [h,m]=v.split(":").map(Number);return h*60+m}
async function saveSlot(){let id=Number(document.getElementById("editId").value)||null;let payload={id,day_of_week:day,start_minute:minutes(document.getElementById("start").value),end_minute:minutes(document.getElementById("end").value),name:document.getElementById("name").value.trim(),genres:[...document.querySelectorAll("#genres input:checked")].map(x=>x.value)};if(!payload.name||payload.end_minute<=payload.start_minute){alert("Please enter a name and a valid time range.");return}let r=await fetch("/api/admin/schedule",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});let j=await r.json();if(!r.ok){alert(j.detail||"Could not save.");return}closeModal();await load();flash("Saved")}
async function deleteSlot(){let id=Number(document.getElementById("editId").value);if(!id||!confirm("Delete this schedule block?"))return;let r=await fetch("/api/admin/schedule/"+id,{method:"DELETE"});if(!r.ok){alert("Could not delete.");return}closeModal();await load();flash("Deleted")}
async function copyDay(){let target=prompt("Copy "+days[day]+" to which day? Enter 1-7 (Mon-Sun).");if(target===null)return;let n=Number(target)-1;if(!Number.isInteger(n)||n<0||n>6||n===day){alert("Enter a different day number from 1 to 7.");return}if(!confirm(`Replace ${days[n]} with ${days[day]}?`))return;let r=await fetch("/api/admin/schedule/copy",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({from_day:day,to_day:n})});let j=await r.json();if(!r.ok){alert(j.detail||"Could not copy.");return}day=n;await load();flash("Day copied")}
function flash(s){document.getElementById("status").textContent=s;setTimeout(()=>document.getElementById("status").textContent="",2000)}
load();
</script>
</body></html>"""

@app.get("/admin", response_class=HTMLResponse)
def admin_scheduler():
    return HTMLResponse(ADMIN_HTML)

@app.get("/admin/schedule", response_class=HTMLResponse)
def admin_schedule():
    return HTMLResponse(ADMIN_HTML)

@app.get("/api/schedule")
def get_schedule():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, day_of_week, start_minute, end_minute, name, genres_json, enabled
        FROM schedule
        WHERE enabled = 1
        ORDER BY day_of_week, start_minute
    """).fetchall()
    conn.close()

    genre_set = set()
    result = []
    for row in rows:
        try:
            genres = json.loads(row["genres_json"] or "[]")
        except Exception:
            genres = []
        genres = [str(g).strip() for g in genres if str(g).strip()]
        genre_set.update(genres)
        result.append({
            "id": row["id"],
            "day_of_week": row["day_of_week"],
            "start_minute": row["start_minute"],
            "end_minute": row["end_minute"],
            "name": row["name"],
            "genres": genres,
            "enabled": bool(row["enabled"]),
        })

    return {"schedule": result, "genres": sorted(genre_set, key=str.casefold)}

@app.post("/api/admin/schedule")
async def save_schedule_slot(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    try:
        day = int(payload.get("day_of_week"))
        start = int(payload.get("start_minute"))
        end = int(payload.get("end_minute"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid schedule time")

    name = str(payload.get("name") or "").strip()
    genres = payload.get("genres") or []

    if day < 0 or day > 6 or start < 0 or end > 1440 or end <= start:
        raise HTTPException(status_code=400, detail="Invalid day or time range")
    if not name:
        raise HTTPException(status_code=400, detail="Program name is required")
    if not isinstance(genres, list):
        raise HTTPException(status_code=400, detail="Genres must be a list")

    genres = [str(g).strip() for g in genres if str(g).strip()]
    slot_id = payload.get("id")
    conn = get_db()

    try:
        if slot_id:
            conn.execute("""
                UPDATE schedule
                SET day_of_week=?, start_minute=?, end_minute=?, name=?, genres_json=?, enabled=1
                WHERE id=?
            """, (day, start, end, name, json.dumps(genres), int(slot_id)))
        else:
            conn.execute("""
                INSERT INTO schedule
                (day_of_week, start_minute, end_minute, name, genres_json, enabled)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (day, start, end, name, json.dumps(genres)))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=409, detail="Another schedule block already uses that time range.")
    finally:
        conn.close()

    return {"status": "ok"}

@app.delete("/api/admin/schedule/{slot_id}")
def delete_schedule_slot(slot_id: int):
    conn = get_db()
    cur = conn.execute("DELETE FROM schedule WHERE id=?", (slot_id,))
    conn.commit()
    conn.close()
    return {"status": "ok", "deleted": cur.rowcount > 0}

@app.post("/api/admin/schedule/copy")
async def copy_schedule_day(request: Request):
    try:
        payload = await request.json()
        source = int(payload.get("from_day"))
        target = int(payload.get("to_day"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid day")

    if source not in range(7) or target not in range(7) or source == target:
        raise HTTPException(status_code=400, detail="Invalid source or target day")

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT start_minute, end_minute, name, genres_json, enabled
            FROM schedule WHERE day_of_week=? ORDER BY start_minute
        """, (source,)).fetchall()
        conn.execute("DELETE FROM schedule WHERE day_of_week=?", (target,))
        for row in rows:
            conn.execute("""
                INSERT INTO schedule
                (day_of_week, start_minute, end_minute, name, genres_json, enabled)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (target, row["start_minute"], row["end_minute"], row["name"], row["genres_json"], row["enabled"]))
        conn.commit()
    finally:
        conn.close()

    return {"status": "ok", "copied": len(rows)}

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
