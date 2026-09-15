#!/usr/bin/env bash
# Run FROM Mac: SSH to the GCP VM and deploy latest main via Docker Compose.
#
# Usage:
#   ./scripts/deploy_from_mac.sh
#   DEPLOY_BRANCH=main ./scripts/deploy_from_mac.sh
#
# Optional env overrides:
#   GCP_INSTANCE  (default: instance-20260909-100326)
#   GCP_ZONE      (default: asia-southeast2-b)
#   GCP_PROJECT   (default: project-f4beb162-04c9-458d-93d)
#   GCP_APP_DIR   (default: /home/user/tele-forwarder)
#   DEPLOY_BRANCH (default: main)
set -euo pipefail

INSTANCE="${GCP_INSTANCE:-instance-20260909-100326}"
ZONE="${GCP_ZONE:-asia-southeast2-b}"
PROJECT="${GCP_PROJECT:-project-f4beb162-04c9-458d-93d}"
APP_DIR="${GCP_APP_DIR:-/home/user/tele-forwarder}"
BRANCH="${DEPLOY_BRANCH:-main}"

# Prefer Homebrew gcloud + Python 3.12 if present (macOS).
if [ -x /opt/homebrew/bin/python3.12 ]; then
  export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.12
fi
if [ -d /opt/homebrew/share/google-cloud-sdk/bin ]; then
  export PATH="/opt/homebrew/share/google-cloud-sdk/bin:$PATH"
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud CLI not found" >&2
  exit 1
fi

echo "==> deploy $BRANCH → $INSTANCE ($ZONE)"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCAL_DEPLOY_SH="$ROOT/scripts/vm_deploy.sh"
if [ ! -f "$LOCAL_DEPLOY_SH" ]; then
  echo "ERROR: missing $LOCAL_DEPLOY_SH" >&2
  exit 1
fi

# Ensure the on-VM helper exists even before the first git pull of this commit.
gcloud compute scp \
  --zone "$ZONE" \
  --project "$PROJECT" \
  "$LOCAL_DEPLOY_SH" \
  "$INSTANCE:$APP_DIR/scripts/vm_deploy.sh"

gcloud compute ssh \
  --zone "$ZONE" \
  --project "$PROJECT" \
  "$INSTANCE" \
  --command "chmod +x '$APP_DIR/scripts/vm_deploy.sh' && GCP_APP_DIR='$APP_DIR' DEPLOY_BRANCH='$BRANCH' bash '$APP_DIR/scripts/vm_deploy.sh'"
