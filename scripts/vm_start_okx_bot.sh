#!/usr/bin/env bash
# Sync okx_bot code + start with local credentials on the VM.
set -u
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/tele-forwarder" || exit 1

# Ensure encryption key exists for /settings (do not overwrite if set)
if ! grep -q '^CREDENTIAL_ENCRYPTION_KEY=.\+' okx_bot/.env 2>/dev/null; then
  KEY=$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
  if grep -q '^CREDENTIAL_ENCRYPTION_KEY=' okx_bot/.env 2>/dev/null; then
    sed -i "s|^CREDENTIAL_ENCRYPTION_KEY=.*|CREDENTIAL_ENCRYPTION_KEY=${KEY}|" okx_bot/.env
  else
    printf '\nCREDENTIAL_ENCRYPTION_KEY=%s\n' "$KEY" >> okx_bot/.env
  fi
  echo "CREDENTIAL_ENCRYPTION_KEY set"
fi

chmod 600 okx_bot/.env data/secrets.yaml 2>/dev/null || true

# Stop existing okx_bot
if [ -f data/okx_bot.pid ]; then
  kill "$(cat data/okx_bot.pid)" 2>/dev/null || true
fi
pkill -f "python -m okx_bot" 2>/dev/null || true
pkill -f "python3 -m okx_bot" 2>/dev/null || true
sleep 2

nohup env PYTHONPATH=. uv run python -m okx_bot >/tmp/okx_bot-stdout.log 2>&1 &
echo $! > data/okx_bot.pid
sleep 5
echo "OKX_PID=$(cat data/okx_bot.pid)"
ps aux | grep -E "[p]ython -m okx_bot|[p]ython3 -m okx_bot" | head -3
echo "---- stdout ----"
tail -n 40 /tmp/okx_bot-stdout.log
