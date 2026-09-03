#!/usr/bin/env bash
# Start tele-forwarder daemon (requires user session already logged in).
set -euo pipefail
cd "$(dirname "$0")"
source "$HOME/.local/bin/env" 2>/dev/null || true
exec uv run forwarder.py "$@"
