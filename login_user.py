#!/usr/bin/env python3
"""One-time user login for tele-forwarder (needs OTP + optional 2FA).

Usage:
  uv run python login_user.py
"""
import asyncio
import getpass
import sys

import yaml
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    SessionPasswordNeededError,
)

import paths

PHONE = "+6287722878726"


async def main() -> None:
    paths.ensure_data_dir()
    with open(paths.SECRETS_PATH) as f:
        secrets = yaml.safe_load(f)

    # Start from a clean user session file
    for p in (
        paths.DATA_DIR / "forwarder.session",
        paths.DATA_DIR / "forwarder.session-journal",
    ):
        if p.exists():
            p.unlink()
            print(f"Removed old {p.name}")

    client = TelegramClient(
        str(paths.USER_SESSION),
        int(secrets["api_id"]),
        secrets["api_hash"],
    )
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Already logged in as @{me.username} id={me.id}")
        await client.disconnect()
        return

    print(f"Sending OTP to {PHONE} ...")
    try:
        await client.send_code_request(PHONE)
    except FloodWaitError as e:
        print(f"Flood wait: try again in {e.seconds}s")
        sys.exit(1)
    except PhoneNumberBannedError:
        print("Phone number is banned by Telegram.")
        sys.exit(1)

    code = input("OTP code from Telegram: ").strip()
    try:
        await client.sign_in(PHONE, code)
    except PhoneCodeInvalidError:
        print("Invalid OTP.")
        sys.exit(1)
    except PhoneCodeExpiredError:
        print("OTP expired. Run again.")
        sys.exit(1)
    except SessionPasswordNeededError:
        pw = getpass.getpass("2FA password: ")
        await client.sign_in(password=pw)

    me = await client.get_me()
    print(f"✓ User logged in as @{me.username} id={me.id}")
    await client.disconnect()
    print()
    print("Next: uv run forwarder.py")


if __name__ == "__main__":
    asyncio.run(main())
