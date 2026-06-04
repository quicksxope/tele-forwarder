# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

A self-hosted Telegram message forwarder. It monitors private groups/channels the user is a member of and re-sends messages to a destination group — without the "Forwarded from" banner. Runs as a headless daemon (Docker or systemd) on a VPS.

**Dual-client model:**
- `user_client` — personal Telegram account (Telethon `TelegramClient`). Reads events from source groups. The only client that can see private groups without admin rights.
- `bot_client` — bot token (same `api_id`/`api_hash`). Sends all outbound messages to destination groups. Destination sees the bot posting, not the personal account.

## Dependency management

Uses **uv** with `pyproject.toml` + `uv.lock`.

```bash
uv sync              # daemon-only dependencies
uv sync --extra tui  # daemon + TUI (textual, ruamel.yaml)
```

## Running the daemon

### Local dev (host, no Docker)

```bash
uv sync --extra tui
mkdir -p data
cp config.example.yaml data/config.yaml   # fill in rules (secrets go via wizard)
uv run python -m tui setup                # first-time login: prompts phone + OTP, writes data/secrets.yaml and *.session
                                          # (no --extra tui needed — wizard uses only base deps)
uv run forwarder.py                       # subsequent runs: non-interactive
```

### Production (Docker — preferred)

Run the setup wizard once on the host to seed `./data/`:

```bash
uv sync --extra tui
mkdir -p data && cp config.example.yaml data/config.yaml
uv run python -m tui setup
```

Then start the daemon in Docker:

```bash
# Easiest: put UID/GID in .env so compose picks them up automatically
echo "UID=$(id -u)" >> .env
echo "GID=$(id -g)" >> .env

docker compose up -d
docker compose logs -f forwarder
```

The TUI always runs on the host (never inside the container):

```bash
uv run --extra tui python -m tui
```

## Data directory layout

All runtime state lives in `./data/` (gitignored). Inside Docker this is bind-mounted to `/data`.

```
data/
├── config.yaml          # forwarding rules (edit via TUI or by hand)
├── secrets.yaml         # api_id, api_hash, bot_token (chmod 600)
├── forwarder.session    # Telethon session for personal account
├── bot.session          # Telethon session for bot
├── mappings.db          # SQLite: message_map + scan_state + forward_events (WAL mode)
├── forwarder.log        # rotating log (5 MB × 3 files)
└── rpc.sock             # Unix socket for TUI ↔ daemon RPC
```

The path root is controlled by `TELE_FORWARDER_DATA_DIR` (default: `./data`) or the `--data-dir` CLI flag (takes precedence). All paths are derived in `paths.py` — never hardcode paths elsewhere.

> **macOS Docker Desktop**: bind-mounted Unix sockets may not be reachable from the host due to VirtioFS limitations. Run the daemon natively (`uv run forwarder.py`) for local Mac dev; use Docker only on a Linux VPS.

## TUI key bindings

App-level (work from any screen):

| Key | Action |
|-----|--------|
| `1` | Switch to Dashboard |
| `2` | Switch to Rules list |
| `q` | Quit |

Screen-local bindings (`r` and `d` are intentionally reserved for screen actions, which is why app navigation uses numbers):

| Screen | Key | Action |
|--------|-----|--------|
| Dashboard | `r` | Refresh stats |
| Rules | `a` | Add rule |
| Rules | `e` | Edit selected rule |
| Rules | `d` | Delete selected rule |
| Rules | `r` | Refresh list |
| Chat picker modal | `r` | Force-refresh dialog list |
| Any modal | `Esc` | Dismiss |
| Rule edit form | `Ctrl+S` | Save |
| Rule edit form | `Esc` | Cancel |

## After making changes

- **Any change to `forwarder.py`** — always restart the daemon immediately:
  ```bash
  # Default data dir
  pkill -f "forwarder.py"; sleep 1 && source $HOME/.local/bin/env && uv run forwarder.py &>> data/forwarder.log &

  # Named instance
  pkill -f "data/alice"; sleep 1 && uv run forwarder.py --data-dir data/alice &>> data/alice/forwarder.log &
  ```
- **TUI changes** (`tui/`) — no restart needed; just re-launch the TUI.

## Multiple instances

Each instance is isolated by its data directory. Provision a new one with:

```bash
./new-instance.sh alice        # creates data/alice/, copies config, runs wizard
uv run forwarder.py --data-dir data/alice &
uv run --extra tui python -m tui --data-dir data/alice
```

The `--data-dir` flag sets `TELE_FORWARDER_DATA_DIR` before `paths.py` is imported, so it takes precedence over the env var. Both forms work; prefer `--data-dir` for clarity.

## Known API compatibility (Telethon 1.43.2 + Textual 8.x)

These were broken in the original code and have been fixed:

