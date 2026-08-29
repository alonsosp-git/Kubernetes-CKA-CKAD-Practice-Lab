#!/usr/bin/env bash
# One-command Docker deployment for k8s-practice-lab.
set -euo pipefail
PORT="${1:-8899}"
IMAGE=k8s-practice-lab
NAME=k8s-lab

command -v docker >/dev/null || { echo "docker is not installed or not on PATH"; exit 1; }

echo "==> building $IMAGE"
docker build -t "$IMAGE" "$(dirname "$0")"

echo "==> (re)starting $NAME on port $PORT"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p "${PORT}:8899" "$IMAGE" >/dev/null

echo "==> waiting for the app"
for _ in $(seq 1 30); do
  if curl -fsS "http://localhost:${PORT}/api/state" >/dev/null 2>&1; then
    echo "==> ready:  http://localhost:${PORT}"
    exit 0
  fi
  sleep 1
done
echo "the container did not become healthy; check: docker logs $NAME"
exit 1
