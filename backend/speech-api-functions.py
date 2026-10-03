"""Functions inserted into RadioRouter main.py by the accompanying patch."""
speech_tool_semaphore = asyncio.Semaphore(2)
SPEECH_TOOL_SAMPLE_SECONDS = 30
SPEECH_TOOL_DIR = Path('/tmp/radiorouter-speech-tool')


async def _resolve_public_speech_stream(url):
    parsed = urlparse(url)
    host = parsed.hostname.casefold().rstrip('.')
    if host in {'tiktok.com', 'www.tiktok.com'}:
        match = re.fullmatch(r'/@([A-Za-z0-9._]{1,24})(?:/live)?/?', parsed.path)
        if not match:
            raise ValueError('Use a TikTok channel live URL, such as https://www.tiktok.com/@username/live')
        return await get_tiktok_stream(match.group(1))
    if host in {'twitch.tv', 'www.twitch.tv', 'm.twitch.tv'}:
        match = re.fullmatch(r'/([A-Za-z0-9_]{2,25})/?', parsed.path)
        if not match:
            raise ValueError('Use a Twitch channel URL, such as https://www.twitch.tv/username')
        return await get_twitch_stream(match.group(1))
    if host in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
        if not (parsed.path == '/watch' or re.fullmatch(r'/(?:live/|@|channel/)?[A-Za-z0-9_-]+(?:/live)?/?', parsed.path)):
            raise ValueError('Use a YouTube live video or channel URL')
        source = url
        if parsed.path.startswith(('/@', '/channel/')) and not parsed.path.rstrip('/').endswith('/live'):
            source = url.split('?', 1)[0].rstrip('/') + '/live'
        result = await asyncio.to_thread(_run_ytdlp, [
            '--dump-single-json', '--no-playlist', '--skip-download',
            '--format', 'bestaudio/best', source,
        ])
        if result.returncode != 0:
            raise RuntimeError('Could not resolve this YouTube live stream. Check that it is public and live.')
        try:
            info = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError('YouTube returned invalid stream metadata') from error
        if not isinstance(info, dict) or info.get('is_live') is not True:
            raise RuntimeError('This YouTube video is not currently live')
        return {'url': info.get('url'), 'headers': info.get('http_headers') or {}}
    return {'url': url, 'headers': {}}


def _capture_public_speech_sample(stream, output_path):
    headers = dict(stream.get('headers') or {})
    cookies = stream.get('cookies') or {}
    if cookies:
        headers['Cookie'] = '; '.join(f'{key}={value}' for key, value in cookies.items())
    header_lines = []
    for name, value in headers.items():
        if not re.fullmatch(r'[A-Za-z0-9-]+', str(name)) or '\r' in str(value) or '\n' in str(value):
            raise RuntimeError('Invalid headers returned by the stream resolver')
        header_lines.append(f'{name}: {value}\r\n')
    command = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-reconnect', '1', '-reconnect_streamed', '1',
        '-reconnect_on_network_error', '1', '-reconnect_delay_max', '3',
        '-rw_timeout', '15000000', '-protocol_whitelist', 'http,https,tcp,tls,crypto',
    ]
    if header_lines:
        command += ['-headers', ''.join(header_lines)]
    command += [
        '-i', stream['url'], '-t', str(SPEECH_TOOL_SAMPLE_SECONDS), '-vn',
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(output_path),
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            timeout=SPEECH_TOOL_SAMPLE_SECONDS + 30, check=False)
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError('Could not read audio from this URL. Use a public direct stream or a supported live platform page.')
    with wave.open(str(output_path), 'rb') as sample:
        duration = sample.getnframes() / sample.getframerate()
    if duration < 5:
        raise RuntimeError('The stream did not provide at least five seconds of audio')
    analysis = _analyse_public_speech(str(output_path))
    if not analysis:
        raise RuntimeError('The speech detector could not analyse this audio sample')
    return analysis, duration


@app.post('/api/tools/speech')
async def public_speech_detector(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='Invalid JSON request')
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail='Expected a JSON object with a URL')
    try:
        url = await asyncio.to_thread(_validate_public_stream_url, data.get('url'))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    if speech_tool_semaphore.locked():
        raise HTTPException(status_code=429, detail='The speech detector is busy. Try again shortly.')
    SPEECH_TOOL_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = SPEECH_TOOL_DIR / f'{uuid4().hex}.wav'
    try:
        async with speech_tool_semaphore:
            stream = await _resolve_public_speech_stream(url)
            if not isinstance(stream, dict):
                raise RuntimeError('This stream is not currently available')
            stream['url'] = await asyncio.to_thread(
                _validate_public_stream_url, stream.get('audio_url') or stream.get('url'))
            analysis, duration = await asyncio.to_thread(_capture_public_speech_sample, stream, wav_path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail='The stream timed out while sampling audio')
    except (RuntimeError, OSError) as error:
        raise HTTPException(status_code=422, detail=str(error) if isinstance(error, RuntimeError) else 'Stream analysis is temporarily unavailable')
    finally:
        try:
            wav_path.unlink(missing_ok=True)
        except OSError:
            pass
    return {'ok': True, **analysis, 'sample_seconds': round(duration, 2)}