| Issue | Fix |
|-------|-----|
| `GetForumTopicsRequest` moved | Import from `telethon.tl.functions.messages`, not `channels`. Parameter is `peer=`, not `channel=`. `offset_date=None` not `0`. |
| Textual `ListView.Selected` index | Use `event.index` (on the event), not `event.list_view.index` (reactive, can be `None`). |
| Textual modal return values | Use `self.dismiss(result)` in the modal + `app.push_screen(modal, callback)` in the caller. `post_message` from a `ModalScreen` does not reach the screen that opened it. |
| Textual app-level key bindings | Wrap in `Binding(..., priority=True)` so `DataTable` and other focusable widgets don't swallow `q`, `1`, `2`. |
| `message.caption` does not exist | Telethon 1.43.2 has no `.caption` attribute. Use `message.message` (plain text) instead. `message.text` returns markdown-formatted text (`**bold**`) which shows as literal asterisks unless parse_mode is set. |
| Media cross-client reference | Never pass `message.media` directly to `bot_client.send_file()`. File references are tied to the uploading client. Always download to a temp file with the correct extension, then re-upload. |
| Named temp files for photos | Use `tempfile.mkstemp(suffix='.jpg')` for photos. A suffix-less temp file loses mime type and arrives as "unnamed file". |
| Telegram General topic (id=1) | Messages in General topic have `reply_to=None` (no thread marker). `get_message_topic()` returns `1` when `reply_to` is None. |
| Album messages sent individually | Albums share `grouped_id`. When resending manually, group by `grouped_id` first and call `resend_album(messages)`. Never send album members one by one — they arrive as separate unrelated images. Only one message in an album carries the caption; the others have empty `message`. |
| Daemon + resend script session conflict | Two Telethon processes cannot share the same `.session` file safely. Stop the daemon before running any standalone resend/test script. |

## Testing

Use Textual's headless test runner for TUI smoke tests — no real terminal needed:

```python
async with app.run_test(headless=True) as pilot:
    await pilot.pause(1.0)          # let on_mount workers settle
    await pilot.press('2')          # navigate to rules
    screen = app.screen             # app.screen is the active screen
    table = screen.query_one('#rules-table', DataTable)
```

Unit tests for pure logic (config_io, stats) use `tempfile.TemporaryDirectory` with a real SQLite DB — no mocking.

## Key architectural facts

**Re-sending, not forwarding.** `resend_single` and `resend_album` use `client.send_message()` / `client.send_file()`, not `client.forward_messages()`. This strips the attribution header. The tradeoff: re-uploaded media counts against the account's upload quota.

**Album buffering.** Telegram fires one `NewMessage` event per photo in a multi-photo album. They share a `grouped_id`. The forwarder buffers them for `ALBUM_FLUSH_DELAY` (0.8s) and sends them together via `send_file(file=[...])`. Albums sent as single messages look native in the destination.

**Edit/delete mirroring.** `mappings.db` maps `(source_chat_id, source_msg_id)` → `(dest_chat_id, dest_msg_id)`. On `MessageEdited` / `MessageDeleted` events, the forwarder looks up the destination message and edits/deletes it. Delete events don't always carry `chat_id` — the handler guards against `None`.

**Topic support.** Forum topic thread IDs come from `message.reply_to.reply_to_top_id` (replies within a topic) or `message.reply_to.reply_to_msg_id` (topic starter). `get_message_topic()` in `forwarder.py` handles this. `topics: all` in config bypasses the check entirely.

**Source index.** At startup `build_source_index(config)` builds a `chat_id → [rules]` dict. The event handlers are registered with `chats=source_ids` so Telethon only calls them for monitored chats.

**Rule identity.** Each rule has a `uuid` field assigned on creation by the TUI (`config_io.ensure_rule_uuid`). The daemon tolerates rules without a uuid. The TUI uses uuid for stable identity across edits and hand-reorders of `config.yaml`.

**RPC protocol.** JSON lines over a Unix socket. Daemon-side handler in `forwarder.py:rpc_server()`. TUI-side in `tui/rpc_client.py`. Methods: `health`, `list_dialogs`, `list_topics`, `resolve_entity`, `stats`. Params are flat kwargs (no nested `params` key). `stats` returns `forwarded_today`, `total_forwarded`, `permanent_failures`, `last_gap_fill_ago_s`, `last_gap_fill_count`.

**Gap filler.** On startup (after 10 s) and every `GAP_FILL_INTERVAL` (300 s), `gap_filler()` iterates each source chat from `scan_state.last_scanned_msg_id` and re-sends any messages that weren't live-forwarded. Useful after daemon downtime. After a successful run it DMs the owner (if `owner_id` is set).

**Retry logic.** Each send attempt is recorded in `forward_events`. If `status='error'` accumulates ≥ `MAX_RETRIES` (3) with no success for a given message, the message is flagged as a permanent failure, counted in `Forwarder._permanent_failures`, and the owner is notified. Permanent failures are surfaced on the dashboard.

**Config hot-reload.** `config_watcher()` polls `config.yaml` every 30 s. On mtime change it reloads the config, rebuilds `source_index`, and re-registers event handlers — no daemon restart needed for rule changes.

**DB housekeeping.** `db_housekeeping()` purges `forward_events` rows older than 30 days, running once per day.

**Media download.** `_download(user_client, message)` saves media to a temp file (with the correct extension, e.g. `.jpg`) before the bot re-uploads it. This is required because Telethon file references are bound to the client that received them.

## Config shape

`data/config.yaml` mirrors `config.example.yaml`. Critical fields per rule:
- `chat_id` — negative integer (groups start with `-100`)
- `source_title` — display name stored by TUI chat picker; daemon ignores it
- `topics` — `'all'` or list of integer thread IDs
- `destination.chat_id` / `destination.topic_id`
- `destination.title` — display name stored by TUI chat picker; daemon ignores it
- `filters.keywords` — empty list = pass all; non-empty = any-match required
- `filters.media_types` — empty list = pass all; options: `text photo video audio document gif`

Top-level keys:
- `restart_cmd` — TUI offers to run this after saving a rule (only if set)
- `owner_id` — your personal Telegram user ID; daemon DMs you on gap-fill completion and permanent failures

## Deployment (systemd — alternative to Docker)

Edit `tele-forwarder.service`: set `User=`, `WorkingDirectory=`, `TELE_FORWARDER_DATA_DIR=`, then:

```bash
sudo cp tele-forwarder.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tele-forwarder
sudo journalctl -u tele-forwarder -f
```
