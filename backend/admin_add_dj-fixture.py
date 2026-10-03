async def admin_add_dj(request: Request):
    """Save/re-enable a DJ, then check live status immediately."""
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

    live_check = await scan_added_dj_immediately(actual_username, platform)
    message += " " + added_dj_live_message(live_check)

    return {
        "ok": True,
        "live_check": live_check,
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
