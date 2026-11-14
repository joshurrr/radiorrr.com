FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install FFmpeg for live stream relay
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir streamlink==8.6.1 yt-dlp==2026.8.19 webrtcvad-wheels==2.0.14

COPY main.py .
COPY tiktok_liveness.py .
COPY radio_rrr_genre_matcher.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
