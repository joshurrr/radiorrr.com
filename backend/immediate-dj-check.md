# Immediate DJ live check

Adding or re-enabling a DJ through `/api/admin/djs/add`, `/api/favourites`, or `/api/favourites/import-tiktok` now checks live status immediately after the save commits. TikTok, Twitch and YouTube use the existing platform liveness APIs. A confirmed live DJ is upserted into `live_djs` immediately; offline DJs remain saved for scheduled monitoring. The response includes `live_check` and the admin success message reports the result.

A failed or timed-out live check does not fail the successful save. The check is bounded to 45 seconds. Existing session start times are preserved, one offline observation does not remove an existing live DJ, and a DJ disabled/deleted during the network check is not resurrected. Station playback still follows the normal relay selection rules.

The patch is applied to `/mnt/user/radiorouter/app/main.py`. The source backup is `main.py.before-immediate-dj-scan-20261004-041922`. No new package is required.

The running container still needs the file loaded and a restart. In the NAS terminal:

```bash
set -e
cd /mnt/user/radiorouter/app
docker build -t radiorouter:latest .
docker cp main.py radiorouter:/app/main.py
docker restart radiorouter
```

Tests: `python tests/immediate-dj-check.py`. These use temporary SQLite storage and mocked platform responses, covering all three add paths, live/offline/error results, session preservation, disabled-during-check protection, and platform metadata. No test DJs were inserted into the production database.
