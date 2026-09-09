#!/usr/bin/env bash
set -u
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/tele-forwarder" || exit 1

if [ -f data/daemon.pid ]; then
  kill "$(cat data/daemon.pid)" 2>/dev/null || true
  sleep 1
fi
pkill -f "python3 forwarder.py" 2>/dev/null || true
sleep 1
rm -f data/daemon.pid

nohup uv run python forwarder.py >/tmp/forwarder-stdout.log 2>&1 &
sleep 4
echo "PID=$(cat data/daemon.pid 2>/dev/null || echo none)"
ps aux | grep -E "[p]ython3 forwarder" | head -2
echo "---- log ----"
tail -n 20 data/forwarder.log
