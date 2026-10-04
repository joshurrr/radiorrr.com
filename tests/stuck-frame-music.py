"""Check the shipped music assets through the real effect processor."""
import asyncio
import json
import math
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
    def post(self, route): return lambda f: f

ns = dict(asyncio=asyncio,json=json,math=math,re=re,shutil=shutil,
          subprocess=subprocess,sys=sys,wave=wave,array=array,Path=Path,
          uuid4=uuid4,app=App(),Request=object)
exec(Path('tests/stuck-frame-bpm-fixture.py').read_text(),ns)
exec(Path('backend/stuck-frame-api-functions.py').read_text(),ns)

def run(*args): return subprocess.run(args,check=True,capture_output=True)

with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    video = folder/'silent.mp4'
    run('ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=320x240:rate=25',
        '-t','12','-c:v','libx264',str(video))
    for key,bpm in [('drum-and-bass',174),('electro',128),('techno',132),('chill',90)]:
        path = Path('audio/stuck-frame')/(key+'.mp3')
        probe = json.loads(run('ffprobe','-v','error','-show_format','-of','json',str(path)).stdout)
        assert 600 <= float(probe['format']['duration']) < 601
        assert path.stat().st_size < 10*1024*1024
        preview = path.with_name(key+'-preview.mp3')
        probe = json.loads(run('ffprobe','-v','error','-show_format','-of','json',str(preview)).stdout)
        assert 16 <= float(probe['format']['duration']) < 17
        output = folder/(key+'.mp4')
        detected,confidence,effects = ns['_stuck_frame_process_video'](video,folder/'audio.wav',output,path,bpm)
        assert effects >= 4
        assert detected == bpm
        _, _, beats = ns['_stuck_frame_detect_beat_grid'](folder/'audio.wav',12,bpm)
        assert abs(beats[0] - 120/bpm) < .0001
        assert all(abs(t/(120/bpm) - round(t/(120/bpm))) < .0001 for t in beats)
        probe = json.loads(run('ffprobe','-v','error','-show_format','-show_streams','-of','json',str(output)).stdout)
        assert {s['codec_type'] for s in probe['streams']} == {'video','audio'}
        assert abs(float(probe['format']['duration'])-12) < .15
        # Real decoded output has finite, audible signal rather than silence.
        pcm = run('ffmpeg','-v','error','-i',str(output),'-t','5','-f','s16le','-ac','1','-ar','8000','-').stdout
        values = array('h');values.frombytes(pcm)
        assert math.sqrt(sum(v*v for v in values)/len(values)) > 300
        print(f'PASS: {key}, detected {detected} BPM, {effects} freezes, preview and 10-minute track',flush=True)
