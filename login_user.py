#!/usr/bin/env python3
"""One-time user login for Telethon (OTP + optional 2FA).

Usage:
  uv run python login_user.py --send-otp
  uv run python login_user.py --keep-session --code 12345 --password 'your-2fa'
  TELEGRAM_OTP=12345 TELEGRAM_2FA_PASSWORD=... uv run python login_user.py --keep-session
"""
from __future__ import annotations

import argparse
import asyncio
import os
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
HASH_PATH = paths.DATA_DIR / "phone_code_hash.txt"


def _save_hash(h: str) -> None:
    paths.ensure_data_dir()
    HASH_PATH.write_text(h)


def _load_hash() -> str | None:
    if HASH_PATH.exists():
        return HASH_PATH.read_text().strip() or None
    return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram user session login")
    parser.add_argument("--code", help="OTP from Telegram (or TELEGRAM_OTP env)")
    parser.add_argument("--password", help="2FA password (or TELEGRAM_2FA_PASSWORD env)")
    parser.add_argument(
        "--send-otp",
        action="store_true",
        help="Only send OTP and exit (use with --keep-session)",
    )
    parser.add_argument(
        "--keep-session",
        action="store_true",
        help="Do not delete existing forwarder.session before login",
    )
    args = parser.parse_args()
    otp = (args.code or os.environ.get("TELEGRAM_OTP") or "").strip()
    password = args.password or os.environ.get("TELEGRAM_2FA_PASSWORD")

    paths.ensure_data_dir()
    with open(paths.SECRETS_PATH) as f:
        secrets = yaml.safe_load(f)

    if not args.keep_session:
        for p in (
            paths.DATA_DIR / "forwarder.session",
            paths.DATA_DIR / "forwarder.session-journal",
            HASH_PATH,
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

    if args.send_otp or not otp:
        print(f"Sending OTP to {PHONE} ...")
        try:
            sent = await client.send_code_request(PHONE)
        except FloodWaitError as e:
            print(f"Flood wait: try again in {e.seconds}s")
            sys.exit(1)
        except PhoneNumberBannedError:
            print("Phone number is banned by Telegram.")
            sys.exit(1)
        _save_hash(sent.phone_code_hash)
        if args.send_otp:
            print("OTP sent. Re-run with --keep-session --code CODE --password '...'")
            await client.disconnect()
            return
        print("OTP sent. Re-run with --code or enter below.")
        otp = input("OTP code from Telegram: ").strip()
    else:
        print(f"Using OTP from args for {PHONE}")

    phone_hash = _load_hash()
    if not phone_hash:
        print("No saved OTP session. Run: uv run python login_user.py --keep-session --send-otp")
        sys.exit(3)

    try:
        await client.sign_in(PHONE, otp, phone_code_hash=phone_hash)
    except PhoneCodeInvalidError:
        print("Invalid OTP.")
        sys.exit(1)
    except PhoneCodeExpiredError:
        print("OTP expired. Run: uv run python login_user.py --keep-session --send-otp")
        sys.exit(1)
    except SessionPasswordNeededError:
        if not password:
            print("2FA required. Re-run with: --password '...' or TELEGRAM_2FA_PASSWORD")
            sys.exit(2)
        await client.sign_in(password=password)

    HASH_PATH.unlink(missing_ok=True)
    me = await client.get_me()
    print(f"✓ User logged in as @{me.username or me.first_name} id={me.id}")
    await client.disconnect()
    print()
    print("Session saved: data/forwarder.session")
    print("Next:")
    print("  PYTHONPATH=. uv run python -m okx_bot")


if __name__ == "__main__":
    asyncio.run(main())
