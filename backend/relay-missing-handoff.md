# RadioRouter relay handoff after missing DJ status

On 15 November 2026 at 05:07:25 AEST, the shared relay stopped after the five-minute missing-DJ grace period. Thirteen seconds earlier, it still had fresh HLS output. The deployed code used grace expiration to stop FFmpeg and clear its output before selecting a replacement. This patch keeps that feed on air while a replacement is selected and validated. Failed candidates retain their existing cooldown. A stalled or exited feed still takes the ordinary recovery path.

## Manual deployment and rollback

Schedule the RadioRouter restart with the station operator. Prepare the new file in a temporary directory on the NAS. The patch expects the exact 8 November baseline whose SHA-256 is `92a095751b4fcdfd7c363217f73c57b54388f57ef94d957c4b118a28fb787157`.

1. Copy [relay-missing-handoff.patch](relay-missing-handoff.patch) into a temporary directory on the NAS. Copy `/mnt/user/radiorouter/app/main.py` there as `main.py`. Check its hash with `sha256sum main.py`. Run `patch --dry-run -p1 < relay-missing-handoff.patch` and then `patch -p1 < relay-missing-handoff.patch`. Review the diff against the NAS source.
2. Save dated copies of both `/mnt/user/radiorouter/app/main.py` and the running container's file. Use `docker cp radiorouter:/app/main.py ./main.py.before-container` for the latter and compare the two backups before proceeding.
3. Place the reviewed file at `/mnt/user/radiorouter/app/main.py` and use `docker cp main.py radiorouter:/app/main.py` from the temporary directory. During the agreed window, run `docker restart radiorouter`. Check `docker logs --timestamps --tail 100 radiorouter`. Rebuild from the updated NAS application source before any future container recreation so the image includes the same fix.
4. To roll back, restore the corresponding backup to the NAS source path and to `radiorouter:/app/main.py`, then restart in the agreed window. Rebuild from the restored source before any later recreation.

## Playback verification

- Run `python scripts/watch-station-hls.py http://192.168.7.10:8000 --samples 180` from a machine with NAS access and FFmpeg on PATH. Redirect stdout to a local log for a roughly 30-minute capture. Use `--ffmpeg PATH` if needed. Compare NAS and public HTTP status, playlist tail, media sequence, segment HTTP status, SHA-256 for the same segment path, and decoded nonzero audio samples. The tail should advance even while the live window first fills and its starting sequence stays fixed. Keep matching Docker logs for the same UTC window.
- Open the public Live Video tab and unmute. Confirm visible motion, advancing playback time, and audible programme audio. In DevTools Network, preserve the log and verify new public playlist and segment requests, along with playback progress. Check the separate Live Audio tab and listen to the MP3 feed.
- During a naturally occurring missing-DJ grace expiration or relay handoff, leave the public player open and unmuted. Confirm segment downloads and audio continue while the replacement validates, then confirm the new DJ plays without reloading the page. If an upstream actually stops producing media, record the last successful segment, replacement promotion, first advancing public sequence and first audible playback after recovery.
- Keep the active browser network capture beside timestamped Docker logs and the paired HLS capture. When diagnosing an extended gap, compare the stop, candidate resolution, FFmpeg start, promotion, and first browser playback timestamps.
