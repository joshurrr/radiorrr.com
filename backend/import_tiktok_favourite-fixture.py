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

        live_check = await scan_added_dj_immediately(profile.get("username") or str(username).lstrip("@"), "TikTok")

        return {
            "status": "ok",
            "live_check": live_check,
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
