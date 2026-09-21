# Radio RRR engine indicators

This package adds three indicators under Tools > System Status:
RadioRouter engine, Genre detection engine, and Genre detection scout.

Running means the RadioRouter status endpoint responded or the worker sent a
heartbeat in the last 90 seconds. Workers report every 20 seconds after their
models load. The page refreshes these indicators independently every 30 seconds
and when Refresh is clicked. Allow up to about two minutes to detect a stopped
worker. Not reporting means no recent heartbeat; it does not prove Docker stopped.
Unavailable means the status request failed or reporting has not been deployed.
A live worker can still have analysis errors or be idle. This is process liveness,
not proof of successful genre analysis, stream health, or every router background task.

## Install

1. Back up the three deployed Python files and the website index.html/script.js.
2. Replace the deployed RadioRouter main.py with this package's main.py, using
   the same persistent host mapping or image build process as your existing setup.
   The correct host path for main.py was not provided; do not assume the /data
   mapping contains the application code. Avoid changes only in the ephemeral
   container filesystem, which can disappear when Unraid recreates the container.
3. Replace these host files with the matching package files:
   - /mnt/user/rrr-genredetect/detector.py
   - /mnt/user/rrr-genredetect/detector-candidate-scout.py
4. In Unraid, add an environment variable named ENGINE_HEARTBEAT_TOKEN to all
   three containers, with the SAME long random secret value. Generate it locally,
   for example with Python: python -c "import secrets; print(secrets.token_hex(32))"
   Keep it in container configuration only, never website files. Preserve the
   existing RADIO_ROUTER_URL value; it must reach RadioRouter from both workers.
5. Apply the configuration and restart RadioRouter, then the two workers. Wait
   for each worker's models to load. Reports are stored in /data/engine-heartbeats.db.
6. Deploy index.html and script.js together to the existing website root, retaining
   all other assets, stylesheets and scripts. These are complete replacement files
   based on the current workspace versions; the package is not a standalone site.
7. Open Tools and check all three show Running. To verify expiry during a suitable
   maintenance window, stop one worker, wait about two minutes and refresh. It
   should show Not reporting. Restart it and verify Running returns after loading.

No Docker socket access, new dependencies, stream changes or automatic restarts
are added. RadioRouter's existing /health and /api/status behavior is unchanged.
The heartbeat receiver requires the shared secret; the public status endpoint
returns only the three running flags. It exposes no secrets or Docker metadata.

## Verification performed locally

Python AST syntax validation for all three files; Node syntax check for script.js.
Isolated backend checks passed for initial state, valid heartbeat, stale heartbeat,
worker independence, rejected credentials, unknown worker, missing configuration,
and no-cache response headers. Reviewed focused source diffs. No NAS deployment,
live container checks, or browser visual verification performed here.

## Rollback

Restore the backed-up Python and website files and restart the affected containers.
The added environment variable and heartbeat database can remain unused.
