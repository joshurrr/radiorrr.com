#!/bin/bash
set -euo pipefail

mapfile -t scouts < <(
  for container in $(docker ps --format '{{.Names}}'); do
    command=$(docker inspect --format '{{json .Config.Cmd}}' "$container")
    if [[ "$command" == *detector-candidate-scout.py* ]]; then
      printf '%s\n' "$container"
    fi
  done
)
if [[ ${#scouts[@]} -ne 1 ]]; then
  echo "Expected one running candidate scout; found ${#scouts[@]}. No containers were changed."
  docker ps --format '{{.Names}} {{.Command}}'
  exit 1
fi
scout=${scouts[0]}
scout_image=$(docker inspect --format '{{.Config.Image}}' "$scout")

docker build -t radiorouter:latest /mnt/user/radiorouter/app
docker build -t "$scout_image" /mnt/user/rrr-genredetect
# Copy into existing containers to preserve their mounts, environment and ports.
docker cp /mnt/user/radiorouter/app/main.py radiorouter:/app/main.py
docker cp /mnt/user/rrr-genredetect/detector-candidate-scout.py "$scout":/app/detector-candidate-scout.py
docker restart radiorouter
ready=0
for attempt in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:8000/api/engines >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo 'RadioRouter did not become ready. Check its logs before restarting the scout.'
  docker logs --tail 60 radiorouter
  exit 1
fi
docker restart "$scout"
echo "Priority audio scanning enabled in radiorouter and $scout."
echo 'Publish the updated website script to enable priority requests when a DJ is clicked.'
