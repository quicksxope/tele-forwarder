"""Run: PYTHONPATH=. uv run python -m okx_bot

Use ONE process only — do not wrap with `while true; python -m okx_bot`.
Auto-reconnect is built in (single asyncio event loop).
"""
from __future__ import annotations

import asyncio
import sys

from .bot import main


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
