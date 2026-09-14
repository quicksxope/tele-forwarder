#!/usr/bin/env bash
# Deploy local okx_bot (incl. Binance support) to the GCP VM and restart the container.
#
# Run FROM your Mac (requires gcloud auth + SSH to the VM):
#   ./scripts/gcp_okx_binance_deploy.sh
#   SYNC_ENV=1 ./scripts/gcp_okx_binance_deploy.sh   # also copy okx_bot/.env (secrets)
#   RUN_TEST=1 ./scripts/gcp_okx_binance_deploy.sh   # run test_binance_order.py in container
#
# Optional env:
#   GCP_INSTANCE, GCP_ZONE, GCP_PROJECT, GCP_APP_DIR (same defaults as deploy_from_mac.sh)
set -euo pipefail

INSTANCE="${GCP_INSTANCE:-instance-20260909-100326}"
ZONE="${GCP_ZONE:-asia-southeast2-b}"
PROJECT="${GCP_PROJECT:-project-f4beb162-04c9-458d-93d}"
APP_DIR="${GCP_APP_DIR:-/home/user/tele-forwarder}"
SYNC_ENV="${SYNC_ENV:-0}"
RUN_TEST="${RUN_TEST:-0}"

if [ -x /opt/homebrew/bin/python3.12 ]; then
  export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.12
fi
if [ -d /opt/homebrew/share/google-cloud-sdk/bin ]; then
  export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
fi

command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud not found" >&2
  exit 1
}

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="user@${INSTANCE}:${APP_DIR}"

echo "==> Sync okx_bot Python files → $INSTANCE"
gcloud compute scp --zone "$ZONE" --project "$PROJECT" \
  "$ROOT/okx_bot/trader.py" \
  "$ROOT/okx_bot/bot.py" \
  "$ROOT/okx_bot/settings_menu.py" \
  "$ROOT/okx_bot/__init__.py" \
  "${REMOTE}/okx_bot/"

gcloud compute scp --zone "$ZONE" --project "$PROJECT" \
  "$ROOT/okx_bot/scripts/test_binance_order.py" \
  "${REMOTE}/okx_bot/scripts/"

if [ "$SYNC_ENV" = "1" ]; then
  if [ ! -f "$ROOT/okx_bot/.env" ]; then
    echo "ERROR: okx_bot/.env missing locally" >&2
    exit 1
  fi
  echo "==> Sync okx_bot/.env (contains secrets)"
  gcloud compute scp --zone "$ZONE" --project "$PROJECT" \
    "$ROOT/okx_bot/.env" "${REMOTE}/okx_bot/.env"
fi

echo "==> Patch exchange=binance on VM (keeps existing API keys unless SYNC_ENV=1)"
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --project "$PROJECT" --command "
set -e
cd '$APP_DIR'
chmod 600 okx_bot/.env 2>/dev/null || true
grep -q '^EXCHANGE=' okx_bot/.env && sed -i 's|^EXCHANGE=.*|EXCHANGE=binance|' || echo 'EXCHANGE=binance' >> okx_bot/.env
grep -q '^BINANCE_DEMO=' okx_bot/.env && sed -i 's|^BINANCE_DEMO=.*|BINANCE_DEMO=true|' || echo 'BINANCE_DEMO=true' >> okx_bot/.env
grep -q '^BINANCE_SANDBOX=' okx_bot/.env && sed -i 's|^BINANCE_SANDBOX=.*|BINANCE_SANDBOX=false|' || echo 'BINANCE_SANDBOX=false' >> okx_bot/.env
grep -q '^PREFER_DB_CREDENTIALS=' okx_bot/.env && sed -i 's|^PREFER_DB_CREDENTIALS=.*|PREFER_DB_CREDENTIALS=false|' || echo 'PREFER_DB_CREDENTIALS=false' >> okx_bot/.env
docker compose up -d --build okx_bot
docker compose ps okx_bot
"

if [ "$RUN_TEST" = "1" ]; then
  echo "==> Binance connectivity test (inside container)"
  gcloud compute ssh "$INSTANCE" --zone "$ZONE" --project "$PROJECT" --command "
cd '$APP_DIR'
docker compose exec -T okx_bot python okx_bot/scripts/test_binance_order.py
"
fi

echo "==> Logs (last 30 lines)"
gcloud compute ssh "$INSTANCE" --zone "$ZONE" --project "$PROJECT" --command "
cd '$APP_DIR' && docker compose logs --tail=30 okx_bot
"

IP="$(gcloud compute instances describe "$INSTANCE" --zone "$ZONE" --project "$PROJECT" --format='get(networkInterfaces[0].accessConfigs[0].natIP)')"
echo "DEPLOY_OK — VM: $INSTANCE ($IP)"
