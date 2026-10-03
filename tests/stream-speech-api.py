import asyncio
import json
import math
import re
import subprocess
import sys
import tempfile
import types
import wave
from array import array
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

class HTTPException(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
class App:
    def post(self, route):
        return lambda f: f
class Request:
    def __init__(self, data): self.data = data
    async def json(self): return self.data

calls = []
def validate(url):
    calls.append(url)
    if not url or not str(url).startswith('https://') or 'private' in url:
        raise ValueError('Invalid or private URL')
    return url
async def platform(username):
    return {'url': 'https://media.example/live.m3u8', 'headers': {}}

globals_for_api = dict(asyncio=asyncio, Path=Path, urlparse=urlparse, re=re, json=json,
                      subprocess=subprocess, wave=wave, uuid4=uuid4, app=App(),
                      Request=Request, HTTPException=HTTPException,
                      _validate_public_stream_url=validate,
                      get_tiktok_stream=platform, get_twitch_stream=platform)
exec(Path('backend/speech-api-functions.py').read_text(), globals_for_api)

async def run():
    resolver = globals_for_api['_resolve_public_speech_stream']
    assert (await resolver('https://www.twitch.tv/somedj'))['url'].startswith('https://media.')
    assert (await resolver('https://www.tiktok.com/@somedj/live'))['url'].startswith('https://media.')
    assert (await resolver('https://radio.example/live.m3u8'))['url'] == 'https://radio.example/live.m3u8'
    for url in ['https://www.twitch.tv/videos/123', 'https://www.tiktok.com/video/123']:
        try: await resolver(url)
        except ValueError: pass
        else: raise AssertionError(url)
    def youtube(args):
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({'is_live':True,'url':'https://media.example/audio.m3u8'}))
    globals_for_api['_run_ytdlp'] = youtube
    assert (await resolver('https://www.youtube.com/@somedj'))['url'].endswith('audio.m3u8')
    globals_for_api['_run_ytdlp'] = lambda args: types.SimpleNamespace(returncode=0, stdout=json.dumps({'is_live':False}))
    try: await resolver('https://youtu.be/123abc')
    except RuntimeError: pass
    else: raise AssertionError('Offline YouTube accepted')
    endpoint=globals_for_api['public_speech_detector']
    with tempfile.TemporaryDirectory() as folder:
        globals_for_api['SPEECH_TOOL_DIR'] = Path(folder)
        def capture(stream,path):
            path.write_bytes(b'temporary sample')
            return {'speech_ratio':0,'talk_ratio':0},30
        globals_for_api['_capture_public_speech_sample']=capture
        result=await endpoint(Request({'url':'https://radio.example/live.mp3'}))
        assert result['speech_ratio']==0 and result['sample_seconds']==30
        assert len(calls)>=2
        assert not list(Path(folder).iterdir())
        for data in [[], {'url':'file:///tmp/foo'}, {'url':'https://private.example/live'}]:
            try: await endpoint(Request(data))
            except HTTPException as error: assert error.status_code==400
            else: raise AssertionError('Bad request accepted')
        def timeout(stream,path):
            path.write_bytes(b'temporary sample')
            raise subprocess.TimeoutExpired('ffmpeg',60)
        globals_for_api['_capture_public_speech_sample']=timeout
        try: await endpoint(Request({'url':'https://radio.example/live.mp3'}))
        except HTTPException as error: assert error.status_code==504
        else: raise AssertionError('Timeout ignored')
        assert not list(Path(folder).iterdir())
    # Exercise the copied analysis logic on known PCM/VAD frames, without FFmpeg or the C extension.
    patch=Path('backend/speech-api.patch').read_text()
    added='\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
    namespace=dict(subprocess=types.SimpleNamespace(PIPE=-1),array=array,sys=sys,math=math)
    function=added[:added.index('"""Functions inserted')]
    exec(function,namespace)
    frame=b'\x00\x00'*320
    flags=[False]*10+[True]*10
    class Vad:
        def __init__(self, mode): self.position=0
        def is_speech(self, pcm, rate):
            result=flags[self.position];self.position+=1;return result
    previous=sys.modules.get('webrtcvad')
    sys.modules['webrtcvad']=types.SimpleNamespace(Vad=Vad)
    namespace['subprocess'].run=lambda *args,**kwargs:types.SimpleNamespace(returncode=0,stdout=frame*len(flags))
    try:
        result=namespace['_analyse_public_speech']('sample.wav')
        assert result['speech_ratio']==.5
        flags[:]=[False]*20
        result=namespace['_analyse_public_speech']('sample.wav')
        assert result['speech_ratio']==0
    finally:
        if previous is None:sys.modules.pop('webrtcvad')
        else:sys.modules['webrtcvad']=previous
    print('PASS: direct/TikTok/Twitch/YouTube resolvers, offline rejection, input errors, zero reading, timeout cleanup and speech frame calculation (mocked audio).')

asyncio.run(run())
