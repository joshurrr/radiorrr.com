from pathlib import Path
import ast
import difflib

router = Path(r"\\192.168.7.10\radiorouter\app")
detector = Path(r"\\192.168.7.10\rrr-genredetect\detector.py")
main = router / "main.py"
original = main.read_text(encoding="utf-8")
source = detector.read_text(encoding="utf-8")
node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "analyse_speech")
speech = ast.get_source_segment(source, node).replace("def analyse_speech(audio_file):", "def _analyse_public_speech(audio_file):")
speech = speech.replace("    convert = subprocess.run(", "    try:\n        import webrtcvad\n    except ImportError as error:\n        raise RuntimeError(\"Speech detector dependency is unavailable; rebuild the router image\") from error\n    convert = subprocess.run(", 1)
helpers = Path("backend/speech-api-functions.py").read_text(encoding="utf-8")
anchor = '@app.post("/api/tools/bpm")'
assert anchor in original and '/api/tools/speech' not in original
updated = original.replace(anchor, speech + "\n\n" + helpers + "\n\n" + anchor, 1)
ast.parse(updated)
Path("backend/speech-api.patch").write_text("".join(difflib.unified_diff(original.splitlines(True), updated.splitlines(True), fromfile="a/app/main.py", tofile="b/app/main.py")), encoding="utf-8")

docker = router / "Dockerfile"
old = docker.read_text(encoding="utf-8")
assert "streamlink==8.6.1" in old
new = old.replace("streamlink==8.6.1 yt-dlp==2026.8.19", "streamlink==8.6.1 yt-dlp==2026.8.19 webrtcvad-wheels==2.0.14", 1)
Path("backend/speech-docker.patch").write_text("".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), fromfile="a/app/Dockerfile", tofile="b/app/Dockerfile")), encoding="utf-8")

print("Saved reviewable router and Dockerfile patches. Shared source and running services were not changed.")
