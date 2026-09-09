#!/usr/bin/env bash
set -u
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/tele-forwarder" || exit 1

# Stop forwarder
if [ -f data/daemon.pid ]; then
  kill "$(cat data/daemon.pid)" 2>/dev/null || true
fi
pkill -f "python3 forwarder.py" 2>/dev/null || true
pkill -f "uv run python forwarder.py" 2>/dev/null || true
sleep 2
rm -f data/daemon.pid
echo "stopped"

# Advance scan_state to newest message per source chat
PYTHONPATH=. uv run python <<'PY'
import asyncio
import sqlite3
from pathlib import Path

import yaml
from telethon import TelegramClient

DATA = Path("data")
cfg = yaml.safe_load((DATA / "config.yaml").read_text())
secrets = yaml.safe_load((DATA / "secrets.yaml").read_text())
api_id = int(secrets["api_id"])
api_hash = secrets["api_hash"]
sources = [int(s["chat_id"]) for s in cfg.get("sources", [])]
print("sources:", sources)


async def main() -> None:
    client = TelegramClient(str(DATA / "forwarder"), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("forwarder.session not authorized")
    conn = sqlite3.connect(DATA / "mappings.db")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS scan_state (
            chat_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL DEFAULT 0,
            last_scanned_msg_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, topic_id))"""
    )
    for chat_id in sources:
        latest = 0
        async for msg in client.iter_messages(chat_id, limit=1):
            latest = msg.id
        conn.execute(
            "INSERT OR REPLACE INTO scan_state (chat_id, topic_id, last_scanned_msg_id) VALUES (?,?,?)",
            (chat_id, 0, latest),
        )
        print(f"scan_state {chat_id} -> {latest}")
    conn.commit()
    conn.close()
    await client.disconnect()
    print("scan_state updated")


asyncio.run(main())
PY

# Restart
nohup uv run python forwarder.py >/tmp/forwarder-stdout.log 2>&1 &
sleep 5
echo "PID_FILE=$(cat data/daemon.pid 2>/dev/null || echo none)"
ps aux | grep -E "[p]ython3 forwarder" | head -3
echo "---- log ----"
tail -n 40 data/forwarder.log
