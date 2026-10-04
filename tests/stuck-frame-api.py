"""Real FFmpeg processing plus upload limits, error reporting and cleanup."""
import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path
from uuid import uuid4

class App:
    def post(self, path): return lambda f: f
class Request:
    def __init__(self, chunks, headers): self.chunks, self.headers = chunks, headers
    async def stream(self):
        for chunk in self.chunks: yield chunk
class HTTPException(Exception):
    def __init__(self, status_code, detail): self.status_code, self.detail = status_code, detail
class BackgroundTask:
    def __init__(self, f, *args, **kwargs): self.call = lambda: f(*args, **kwargs)
class FileResponse:
    def __init__(self, path, **kwargs): self.path, self.options = path, kwargs

ns = dict(asyncio=asyncio, json=json, math=math, re=re, shutil=shutil,
          subprocess=subprocess, sys=sys, wave=wave, array=array, Path=Path,
          uuid4=uuid4, app=App(), Request=Request, HTTPException=HTTPException,
          FileResponse=FileResponse, BackgroundTask=BackgroundTask)
exec(Path('tests/stuck-frame-bpm-fixture.py').read_text(), ns)
exec(Path('backend/stuck-frame-api-functions.py').read_text(), ns)

def command(*args):
    return subprocess.run(args, check=True, capture_output=True)

async def run():
    assert shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Put FFmpeg and FFprobe on PATH'
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        ns['STUCK_FRAME_TOOL_DIR'] = folder / 'jobs'
        video, music, with_audio = folder/'silent.mp4', folder/'music.wav', folder/'sound.mp4'
        command('ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=25',
                '-t','12','-c:v','libx264','-pix_fmt','yuv420p',str(video))
        samples = array('h', (int(18000 * math.sin(2*math.pi*180*i/8000))
                             if i % 4000 < 640 else 0 for i in range(8000*10)))
        with wave.open(str(music),'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000); wav.writeframes(samples.tobytes())
        command('ffmpeg','-v','error','-i',str(video),'-i',str(music),'-c:v','copy','-c:a','aac',str(with_audio))
        endpoint = ns['public_stuck_frame_effect']
        raw = video.read_bytes()
        async def failure(chunks, headers, status, code=None):
            try: await endpoint(Request(chunks, headers))
            except HTTPException as error:
                assert error.status_code == status, error.detail
                if code: assert error.detail['code'] == code
            else: raise AssertionError('Expected failure')
            assert not list(ns['STUCK_FRAME_TOOL_DIR'].glob('*'))
        headers = {'X-RRR-Filename':'silent.mp4'}
        await failure([raw],headers,422,'no_audio')
        await failure([raw],dict(headers, **{'X-RRR-Video-Bytes':'bad'}),400)
        await failure([raw],dict(headers, **{'X-RRR-Video-Bytes':str(len(raw))}),400)
        await failure([raw],dict(headers, **{'X-RRR-Music-Preset':'unknown'}),400)
        await failure([raw],dict(headers, **{'X-RRR-Music-Preset':'chill'}),400)
        ns['STUCK_FRAME_MAX_UPLOAD_BYTES'] = 2048
        await failure([raw],headers,413)
        ns['STUCK_FRAME_MAX_UPLOAD_BYTES'] = 100*1024*1024
        combined = raw + music.read_bytes()
        # Cross the video/audio boundary within a chunk, and also exactly at it.
        for chunks in ([combined[:len(raw)-7], combined[len(raw)-7:len(raw)+9], combined[len(raw)+9:]],
                       [raw, music.read_bytes()]):
            result = await endpoint(Request(chunks,dict(headers, **{'X-RRR-Video-Bytes':str(len(raw))})))
            probe = json.loads(command('ffprobe','-v','error','-show_streams','-show_format','-of','json',str(result.path)).stdout)
            assert {s['codec_type'] for s in probe['streams']} == {'audio','video'}
            assert abs(float(probe['format']['duration']) - 12) < .15
            assert int(result.options['headers']['X-RRR-Stuck-Frames']) > 0
            result.options['background'].call()
            assert not list(ns['STUCK_FRAME_TOOL_DIR'].glob('*'))
        result = await endpoint(Request([with_audio.read_bytes()], {'X-RRR-Filename':'sound.mp4'}))
        assert result.path.stat().st_size > 50000
        result.options['background'].call()
        assert not list(ns['STUCK_FRAME_TOOL_DIR'].glob('*'))
        # Invalid added audio must produce the same actionable result.
        await failure([raw,raw], dict(headers, **{'X-RRR-Video-Bytes':str(len(raw))}),422,'no_audio')
        result = await endpoint(Request([raw,music.read_bytes()], dict(headers, **{
            'X-RRR-Video-Bytes':str(len(raw)), 'X-RRR-Music-Preset':'drum-and-bass'})))
        assert result.options['headers']['X-RRR-Detected-BPM'] == '174'
        result.options['background'].call()
        assert not list(ns['STUCK_FRAME_TOOL_DIR'].glob('*'))
    print('PASS: missing audio, added audio, original audio, real rendering, video duration, chunk boundaries, limits and cleanup')

asyncio.run(run())
