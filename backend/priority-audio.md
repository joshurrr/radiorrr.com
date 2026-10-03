# Priority audio scans

The router maintains a persistent, platform-specific priority queue. Adding/re-enabling a confirmed live DJ automatically enqueues their first audio scan. Browser-selected playback requests `/api/ai-genre/scan-priority` through the updated website script.

The scout checks the queue at the start of every round and before every normal sample. It finishes the active sample, then services up to three priority jobs before allowing the normal round to proceed. Genres, BPM and speech are published through the existing analysis endpoint. Duplicate requests are combined, completed/error requests have a 60-second cooldown, crashed-worker leases expire after three minutes, and offline/disabled DJs are not claimed.

Shared router source and both scout source versions have been updated, with `.before-priority-audio-20261004-044039` backups. Python syntax checks and mocked queue, worker and browser tests pass. The alternate scout file was recovered from its backup after an interrupted network-share write; its patched contents were verified after atomic replacement.

Activate in the NAS terminal:

```bash
bash /mnt/user/radiorouter/tools/activate-priority-audio.sh
```

The script detects the running candidate scout from its configured command, rebuilds the router and scout images, copies patched source into the existing containers to preserve their settings, and restarts them. It aborts before modifying containers if there is not exactly one candidate scout.

The updated website script must also be published for browser clicks to submit priority jobs. Manually request Kamilla's scan after the backend is activated:

```bash
curl --fail-with-body -X POST http://127.0.0.1:8000/api/ai-genre/scan-priority -H 'Content-Type: application/json' -d '{"username":"kamilla_beriya","platform":"TikTok"}'
```

Tests: `python tests/priority-audio.py`, `python tests/immediate-dj-check.py`, and `node tests/priority-audio-browser.cjs`. Audio/platform responses are mocked in these tests; real priority analysis must be verified after container activation. The station relay selection rules are unchanged.
