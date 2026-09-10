#!/usr/bin/env bash
# One-time: turn scp'd tree into a git clone and start Docker Compose.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME"

# Stop uv daemons so Docker can take over cleanly.
if [ -f tele-forwarder/data/okx_bot.pid ]; then
  kill "$(cat tele-forwarder/data/okx_bot.pid)" 2>/dev/null || true
fi
if [ -f tele-forwarder/data/daemon.pid ]; then
  kill "$(cat tele-forwarder/data/daemon.pid)" 2>/dev/null || true
fi
pkill -f "python -m okx_bot" 2>/dev/null || true
pkill -f "python3 -m okx_bot" 2>/dev/null || true
pkill -f "forwarder.py" 2>/dev/null || true
sleep 2

# Preserve secrets/runtime state.
rm -rf tele-forwarder.bak
mv tele-forwarder tele-forwarder.bak
git clone https://github.com/quicksxope/tele-forwarder.git tele-forwarder
cd tele-forwarder
git checkout main
git reset --hard origin/main

mkdir -p data okx_bot
cp -a ../tele-forwarder.bak/data/. data/ 2>/dev/null || true
cp -a ../tele-forwarder.bak/okx_bot/.env okx_bot/.env
cp -a ../tele-forwarder.bak/okx_bot/channels.yaml okx_bot/channels.yaml

if grep -q "^PREFER_DB_CREDENTIALS=" okx_bot/.env; then
  sed -i "s/^PREFER_DB_CREDENTIALS=.*/PREFER_DB_CREDENTIALS=false/" okx_bot/.env
else
  printf '\nPREFER_DB_CREDENTIALS=false\n' >> okx_bot/.env
fi

printf 'UID=%s\nGID=%s\n' "$(id -u)" "$(id -g)" > .env
chmod 600 okx_bot/.env data/secrets.yaml 2>/dev/null || true

if docker info >/dev/null 2>&1; then
  docker compose up -d --build
  docker compose ps
elif sudo docker info >/dev/null 2>&1; then
  sudo docker compose up -d --build
  sudo docker compose ps
else
  echo "DOCKER_NOT_USABLE" >&2
  exit 1
fi
echo BOOTSTRAP_OK
