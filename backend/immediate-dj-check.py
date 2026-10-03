"""Immediate live check after saving or re-enabling a DJ."""

async def _check_added_dj_live(username, platform):
    platform_key = str(platform or 'TikTok').strip().casefold()
    conn = get_db()
    try:
        row = conn.execute('''
            SELECT * FROM favourite_djs
            WHERE lower(username) = lower(?) AND lower(platform) = lower(?)
              AND enabled = 1
        ''', (username, platform)).fetchone()
    finally:
        conn.close()
    if row is None:
        return {'status': 'skipped', 'live': None}
    favourite = dict(row)
    username = favourite['username']
    platform = favourite['platform']
    now = datetime.now(timezone.utc).isoformat()
    name = favourite.get('name') or username
    live_url = favourite.get('live_url') or favourite.get('profile_url')
    viewers = 0
    started_at = None

    if platform_key == 'tiktok':
        is_live = bool(await asyncio.to_thread(_run_tiktok_is_live_check, username))
        _record_tiktok_live_check(username, is_live)
        invalid_tiktok_account_counts.pop(username, None)
        live_url = live_url or f'https://www.tiktok.com/@{username}/live'
        if is_live:
            offline_miss_counts.pop(username, None)
    elif platform_key == 'twitch':
        streams = await fetch_twitch_live_streams([username])
        info = streams.get(username.lower())
        is_live = info is not None
        if info:
            name = info.get('user_name') or name
            viewers = info.get('viewer_count') or 0
            started_at = info.get('started_at')
        live_url = live_url or f'https://www.twitch.tv/{username}'
    elif platform_key == 'youtube':
        live_url = favourite.get('live_url') or str(favourite.get('profile_url') or '').rstrip('/') + '/live'
        info = await get_youtube_live_info(live_url)
        is_live = info is not None
        if info:
            name = info.get('channel') or info.get('uploader') or name
            live_url = info.get('webpage_url') or info.get('original_url') or live_url
            viewers = info.get('concurrent_view_count') or 0
            timestamp = info.get('release_timestamp') or info.get('timestamp')
            try:
                started_at = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                pass
    else:
        return {'status': 'unsupported', 'live': None}

    try:
        viewers = max(0, int(viewers))
    except (TypeError, ValueError):
        viewers = 0
    conn = get_db()
    try:
        # Do not resurrect a DJ disabled/deleted while the network check ran.
        enabled = conn.execute('''
            SELECT 1 FROM favourite_djs
            WHERE lower(username) = lower(?) AND lower(platform) = lower(?)
              AND enabled = 1
        ''', (username, platform)).fetchone()
        if enabled is None:
            return {'status': 'skipped', 'live': None}
        existing = conn.execute('''
            SELECT started_at FROM live_djs
            WHERE lower(username) = lower(?) AND lower(platform) = lower(?)
        ''', (username, platform)).fetchone()
        if is_live:
            started_at = (existing['started_at'] if existing else None) or started_at or now
            conn.execute('''
                INSERT INTO live_djs
                (username, name, platform, url, genre, viewers, started_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, username) DO UPDATE SET
                    name=excluded.name, url=excluded.url, genre=excluded.genre,
                    viewers=excluded.viewers, started_at=excluded.started_at,
                    updated_at=excluded.updated_at
            ''', (username, name, platform, live_url, favourite.get('genre'), viewers, started_at, now))
            conn.commit()
            print(f'[New DJ] LIVE: {platform} @{username} added to the live list')
            return {'status': 'live', 'live': True}
        # A single offline observation must not remove an already-live DJ.
        if existing:
            return {'status': 'retained_live', 'live': True}
        print(f'[New DJ] OFFLINE: {platform} @{username}; saved for future monitoring')
        return {'status': 'offline', 'live': False}
    finally:
        conn.close()


async def scan_added_dj_immediately(username, platform):
    try:
        return await asyncio.wait_for(_check_added_dj_live(username, platform), timeout=45)
    except asyncio.TimeoutError:
        print(f'[New DJ] CHECK TIMED OUT: {platform} @{username}; scheduled monitoring will retry')
        return {'status': 'error', 'live': None, 'message': 'Live check timed out. Scheduled monitoring will retry.'}
    except Exception as error:
        print(f'[New DJ] CHECK ERROR: {platform} @{username}: {error}')
        return {'status': 'error', 'live': None, 'message': 'Could not check live status. Scheduled monitoring will retry.'}


def added_dj_live_message(check):
    if check['status'] == 'live':
        return 'Checked immediately: LIVE and added to the live DJ list.'
    if check['status'] == 'offline':
        return 'Checked immediately: currently offline. Scheduled monitoring will continue.'
    if check['status'] == 'retained_live':
        return 'The existing live DJ listing was retained after an inconclusive offline check.'
    return check.get('message') or 'Saved. Scheduled monitoring will continue.'
