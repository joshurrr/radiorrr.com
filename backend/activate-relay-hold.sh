#!/bin/bash
set -euo pipefail
# Preserve the existing container's environment, ports and persistent /data mount.
docker build -t radiorouter:latest /mnt/user/radiorouter/app
docker cp /mnt/user/radiorouter/app/main.py radiorouter:/app/main.py
docker restart radiorouter
curl --retry 20 --retry-connrefused --retry-delay 2 --fail --silent --show-error http://127.0.0.1:8000/api/status
