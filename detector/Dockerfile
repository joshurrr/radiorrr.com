FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgomp1 \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY detector.py .
COPY sound_intelligence.py .
COPY detector-candidate-scout.py .
COPY config.json .

RUN mkdir -p /app/models /app/logs

CMD ["python", "/app/detector.py"]
