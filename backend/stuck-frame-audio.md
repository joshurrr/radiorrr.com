# Stuck-frame videos without audio

The tool now probes for an audio stream before extracting a WAV. Missing audio
returns HTTP 422 with `detail.code = "no_audio"` and a readable message. The
browser reveals a Choose audio input and allows the user to retry with music.
The added track drives beat detection and becomes the output soundtrack.
Short tracks are padded with silence; long tracks are trimmed to video length.

Existing raw-video requests still work. With added audio, the request body is
the video followed by the audio, and `X-RRR-Video-Bytes` identifies the byte
boundary. The server streams both files to a temporary job directory, limits
each to 100 MB, and cleans up failed jobs and completed downloads. This requires
no additional Python packages. Audio selection is reset when the video changes.

The renderer also fixes its FFmpeg graph: `freezeframes` requires two inputs,
so each stage splits its source into the main and reference streams, then maps
the processed video and selected audio explicitly.

Validation:

- `node tests/stuck-frame.cjs`
- `node --check script.js`
- `python tests/stuck-frame-api.py` with FFmpeg and FFprobe on PATH

The processing test generates real media and checks missing audio, original
audio, adding audio, output duration, upload boundaries, limits and job cleanup.

`apply-stuck-frame-audio.py` backs up the shared main.py and replaces only the
reviewed tool functions. It refuses to write if those functions have changed.
The website changes must be published, and RadioRouter must load the updated
main.py before the added-audio retry can work on the live site.

## Built-in music

The picker offers four original, synthesised tracks at `/audio/stuck-frame/`:
drum and bass (174 BPM), electro (128 BPM), techno (132 BPM) and chill (90 BPM).
Each has a 16-second preview and a ten-minute full track. Tracks are documented
and dedicated under CC0 in `audio/stuck-frame/README.md`. No third-party samples
or songs are used. Publish the complete audio directory with the frontend.

The browser downloads the selected full track only when Create Effect is clicked
and submits it using the existing added-audio upload. A bounded
`X-RRR-Music-Preset` identifier selects its exact known tempo in the backend.
These tracks start at beat zero, so preset beat positions are calculated
directly. Uploaded audio continues to use automatic tempo and phase detection.

For a backend that already has the audio-upload fix, apply this update with
`python backend/apply-stuck-frame-audio.py --music`, then run on the NAS:

```sh
docker cp /mnt/user/radiorouter/app/main.py radiorouter:/app/main.py && docker restart radiorouter
```

Additional checks: `node tests/stuck-frame-browser.cjs` with Playwright available,
and `python tests/stuck-frame-music.py` with FFmpeg/FFprobe on PATH. They exercise
preview playback, desktop/mobile layout, real processing with each built-in
track, and exact preset beat positions.
