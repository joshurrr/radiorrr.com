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

    live_check = await scan_added_dj_immediately(username, platform)

    return {
        "status": "ok",
        "live_check": live_check,
        "username": username,
    }
