#!/usr/bin/env bash
# Run on the NAS only after approving the deployment.
set -euo pipefail
umask 077

root=/mnt/user/radiorouter
app=$root/app
stage=$root/scanner-fix
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup=$root/scanner-backups/$stamp
old=radiorouter-before-scanner-$stamp
candidate=radiorouter-scanner-$stamp
mkdir -p "$backup"

cd "$app"
printf '%s  %s\n' \
  a0d95a511c27c5b84e436f9b45743d91b32b45b870d9c8af70f8c9661ec57460 main.py \
  16ecf7fc2e3bbb93921f3152d625fd854b5cd2db307c6c1286b5420220795576 Dockerfile \
  | sha256sum -c -
test ! -e tiktok_liveness.py
cp -p main.py Dockerfile "$backup/"
docker inspect radiorouter > "$backup/container.json"
cp "$root/radiorouter-container-inspect.json" "$backup/documented-container.json"
old_image=$(docker inspect --format '{{.Image}}' radiorouter)

# Verify the live settings against the documented container before recreating.
# Keep runtime environment and labels private and carry them into the replacement.
docker run --rm -i --network none --user "$(id -u):$(id -g)" \
  -v "$backup:/snapshot" --entrypoint python "$old_image" -B - <<'PY'
import json
from pathlib import Path

root = Path("/snapshot")
live, saved = (
    json.loads((root / name).read_bytes())[0]
    for name in ("container.json", "documented-container.json")
)
def normalized_host_config(config):
    config = dict(config)
    for key in ("Dns", "Ulimits"):
        if config.get(key) in (None, []):
            config[key] = []
    return config


if normalized_host_config(live["HostConfig"]) != normalized_host_config(saved["HostConfig"]):
    raise SystemExit("Live Docker HostConfig differs from documented configuration. Review before recreating.")
host = live["HostConfig"]
if (
    host["Binds"] != ["/mnt/user/radiorouter/data:/data"]
    or host["PortBindings"] != {"8000/tcp": [{"HostIp": "", "HostPort": "8000"}]}
    or host["RestartPolicy"]["Name"] != "unless-stopped"
    or host["NetworkMode"] not in ("bridge", "default")
    or host["LogConfig"] != {
        "Type": "json-file", "Config": {"max-file": "1", "max-size": "50m"}
    }
):
    raise SystemExit("Unexpected mount, port, restart, network or logging configuration.")
config = live["Config"]
for key in ("Entrypoint", "Cmd", "User", "WorkingDir", "Healthcheck"):
    if config.get(key) != saved["Config"].get(key):
        raise SystemExit(f"Live Docker setting differs from documented configuration: {key}")
for name, lines in (
    ("env", config.get("Env") or []),
    ("labels", [f"{key}={value}" for key, value in (config.get("Labels") or {}).items()]),
):
    if any("\n" in line or "\r" in line for line in lines):
        raise SystemExit(f"Multiline {name} setting requires manual recreation.")
    (root / name).write_text("".join(line + "\n" for line in lines))
PY

# Prove that the staged main.py is exactly the reviewed diff on this baseline.
mkdir -p "$backup/review/backend"
cp "$backup/main.py" "$backup/review/backend/main.py"
(
  cd "$backup/review"
  git apply --check -p2 --directory=backend "$stage/tiktok-scanner-reliability.patch"
  git apply -p2 --directory=backend "$stage/tiktok-scanner-reliability.patch"
  cmp backend/main.py "$stage/backend/main.py"
)

verify() {
  docker run --rm --network none -v "$stage:/verify:ro" -w /verify \
    --entrypoint python "$1" -B -c 'from pathlib import Path
for path in ("backend/main.py", "backend/tiktok_liveness.py", "tests/test_tiktok_scanner.py"):
    compile(Path(path).read_bytes(), path, "exec")' &&
  docker run --rm --network none -v "$stage:/verify:ro" -w /verify \
    --entrypoint python "$1" -B tests/test_tiktok_scanner.py -v
}

verify "$old_image"
cmp main.py "$backup/main.py"
cmp Dockerfile "$backup/Dockerfile"
test ! -e tiktok_liveness.py

restore_source() {
  cp -p "$backup/main.py" "$backup/Dockerfile" "$app/"
  rm -f "$app/tiktok_liveness.py"
}

if ! (
  cp "$stage/backend/main.py" "$stage/backend/Dockerfile" \
    "$stage/backend/tiktok_liveness.py" "$app/" &&
  docker build -t radiorouter:latest . &&
  verify radiorouter:latest
); then
  restore_source
  docker tag "$old_image" radiorouter:latest
  echo "Build or verification failed. Running RadioRouter was left in place." >&2
  exit 1
fi

if ! docker create --name "$candidate" --network bridge \
  --restart unless-stopped -p 8000:8000 -v "$root/data:/data" \
  --log-driver json-file --log-opt max-file=1 --log-opt max-size=50m \
  --env-file "$backup/env" --label-file "$backup/labels" radiorouter:latest; then
  restore_source
  docker tag "$old_image" radiorouter:latest
  exit 1
fi

echo "Rollback container: $old"
echo "Persistent source and configuration backup: $backup"
docker rename radiorouter "$old"
docker stop "$old"
docker rename "$candidate" radiorouter
docker start radiorouter

for attempt in $(seq 1 60); do
  if curl --max-time 2 -fsS http://127.0.0.1:8000/health >/dev/null; then
    curl --max-time 10 -fsS http://127.0.0.1:8000/api/schedule >/dev/null
    curl --max-time 10 -fsS http://127.0.0.1:8000/api/tiktok-scanner-health
    exit 0
  fi
  sleep 1
done
docker logs --tail 60 radiorouter
echo "Replacement did not become healthy. The previous container is available as $old." >&2
exit 1
