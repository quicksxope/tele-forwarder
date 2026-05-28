#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import sqlite3
import time
import yaml
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import (
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
    DocumentAttributeAudio,
    DocumentAttributeAnimated,
)
import paths

logger = logging.getLogger(__name__)

ALBUM_FLUSH_DELAY = 0.8   # seconds to wait before flushing a buffered album
GAP_FILL_INTERVAL = 300   # seconds between gap-filler scans (5 min)
GAP_FILL_BATCH    = 500   # max messages fetched per scan per chat
CAPTION_MAX_LEN   = 1024  # Telegram hard limit for media captions
TEXT_MAX_LEN      = 4096  # Telegram hard limit for plain text messages

LOG_FORMAT = '%(asctime)s %(levelname)s %(message)s'
LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'


def load_config(path=None):
    if path is None:
        path = paths.CONFIG_PATH
    with open(path) as f:
        return yaml.safe_load(f)


def setup_logging(config):
    log_path = paths.LOG_PATH
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.INFO)
    stream_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)


def init_db(path=None):
    if path is None:
        path = paths.DB_PATH
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS message_map (
            source_chat_id INTEGER NOT NULL,
            source_msg_id  INTEGER NOT NULL,
            dest_chat_id   INTEGER NOT NULL,
            dest_msg_id    INTEGER NOT NULL,
            PRIMARY KEY (source_chat_id, source_msg_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS scan_state (
            chat_id             INTEGER NOT NULL,
            topic_id            INTEGER NOT NULL DEFAULT 0,
            last_scanned_msg_id INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (chat_id, topic_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS forward_events (
            ts             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source_chat_id INTEGER NOT NULL,
            source_msg_id  INTEGER NOT NULL,
            status         TEXT NOT NULL,
            error_text     TEXT
        )
    ''')
    conn.commit()
    return conn


def save_mapping(conn, source_chat_id, source_msg_id, dest_chat_id, dest_msg_id):
    conn.execute(
        'INSERT OR REPLACE INTO message_map VALUES (?,?,?,?)',
        (source_chat_id, source_msg_id, dest_chat_id, dest_msg_id),
    )
    conn.commit()


def get_mapping(conn, source_chat_id, source_msg_id):
    return conn.execute(
        'SELECT dest_chat_id, dest_msg_id FROM message_map '
        'WHERE source_chat_id=? AND source_msg_id=?',
        (source_chat_id, source_msg_id),
    ).fetchone()


def get_scan_state(conn, chat_id, topic_id=0):
    row = conn.execute(
        'SELECT last_scanned_msg_id FROM scan_state WHERE chat_id=? AND topic_id=?',
        (chat_id, topic_id),
    ).fetchone()
    return row[0] if row else 0


def set_scan_state(conn, chat_id, last_msg_id, topic_id=0):
    conn.execute(
        'INSERT OR REPLACE INTO scan_state (chat_id, topic_id, last_scanned_msg_id) VALUES (?,?,?)',
        (chat_id, topic_id, last_msg_id),
    )
    conn.commit()


def get_mappings_for_ids(conn, source_chat_id, source_msg_ids):
    ph = ','.join('?' * len(source_msg_ids))
    return conn.execute(
        f'SELECT source_msg_id, dest_chat_id, dest_msg_id FROM message_map '
        f'WHERE source_chat_id=? AND source_msg_id IN ({ph})',
        [source_chat_id] + list(source_msg_ids),
    ).fetchall()


def record_event(conn, source_chat_id, source_msg_id, status, error_text=None):
    conn.execute(
        'INSERT INTO forward_events (source_chat_id, source_msg_id, status, error_text) VALUES (?,?,?,?)',
        (source_chat_id, source_msg_id, status, error_text),
    )
    conn.commit()


def resolve_reply_to(db, message, dest_topic):
    """Return the reply_to value to use when re-sending a message.

    If the source message replies to a message we already forwarded, point to
    the forwarded copy. Otherwise fall back to dest_topic (keeps forum thread).
    """
    if not message.reply_to:
        return dest_topic
    reply_msg_id = getattr(message.reply_to, 'reply_to_msg_id', None)
    if not reply_msg_id:
        return dest_topic
    mapping = get_mapping(db, message.chat_id, reply_msg_id)
    if mapping:
        return mapping[1]  # dest_msg_id
    return dest_topic


def build_source_index(config):
    """Returns dict: chat_id -> list of rule dicts."""
    index = defaultdict(list)
    for rule in config.get('sources', []):
        index[rule['chat_id']].append(rule)
    return dict(index)


def get_message_topic(message):
    """Return topic thread ID for messages inside a forum topic, else None.

    In Telegram forums, the General topic (id=1) never sets reply_to on its
    messages. All other topics set reply_to_top_id (for replies) or
    reply_to_msg_id (for top-level posts within the topic).
    """
    if not message.reply_to:
        return 1  # No reply_to → General topic
    return message.reply_to.reply_to_top_id or message.reply_to.reply_to_msg_id


def get_media_type(message):
    if not message.media:
        return 'text'
    if isinstance(message.media, MessageMediaPhoto):
        return 'photo'
    if isinstance(message.media, MessageMediaDocument):
        for attr in message.media.document.attributes:
            if isinstance(attr, DocumentAttributeVideo):
                return 'video'
            if isinstance(attr, DocumentAttributeAudio):
                return 'audio'
            if isinstance(attr, DocumentAttributeAnimated):
                return 'gif'
        return 'document'
    return 'other'


def matches_rule(message, rule):
    # Topic filter
    topics = rule.get('topics', 'all')
    if topics != 'all':
        topic = get_message_topic(message)
        if topic not in topics:
            return False

    # Keyword filter (empty list = allow all)
    keywords = rule.get('filters', {}).get('keywords') or []
    if keywords:
        text = (message.text or '').lower()
        if not any(kw.lower() in text for kw in keywords):
            return False

    # Media type filter (empty list = allow all)
    media_types = rule.get('filters', {}).get('media_types') or []
    if media_types and get_media_type(message) not in media_types:
        return False

    return True


async def _download(user_client, message):
    """Download media to a temp file, returning the path. Caller must delete it."""
    import tempfile, os
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


async def resend_single(user_client, bot_client, rule, message, reply_to=None):
    """Re-send a single message without the 'Forwarded from' header."""
    import os
    dest_id = rule['destination']['chat_id']

    if message.media:
        path = await _download(user_client, message)
        try:
            sent = await bot_client.send_file(
                entity=dest_id,
                file=path,
                caption=(message.message or '')[:CAPTION_MAX_LEN],
                formatting_entities=message.entities or [],
                reply_to=reply_to,
            )
        finally:
            os.unlink(path)
    else:
        sent = await bot_client.send_message(
            entity=dest_id,
            message=(message.message or '')[:TEXT_MAX_LEN],
            formatting_entities=message.entities or [],
            reply_to=reply_to,
        )
    return sent


async def resend_album(user_client, bot_client, rule, messages, reply_to=None):
    """Re-send a grouped album without the 'Forwarded from' header."""
    import os
    dest_id = rule['destination']['chat_id']

    paths = [await _download(user_client, m) for m in messages]
    try:
        caption_msg = next((m for m in messages if m.message), None)
        caption = (caption_msg.message if caption_msg else '')[:CAPTION_MAX_LEN]
        caption_entities = (caption_msg.entities or []) if caption_msg else []
        sent = await bot_client.send_file(
            entity=dest_id,
            file=paths,
            caption=caption,
            formatting_entities=caption_entities,
            reply_to=reply_to,
        )
    finally:
        for p in paths:
            os.unlink(p)
    return sent if isinstance(sent, list) else [sent]


class Forwarder:
    def __init__(self, config, user_client, bot_client, db):
        self.config = config
        self.user_client = user_client
        self.bot_client = bot_client
        self.db = db
        self.source_index = build_source_index(config)
        # grouped_id -> list of (message, rule)
        self._album_buffer: dict[int, list] = defaultdict(list)
        self._album_tasks: dict[int, asyncio.Task] = {}

    def register_handlers(self):
        source_ids = list(self.source_index.keys())
        self.user_client.add_event_handler(self.on_new_message, events.NewMessage(chats=source_ids))
        self.user_client.add_event_handler(self.on_message_edited, events.MessageEdited(chats=source_ids))
        self.user_client.add_event_handler(self.on_message_deleted, events.MessageDeleted(chats=source_ids))
        logger.info(f'Listening on {len(source_ids)} source chat(s)')
        asyncio.ensure_future(self.gap_filler())

    async def gap_filler(self):
        """Runs at startup then every GAP_FILL_INTERVAL seconds.
        Catches messages the live handler missed (daemon offline, transient send error).
        """
        await asyncio.sleep(10)  # let clients fully connect first
        while True:
            try:
                await self._run_gap_fill()
            except Exception as e:
                logger.error(f'[gap_fill] Scan error: {e}')
            await asyncio.sleep(GAP_FILL_INTERVAL)

    async def _run_gap_fill(self):
        for chat_id, rules in self.source_index.items():
            last_id = get_scan_state(self.db, chat_id)
            msgs = []
            async for msg in self.user_client.iter_messages(
                chat_id, min_id=last_id, limit=GAP_FILL_BATCH
            ):
                msgs.append(msg)

            if not msgs:
                continue

            msgs.sort(key=lambda m: m.id)
            new_last_id = msgs[-1].id

            # Separate albums from singles
            albums: dict[int, list] = defaultdict(list)
            singles = []
            for msg in msgs:
                if msg.grouped_id:
                    albums[msg.grouped_id].append(msg)
                else:
                    singles.append(msg)

            for grouped_id, album_msgs in albums.items():
                album_msgs.sort(key=lambda m: m.id)
                # If any member already mapped, the album was forwarded — skip
                if any(get_mapping(self.db, chat_id, m.id) for m in album_msgs):
                    continue
                for rule in rules:
                    if not matches_rule(album_msgs[0], rule):
                        continue
                    try:
                        dest_topic = rule['destination'].get('topic_id')
                        reply_to = resolve_reply_to(self.db, album_msgs[0], dest_topic)
                        sent_list = await resend_album(
                            self.user_client, self.bot_client, rule, album_msgs, reply_to=reply_to
                        )
                        dest_id = rule['destination']['chat_id']
                        for src_msg, sent in zip(album_msgs, sent_list):
                            save_mapping(self.db, src_msg.chat_id, src_msg.id, dest_id, sent.id)
                            record_event(self.db, src_msg.chat_id, src_msg.id, 'ok')
                        logger.info(f'[gap_fill] Album {grouped_id} ({len(sent_list)}) → {dest_id}')
                    except Exception as e:
                        logger.error(f'[gap_fill] Album {grouped_id} failed: {e}')

            for msg in singles:
                if get_mapping(self.db, chat_id, msg.id):
                    continue
                for rule in rules:
                    if not matches_rule(msg, rule):
                        continue
                    try:
                        dest_topic = rule['destination'].get('topic_id')
                        reply_to = resolve_reply_to(self.db, msg, dest_topic)
                        sent = await resend_single(
                            self.user_client, self.bot_client, rule, msg, reply_to=reply_to
                        )
                        dest_id = rule['destination']['chat_id']
                        save_mapping(self.db, msg.chat_id, msg.id, dest_id, sent.id)
                        record_event(self.db, msg.chat_id, msg.id, 'ok')
                        logger.info(f'[gap_fill] Msg {msg.id} → {dest_id}')
                    except Exception as e:
                        logger.error(f'[gap_fill] Msg {msg.id} failed: {e}')

            set_scan_state(self.db, chat_id, new_last_id)
            if new_last_id > last_id:
                logger.info(f'[gap_fill] chat {chat_id}: scanned up to msg {new_last_id}')

    async def _flush_album(self, grouped_id: int):
        await asyncio.sleep(ALBUM_FLUSH_DELAY)
        items = self._album_buffer.pop(grouped_id, [])
        self._album_tasks.pop(grouped_id, None)
        if not items:
            return

        # Group by destination rule
        by_rule: dict[int, list] = defaultdict(list)
        for msg, rule in items:
            by_rule[id(rule)].append((msg, rule))

        for _, group in by_rule.items():
            messages = [m for m, _ in group]
            rule = group[0][1]
            try:
                dest_topic = rule['destination'].get('topic_id')
                reply_to = resolve_reply_to(self.db, messages[0], dest_topic)
                sent_list = await resend_album(self.user_client, self.bot_client, rule, messages, reply_to=reply_to)
                dest_id = rule['destination']['chat_id']
                for src_msg, sent in zip(messages, sent_list):
                    save_mapping(self.db, src_msg.chat_id, src_msg.id, dest_id, sent.id)
                    record_event(self.db, src_msg.chat_id, src_msg.id, 'ok')
                logger.info(f'Album ({len(sent_list)} msgs) forwarded → {dest_id}')
            except Exception as e:
                for src_msg, _ in group:
                    record_event(self.db, src_msg.chat_id, src_msg.id, 'error', str(e))
                logger.error(f'Album send failed: {e}')

    async def on_new_message(self, event):
        message = event.message
        rules = self.source_index.get(message.chat_id, [])

        for rule in rules:
            if not matches_rule(message, rule):
                continue

            if message.grouped_id:
                self._album_buffer[message.grouped_id].append((message, rule))
                if message.grouped_id not in self._album_tasks:
                    task = asyncio.create_task(self._flush_album(message.grouped_id))
                    self._album_tasks[message.grouped_id] = task
            else:
                try:
                    dest_topic = rule['destination'].get('topic_id')
                    reply_to = resolve_reply_to(self.db, message, dest_topic)
                    sent = await resend_single(self.user_client, self.bot_client, rule, message, reply_to=reply_to)
                    dest_id = rule['destination']['chat_id']
                    save_mapping(self.db, message.chat_id, message.id, dest_id, sent.id)
                    record_event(self.db, message.chat_id, message.id, 'ok')
                    logger.info(f'Forwarded msg {message.id} from {message.chat_id} → {dest_id}')
                except Exception as e:
                    record_event(self.db, message.chat_id, message.id, 'error', str(e))
                    logger.error(f'Send failed for msg {message.id}: {e}')

    async def on_message_edited(self, event):
        message = event.message
        mapping = get_mapping(self.db, message.chat_id, message.id)
        if not mapping:
            return
        dest_chat_id, dest_msg_id = mapping
        try:
            await self.bot_client.edit_message(
                dest_chat_id,
                dest_msg_id,
                text=message.text or '',
            )
            logger.info(f'Edit synced: {message.id} → {dest_msg_id}')
        except Exception as e:
            logger.error(f'Edit sync failed for msg {message.id}: {e}')

    async def on_message_deleted(self, event):
        deleted_ids = event.deleted_ids
        if not deleted_ids:
            return
        chat_id = event.chat_id
        if not chat_id or chat_id not in self.source_index:
            return
        rows = get_mappings_for_ids(self.db, chat_id, deleted_ids)
        for source_msg_id, dest_chat_id, dest_msg_id in rows:
            try:
                await self.bot_client.delete_messages(dest_chat_id, [dest_msg_id])
                logger.info(f'Delete synced: {source_msg_id} → {dest_msg_id}')
            except Exception as e:
                logger.error(f'Delete sync failed for msg {source_msg_id}: {e}')


async def rpc_server(user_client, start_time, socket_path):
    socket_path = os.path.expanduser(socket_path)
    parent = os.path.dirname(socket_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        os.unlink(socket_path)
    except FileNotFoundError:
        pass

    async def list_dialogs(params):
        archived = params.get('archived', False)
        limit = params.get('limit', 500)
        dialogs = []
        async for dialog in user_client.iter_dialogs(limit=limit, archived=archived):
            e = dialog.entity
            is_forum = getattr(e, 'forum', False)
            if hasattr(e, 'broadcast'):          # Channel
                etype = 'channel'
                can_post = getattr(e, 'creator', False) or getattr(e, 'admin_rights', None) is not None
            elif hasattr(e, 'megagroup'):        # Supergroup/group
                etype = 'group'
                can_post = True
            else:
                etype = 'user'
                can_post = True
            dialogs.append({
                'id': dialog.id,
                'title': dialog.name,
                'type': etype,
                'is_forum': is_forum,
                'can_post': can_post,
            })
        return dialogs

    async def list_topics(params):
        chat_id = params['chat_id']
        result = await user_client(GetForumTopicsRequest(
            peer=chat_id, offset_date=None, offset_id=0, offset_topic=0, limit=100
        ))
        return [
            {'id': t.id, 'title': t.title, 'is_closed': getattr(t, 'closed', False)}
            for t in result.topics
        ]

    async def resolve_entity(params):
        chat_id = params['chat_id']
        e = await user_client.get_entity(chat_id)
        etype = 'channel' if hasattr(e, 'broadcast') else 'group' if hasattr(e, 'megagroup') else 'user'
        return {
            'id': e.id,
            'title': getattr(e, 'title', getattr(e, 'first_name', str(e.id))),
            'type': etype,
        }

    async def handle(reader, writer):
        try:
            line = await reader.readline()
            request = json.loads(line.decode())
            method = request.get('method')
            params = {k: v for k, v in request.items() if k != 'method'}

            if method == 'health':
                result = {'uptime_s': time.monotonic() - start_time, 'version': '1.0.0'}
            elif method == 'list_dialogs':
                result = await list_dialogs(params)
            elif method == 'list_topics':
                result = await list_topics(params)
            elif method == 'resolve_entity':
                result = await resolve_entity(params)
            else:
                result = None
                raise ValueError(f'Unknown method: {method!r}')

            response = json.dumps({'ok': True, 'result': result})
        except Exception as exc:
            response = json.dumps({'ok': False, 'error': str(exc)})

        try:
            writer.write((response + '\n').encode())
            await writer.drain()
        except ConnectionResetError:
            pass
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle, path=socket_path)
    logger.info(f'RPC server listening on {socket_path}')
    async with server:
        await server.serve_forever()


async def main():
    paths.ensure_data_dir()
    config = load_config()
    setup_logging(config)

    tg = config.get('telegram') or {}

    # Always load secrets from the data-dir secrets file when present
    if paths.SECRETS_PATH.exists():
        with open(paths.SECRETS_PATH) as f:
            secrets = yaml.safe_load(f)
        if secrets:
            tg.update(secrets)

    db = init_db()

    user_client = TelegramClient(str(paths.USER_SESSION), tg['api_id'], tg['api_hash'])
    bot_client = TelegramClient(str(paths.BOT_SESSION), tg['api_id'], tg['api_hash'])

    start_time = time.monotonic()
    await user_client.start()
    await bot_client.start(bot_token=tg['bot_token'])
    logger.info('Telegram clients connected (user + bot)')

    forwarder = Forwarder(config, user_client, bot_client, db)
    forwarder.register_handlers()

    socket_path = str(paths.SOCKET_PATH)
    asyncio.ensure_future(rpc_server(user_client, start_time, socket_path))

    await user_client.run_until_disconnected()


if __name__ == '__main__':
    asyncio.run(main())
