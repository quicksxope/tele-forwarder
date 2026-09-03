"""Shim — prefer: PYTHONPATH=. uv run python okx_bot/scripts/run_backtest.py"""
from __future__ import annotations

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).parent / "scripts" / "run_backtest.py"), run_name="__main__")
