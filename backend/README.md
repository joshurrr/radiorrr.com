# Stream speech detector backend

The website now submits a stream URL to `POST /api/tools/speech`.

`speech-api.patch` adds that endpoint to RadioRouter `app/main.py`. It resolves public direct streams and supported TikTok, Twitch and YouTube live pages, validates the submitted and resolved media URLs with the router's existing validator, captures up to 30 seconds of mono PCM audio, and returns estimated voice activity in `speech_ratio` plus the actual `sample_seconds`.

`speech-docker.patch` adds `webrtcvad-wheels==2.0.14` to the router image. The package uses the same WebRTC VAD implementation already used by the genre detector.

The user approved applying the patches and rebuilding/restarting RadioRouter. The patches have been applied to the shared router `app/main.py` and `app/Dockerfile`. Verified backups are `main.py.before-stream-speech-20261004-023222` and `Dockerfile.before-stream-speech-20261004-023222` in that same directory.

The running service has not been rebuilt or restarted: the NAS refuses SSH on port 22 and Docker management on ports 2375/2376, its web interface requires login, and browser automation cannot start in this environment. The live API does not yet advertise `/api/tools/speech`. Deployment requires a working NAS management connection, an image rebuild and recreation of the existing RadioRouter container while preserving its configuration and `/data` mount. This may briefly interrupt the station.

The request/result and resolver tests use mocks; real audio capture and platform access must be checked after deployment. Unknown HTML pages, offline streams and restricted streams return an error rather than a speech percentage.

Validation:

- `node tests/stream-speech.cjs`
- `python tests/stream-speech-api.py`

`prepare-speech-api.py` reads the current shared router and detector sources to regenerate the local patches. It does not write to shared sources.
