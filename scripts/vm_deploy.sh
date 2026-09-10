#!/usr/bin/env bash
# Run ON the GCP VM: pull latest main and rebuild Docker Compose services.
# Secrets (data/, okx_bot/.env, channels.yaml) are left untouched.
set -euo pipefail

APP_DIR="${GCP_APP_DIR:-$HOME/tele-forwarder}"
BRANCH="${DEPLOY_BRANCH:-main}"

cd "$APP_DIR"

if [ ! -d .git ]; then
  echo "ERROR: $APP_DIR is not a git clone" >&2
  exit 1
fi

if [ ! -f okx_bot/.env ]; then
  echo "ERROR: missing okx_bot/.env (do not overwrite from git)" >&2
  exit 1
fi

if [ ! -f okx_bot/channels.yaml ]; then
  echo "ERROR: missing okx_bot/channels.yaml" >&2
  exit 1
fi

# Compose UID/GID file (bash UID is readonly — do not export it).
if [ ! -f .env ]; then
  printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env
fi

echo "==> git fetch/reset $BRANCH"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"
echo "HEAD=$(git rev-parse --short HEAD)"

echo "==> docker compose up -d --build"
if docker info >/dev/null 2>&1; then
  docker compose up -d --build
  docker compose ps
elif sudo docker info >/dev/null 2>&1; then
  sudo docker compose up -d --build
  sudo docker compose ps
else
  echo "ERROR: docker not usable for this user" >&2
  exit 1
fi

echo "DEPLOY_OK"
