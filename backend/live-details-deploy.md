# Deploy faster live DJ details

The website draws the current DJ and lineup from the live response, then adds programme scores and live genre readings as they arrive. The NAS adds an optional `include_learned=false` mode to `/api/live`. It keeps stored profiles and current detections while skipping the catalogue-wide historical genre calculation. Calls without that option retain the full response.

## NAS

Stage the updated source in Windows PowerShell.

```powershell
Copy-Item -LiteralPath 'C:\GitHub\radiorrr.com\backend\main.py' -Destination '\\nas\radiorouter\main.live-details.py' -ErrorAction Stop
```

Paste this in Unraid when ready for one RadioRouter restart.

```sh
(
  set -eu
  cd /mnt/user/radiorouter
  sha256sum -c <<'CHECKSUMS'
dfa0ff9463ffe3283ca7e94f60813c4d14510421282b6618895a83d64561ca9b  app/main.py
6349a8652e729e6ee3a0a9e086bc8484c56621ea6daf57872568d677ee2c0bfd  main.live-details.py
CHECKSUMS
  backup="live-details-backup-$(date +%Y%m%d-%H%M%S)"
  mkdir "$backup"
  cp -p app/main.py "$backup/main.nas.py"
  docker cp radiorouter:/app/main.py "$backup/main.container.py"
  cmp "$backup/main.nas.py" "$backup/main.container.py"
  printf 'Rollback backup: %s/%s\n' "$PWD" "$backup"
  cp main.live-details.py app/main.py
  docker cp app/main.py radiorouter:/app/main.py
  docker restart radiorouter
)
```

For NAS rollback, restore `main.nas.py` to `/mnt/user/radiorouter/app/main.py` and `main.container.py` to `radiorouter:/app/main.py` from the printed backup directory, then restart RadioRouter.

## Website

From Windows PowerShell, publish only the website and browser test changes.

```powershell
cd C:\GitHub\radiorrr.com
git add -A -- index.html frontend/index.html tools script.20261115-playback-audio.js script.20261115-live-details.js tests/station-startup.cjs
git diff --cached --stat
git commit -m "fix: show live DJ details before optional analytics"
git push origin main
```

Once GitHub Pages has updated, open the home page in a new tab. In DevTools Network, compare the completion time of `/api/live?include_learned=false` with when the DJ name and lineup appear. Check that programme scores fill in when `/api/genre-match` completes. Confirm that Live Video still plays with sound after unmuting. For frontend rollback, revert the website commit and push the revert to `main`.
