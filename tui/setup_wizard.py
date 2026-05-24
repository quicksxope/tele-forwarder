import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    SessionPasswordNeededError,
)
from paths import (
    CONFIG_PATH,
    SECRETS_PATH,
    USER_SESSION,
    BOT_SESSION,
    ensure_data_dir,
)


def _prompt(prompt_text, validator=None, secret=False):
    """Prompt the user for input, re-prompting on validation failure."""
    import getpass
    while True:
        if secret:
            value = getpass.getpass(prompt_text)
        else:
            value = input(prompt_text).strip()
        if validator:
            try:
                return validator(value)
            except ValueError as e:
                print(f"  Error: {e}")
        else:
            if value:
                return value
            print("  Error: value cannot be empty")


def _validate_api_id(value):
    value = value.strip()
    if not value:
        raise ValueError("api_id cannot be empty")
    try:
        int_val = int(value)
    except ValueError:
        raise ValueError(f"api_id must be an integer, got: {value!r}")
    if int_val <= 0:
        raise ValueError("api_id must be a positive integer")
    return int_val


async def _phase1_login(api_id, api_hash, phone_number):
    """Create and authenticate the personal Telegram client. Returns the connected client."""
    session_path = str(USER_SESSION)
    user_client = TelegramClient(session_path, api_id, api_hash)

    while True:
        try:
            await user_client.start(phone=phone_number)
            break
        except PhoneCodeInvalidError:
            print("Invalid code, please try again")
            # Loop: start() will prompt for OTP again
        except SessionPasswordNeededError:
            pw = _prompt("  Two-factor authentication password: ", secret=True)
            await user_client.sign_in(password=pw)
            break
        except PhoneNumberBannedError:
            print("This phone number is banned by Telegram.")
            sys.exit(1)
        except FloodWaitError as e:
            print(f"Too many attempts. Wait {e.seconds} seconds and try again.")
            sys.exit(1)

    me = await user_client.get_me()
    print(f"✓ Personal account connected as @{me.username}")
    return user_client


async def _phase2_bot(api_id, api_hash):
    """Create and authenticate the bot client. Returns (connected client, bot info)."""
    session_path = str(BOT_SESSION)

    while True:
        bot_token = _prompt("  Bot token (from @BotFather): ")
        bot_client = TelegramClient(session_path, api_id, api_hash)
        try:
            await bot_client.start(bot_token=bot_token)
            bot_info = await bot_client.get_me()
            print(f"✓ Bot connected as @{bot_info.username}")
            return bot_client, bot_info, bot_token
        except Exception as e:
            print(f"  Failed to connect bot: {e}")
            retry = input("  Retry? (y/n): ").strip().lower()
            if retry != 'y':
                sys.exit(1)
            # Disconnect before retrying
            try:
                await bot_client.disconnect()
            except Exception:
                pass


def _write_secrets(api_id, api_hash, bot_token):
    content = (
        f"api_id: {api_id}\n"
        f'api_hash: "{api_hash}"\n'
        f'bot_token: "{bot_token}"\n'
    )
    with open(SECRETS_PATH, 'w') as f:
        f.write(content)
    os.chmod(SECRETS_PATH, 0o600)
    print(f"  Written: {SECRETS_PATH}")


def _write_config_if_missing():
    if CONFIG_PATH.exists():
        print(f"  config.yaml already exists — skipping.")
        return
    content = (
        "# restart_cmd is run by the TUI when you save a rule.\n"
        "# Use 'docker compose restart forwarder' for Docker or\n"
        "# 'systemctl --user restart tele-forwarder' for systemd.\n"
        "restart_cmd: docker compose restart forwarder\n"
        "\n"
        "sources: []\n"
    )
    with open(CONFIG_PATH, 'w') as f:
        f.write(content)
    print(f"  Written: {CONFIG_PATH}")


async def async_main():
    ensure_data_dir()
    print("=== Tele-Forwarder Setup ===\n")

    # Phase 1 — Personal account
    print("Phase 1: Personal Telegram account")
    print("Get your api_id and api_hash from https://my.telegram.org → API development tools\n")

    api_id = _prompt("  api_id: ", validator=_validate_api_id)
    api_hash = _prompt("  api_hash: ")
    phone_number = _prompt("  Phone number (e.g. +1234567890): ")

    user_client = await _phase1_login(api_id, api_hash, phone_number)

    # Phase 2 — Bot account
    print("\nPhase 2: Bot account (for posting to destination groups)")
    print("1. Open Telegram and message @BotFather")
    print("2. Send /newbot and follow the instructions")
    print("3. Copy the bot token BotFather gives you\n")

    bot_client, bot_info, bot_token = await _phase2_bot(api_id, api_hash)

    print(f"\nIMPORTANT: Add @{bot_info.username} to each destination group and grant it 'Send Messages' permission.")

    # Disconnect both clients
    await user_client.disconnect()
    await bot_client.disconnect()

    # Write output files
    print("\nWriting configuration files...")
    _write_secrets(api_id, api_hash, bot_token)
    _write_config_if_missing()

    print("\nSetup complete!")
    print()
    print("Next steps:")
    print("  1. Edit config.yaml and add your forwarding rules (or use: python -m tui)")
    print("  2. Start the daemon: python forwarder.py")
    print("  3. Install as a service: see tele-forwarder.service")


def run_setup():
    asyncio.run(async_main())
