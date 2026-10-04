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
