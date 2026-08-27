"""Run: uv run python -m okx_bot"""
from .bot import main
import asyncio

asyncio.run(main())
