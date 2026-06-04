#!/usr/bin/env bash
# Usage: ./new-instance.sh <name>
# Creates data/<name>/ and runs the setup wizard for that instance.
set -euo pipefail

NAME="${1:?Usage: ./new-instance.sh <name>}"
DATA_DIR="$(pwd)/data/$NAME"

if [ -d "$DATA_DIR" ]; then
    echo "Instance '$NAME' already exists at $DATA_DIR"
    exit 1
fi

mkdir -p "$DATA_DIR"
cp config.example.yaml "$DATA_DIR/config.yaml"
echo "Created $DATA_DIR"
echo ""
echo "Running setup wizard for '$NAME'..."
uv run python -m tui --data-dir "$DATA_DIR" setup
echo ""
echo "Done. To start the daemon:"
echo "  uv run forwarder.py --data-dir $DATA_DIR"
echo ""
echo "To open the TUI:"
echo "  uv run --extra tui python -m tui --data-dir $DATA_DIR"
