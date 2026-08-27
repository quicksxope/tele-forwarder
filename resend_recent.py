#!/usr/bin/env python3
"""
Resend recent messages from source chat, grouping albums correctly.
Usage: uv run python resend_recent.py
"""
import asyncio
import os
import sqlite3
import sys
import yaml
from collections import defaultdict

import paths

SOURCE_CHAT = -1002290536326
DEST_CHAT = 6878724303

FETCH_LIMIT = 30  # recent messages to look back


def get_mapping(conn, source_chat_id, source_msg_id):
    return conn.execute(
        'SELECT dest_chat_id, dest_msg_id FROM message_map '
        'WHERE source_chat_id=? AND source_msg_id=?',
        (source_chat_id, source_msg_id),
    ).fetchone()


def save_mapping(conn, source_chat_id, source_msg_id, dest_chat_id, dest_msg_id):
    conn.execute(
        'INSERT OR REPLACE INTO message_map VALUES (?,?,?,?)',
        (source_chat_id, source_msg_id, dest_chat_id, dest_msg_id),
    )
    conn.commit()


async def download(user_client, message):
    import tempfile
    from telethon.tl.types import MessageMediaPhoto, MessageMediaDocument
    if isinstance(message.media, MessageMediaPhoto):
        ext = '.jpg'
    elif isinstance(message.media, MessageMediaDocument):
        from telethon.utils import get_extension
        ext = get_extension(message.media.document) or '.bin'
    else:
        ext = ''
    fd, path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    await user_client.download_media(message, file=path)
    return path


async def send_album(bot_client, messages, user_client, dest_chat):
    paths_list = [await download(user_client, m) for m in messages]
    try:
        caption_msg = next((m for m in messages if m.message), None)
        caption = caption_msg.message if caption_msg else ''
        caption_entities = caption_msg.entities or [] if caption_msg else []
        sent = await bot_client.send_file(
            entity=dest_chat,
            file=paths_list,
            caption=caption,
            formatting_entities=caption_entities,
        )
        return sent if isinstance(sent, list) else [sent]
    finally:
        for p in paths_list:
            os.unlink(p)


async def send_single(bot_client, message, user_client, dest_chat):
    if message.media:
        path = await download(user_client, message)
        try:
            sent = await bot_client.send_file(
                entity=dest_chat,
                file=path,
                caption=message.message or '',
                formatting_entities=message.entities or [],
            )
        finally:
            os.unlink(path)
    else:
        sent = await bot_client.send_message(
            entity=dest_chat,
            message=message.message or '',
            formatting_entities=message.entities or [],
        )
    return sent


async def main():
    paths.ensure_data_dir()
    with open(paths.SECRETS_PATH) as f:
        secrets = yaml.safe_load(f)

    from telethon import TelegramClient
    user_client = TelegramClient(str(paths.USER_SESSION), secrets['api_id'], secrets['api_hash'])
    bot_client = TelegramClient(str(paths.BOT_SESSION), secrets['api_id'], secrets['api_hash'])

    db = sqlite3.connect(str(paths.DB_PATH), check_same_thread=False)

    await user_client.start()
    await bot_client.start(bot_token=secrets['bot_token'])
    print("Clients connected")

    print(f"\n--- {SOURCE_CHAT} → {DEST_CHAT} (no topics) ---")

    msgs = []
    async for msg in user_client.iter_messages(SOURCE_CHAT, limit=FETCH_LIMIT):
        msgs.append(msg)

    msgs.reverse()  # oldest first
    print(f"  Fetched {len(msgs)} messages")

    # Group by grouped_id, preserving order
    already_sent = 0
    albums: dict[int, list] = defaultdict(list)
    singles = []
    for msg in msgs:
        if get_mapping(db, SOURCE_CHAT, msg.id):
            already_sent += 1
            continue
        if msg.grouped_id:
            albums[msg.grouped_id].append(msg)
        else:
            singles.append(msg)

    print(f"  Already forwarded: {already_sent}")
    print(f"  Albums to send: {len(albums)} groups")
    print(f"  Singles to send: {len(singles)}")

    for grouped_id, album_msgs in albums.items():
        # Sort album by message id (ascending)
        album_msgs.sort(key=lambda m: m.id)
        ids = [m.id for m in album_msgs]
        print(f"  Sending album {grouped_id}: msg ids {ids}")
        try:
            sent_list = await send_album(bot_client, album_msgs, user_client, DEST_CHAT)
            for src_msg, sent in zip(album_msgs, sent_list):
                save_mapping(db, SOURCE_CHAT, src_msg.id, DEST_CHAT, sent.id)
            print(f"    → Sent as album ({len(sent_list)} msgs)")
        except Exception as e:
            print(f"    ERROR: {e}")

    for msg in singles:
        print(f"  Sending single msg {msg.id}")
        try:
            sent = await send_single(bot_client, msg, user_client, DEST_CHAT)
            save_mapping(db, SOURCE_CHAT, msg.id, DEST_CHAT, sent.id)
            print(f"    → Sent (dest msg {sent.id})")
        except Exception as e:
            print(f"    ERROR: {e}")

    print("\nDone.")
    await user_client.disconnect()
    await bot_client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
