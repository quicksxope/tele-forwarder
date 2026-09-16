#!/usr/bin/env python3
"""Inspect recent Cryptocium forum messages: topic id, match, parse."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

import yaml
from telethon import TelegramClient

from okx_bot.bot import _message_topic_id
from okx_bot.channels import list_enabled_channels, match_channel


async def main() -> None:
    data = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", ROOT.parent / "data"))
    secrets = yaml.safe_load((data / "secrets.yaml").read_text())
    api_id = int(secrets["api_id"])
    api_hash = secrets["api_hash"]
    session = str(data / "okx_user")

    channels = list_enabled_channels()
    crypto = next((c for c in channels if c.key == "cryptocium"), None)
    if not crypto:
        print("cryptocium channel not in config")
        return

    client = TelegramClient(session, api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("okx_user session not authorized")
        return

    print(f"Configured cryptocium: chat_id={crypto.chat_id} topic_id={crypto.topic_id}")
    print("Last 25 messages in chat (any topic):\n")

    async for msg in client.iter_messages(crypto.chat_id, limit=25):
        topic = _message_topic_id(msg.message)
        text = (msg.message or msg.text or "")[:200].replace("\n", " | ")
        src = match_channel(channels, msg.chat_id, topic)
        matched = src.key if src else None
        has_setup = "SETUP" in (msg.message or "").upper()
        parsed = src.parse(msg.message or "") if src else None
        print(
            f"id={msg.id} date={msg.date} topic={topic} "
            f"match={matched} SETUP={has_setup} parsed={'yes' if parsed else 'no'}"
        )
        if text:
            print(f"  {text[:180]}")
        print()

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
