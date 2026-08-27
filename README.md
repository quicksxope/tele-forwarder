# tele-forwarder

Self-hosted Telegram message forwarder. Monitors private groups/channels you're a member of and silently re-sends messages to a destination group — no "Forwarded from" banner, no attribution.

**How it works:** Your personal account reads messages (the only way to see private groups you're a member of). A bot you own posts them to the destination. The destination group sees the bot posting, not you.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Getting your api\_id and api\_hash](#getting-your-api_id-and-api_hash)
3. [Quick setup](#quick-setup)
4. [Finding chat IDs](#finding-chat-ids)
5. [Finding topic IDs (forum groups)](#finding-topic-ids-forum-groups)
6. [Writing your first rule](#writing-your-first-rule)
7. [Running the daemon](#running-the-daemon)
8. [Running multiple instances](#running-multiple-instances)
9. [Managing rules with the TUI](#managing-rules-with-the-tui)
10. [Troubleshooting](#troubleshooting)
11. [OKX signal bot](#okx-signal-bot)

---

## OKX signal bot

Optional module under [`okx_bot/`](okx_bot/) — parse DEX VIP-style Telegram signals, trade on OKX via CCXT (demo/live), backtest, and weekly win rate / ROI / avg R reports.

See **[okx_bot/README.md](okx_bot/README.md)** for setup and commands.

---

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A Telegram account
- A Telegram bot (created via [@BotFather](https://t.me/BotFather))
- Your own `api_id` and `api_hash` from Telegram (see next section)
- Docker + docker compose (for production deploy — optional for local dev)

---

## Getting your api\_id and api\_hash

Telegram requires every client app to identify itself with an `api_id` and `api_hash`. You register your own at [my.telegram.org](https://my.telegram.org) — it's free and takes two minutes.

> **Important:** Each person running this forwarder should register their own `api_id`. Do not share yours. Telegram monitors `api_id` usage patterns, and sharing one across multiple unrelated accounts risks getting it banned.

### Steps

1. Go to [my.telegram.org](https://my.telegram.org) and log in with your phone number (it sends an OTP via Telegram, same as login).

2. Click **API development tools**.

3. Fill in the form:
   - **App title**: anything (e.g. `My Forwarder`)
   - **Short name**: anything lowercase, no spaces (e.g. `myforwarder`)
   - **Platform**: Other
   - **Description**: leave blank or add a note

4. Click **Create application**.

5. You'll see your credentials:
   ```
   App api_id:    12345678
   App api_hash:  0123456789abcdef0123456789abcdef
   ```

   Copy both — the setup wizard will ask for them.

> **Keep these secret.** Anyone with your `api_id` + `api_hash` + session file can act as your Telegram account. The wizard stores them in `data/secrets.yaml` (chmod 600).

---

## Quick setup

Before starting, make sure you have:
- Your `api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org) (see [previous section](#getting-your-api_id-and-api_hash))
- A bot token from [@BotFather](https://t.me/BotFather)

```bash
# 1. Clone and install
git clone https://github.com/vrieza/tele-forwarder
cd tele-forwarder
uv sync --extra tui

# 2. Create the data directory and copy the example config
mkdir -p data
cp config.example.yaml data/config.yaml

# 3. Run the setup wizard — it walks you through logging in
uv run python -m tui setup
```

The wizard prompts for your `api_id`, `api_hash`, phone number (OTP), and bot token, then writes `data/secrets.yaml` (chmod 600) and `data/*.session`.

After setup, `data/` contains everything the daemon needs to run non-interactively.

---

## Finding chat IDs

Every Telegram group, channel, and user has a numeric ID. For groups and channels, this is always a **negative integer** (e.g., `-1001234567890`). You need these IDs to write forwarding rules.

### Method 1 — TUI chat picker (easiest, no manual lookup)

Start the daemon first (even briefly), then open the TUI:

```bash
uv run forwarder.py &          # start daemon in background
uv run --extra tui python -m tui
```

Press `2` to go to the **Rules** screen, press `a` to add a rule, then click **Source chat** or **Destination chat**. A picker opens showing all groups you're in, with their IDs displayed inline. Pick one and the ID is filled in automatically.

This is the recommended approach — you never have to look up an ID manually.

---

### Method 2 — Telegram Web URL

Open [web.telegram.org](https://web.telegram.org) and navigate to the group or channel.

Look at the browser URL bar:

```
https://web.telegram.org/a/#-1001234567890
                                ↑
                           this is the chat ID (already negative, use as-is)
```

> **Note:** The URL shows the full peer ID directly. Copy it including the minus sign.

For **channels** the URL format is the same. For **private groups** that don't appear in the URL, use method 3.

---

### Method 3 — @JsonDumpBot (for any chat, including private)

1. Open the source group in Telegram
2. Forward **any message** from that group to [@JsonDumpBot](https://t.me/JsonDumpBot)
3. The bot replies with a JSON dump. Find the `"chat"` object:

```json
{
  "forward_from_chat": {
    "id": -1001234567890,   ← this is the chat ID
    "title": "My Private Group",
    "type": "supergroup"
  }
}
```

Use the value of `"id"` exactly as shown (it's already negative).

> **Why -100 prefix?** Telegram supergroups have a "bare" ID (e.g., `1234567890`) internally, but the MTProto API represents them with `-100` prepended (making it `-1001234567890`). Always use the full negative form.

---

### Method 4 — @getidsbot

1. Add [@getidsbot](https://t.me/getidsbot) to the group temporarily (as a member, not admin)
2. It immediately posts the group's ID in the chat
3. Remove it when done

---

## Finding topic IDs (forum groups)

Forum groups have **topics** (threads). Each topic has its own integer ID. If you want to forward only specific topics, you need these IDs.

### Method 1 — TUI inline topic list (easiest)

In the rule edit form, after selecting a **source chat** that is a forum group, the form fetches that group's topics automatically and shows them as checkboxes. Check the topics you want — their IDs are written to the rule for you.

---

### Method 2 — Telegram Web URL

Open [web.telegram.org](https://web.telegram.org) and navigate to the forum group. Click into a specific topic. The URL changes to:

```
https://web.telegram.org/a/#-1001234567890_789
                                             ↑
                                      this is the topic ID
```

The number after the underscore is the topic ID. Use it as an integer in `topics: [789]`.

> The **General** topic (the default one) has topic ID `1` in most groups.

---

### Method 3 — @JsonDumpBot (for topic messages)

Forward a message **from inside a specific topic** to [@JsonDumpBot](https://t.me/JsonDumpBot). Look for:

```json
{
  "reply_to_message": {
    "message_id": 789,   ← topic ID (for the topic's root message)
    ...
  }
}
```

Or look for `"message_thread_id"` in the forwarded message object — that field directly contains the topic ID.

---

## Writing your first rule

Open `data/config.yaml` and add a rule under `sources:`. Or use the TUI (press `2` → `a`) to do it visually without editing YAML.

### Forward everything from a group

```yaml
sources:
  - name: "My source group"
    chat_id: -1001234567890    # source group ID (negative integer)
    topics: all                # forward from all topics + general chat
    destination:
      chat_id: -1009876543210  # your destination group ID
      topic_id: null           # null = general chat; set to an integer for a specific topic
    filters:
      keywords: []             # empty = forward all messages
      media_types: []          # empty = all types
```

### Forward only specific topics

```yaml
  - name: "Only signals topic"
    chat_id: -1001234567890
    topics: [789, 1024]        # only these topic IDs
    destination:
      chat_id: -1009876543210
      topic_id: null
    filters:
      keywords: []
      media_types: []
```

### Keyword filter (any-match)

```yaml
  - name: "BUY/SELL alerts only"
    chat_id: -1001234567890
    topics: all
    destination:
      chat_id: -1009876543210
      topic_id: null
    filters:
      keywords: ["BUY", "SELL", "ALERT"]   # forward only if message contains any of these
      media_types: []
```

### Media type filter

```yaml
  - name: "Photos and videos only"
    chat_id: -1001234567890
    topics: all
    destination:
      chat_id: -1009876543210
      topic_id: 55             # forward into topic 55 of the destination
    filters:
      keywords: []
      media_types: ["photo", "video"]   # options: text photo video audio document gif
```

> **Bot must be in the destination group.** Add your bot to every destination group and grant it the "Send Messages" permission. It doesn't need admin rights unless the group restricts all members from posting.

---

## Running the daemon

### Local dev (no Docker)

```bash
uv run forwarder.py                        # uses ./data/ by default
uv run forwarder.py --data-dir data/alice  # explicit data dir (multi-instance)
```

Logs go to `data/forwarder.log` and stdout.

### Production (Docker — recommended for a VPS)

Run the setup wizard once on the host (not inside Docker — there's no TTY inside the container for the OTP prompt):

```bash
uv run python -m tui setup   # seeds data/ with sessions + secrets.yaml
```

Then start the container:

```bash
# Store your UID/GID in .env so files in ./data/ are owned by you
echo "UID=$(id -u)" >> .env
echo "GID=$(id -g)" >> .env

docker compose up -d
docker compose logs -f forwarder
```

The daemon auto-restarts on crash or reboot (`restart: unless-stopped`).

---

## Running multiple instances

Each instance is fully isolated: its own Telegram session, secrets, config, database, log, and RPC socket — all under its own data directory.

### Quick start (host, no Docker)

Use the helper script to create and set up a new instance:

```bash
./new-instance.sh alice
```

This creates `data/alice/`, copies the example config, and runs the setup wizard for that instance. Repeat for each additional user.

To start all instances:

```bash
uv run forwarder.py --data-dir data/alice &
uv run forwarder.py --data-dir data/bob   &
```

To open the TUI for a specific instance:

```bash
uv run --extra tui python -m tui --data-dir data/alice
```

### Docker Compose (multi-instance)

Add one service per instance in `docker-compose.yml`:

```yaml
services:
  forwarder-alice:
    build: .
    restart: unless-stopped
    user: "${UID:-1000}:${GID:-1000}"
    volumes:
      - ./data/alice:/data
    environment:
      TELE_FORWARDER_DATA_DIR: /data

  forwarder-bob:
    build: .
    restart: unless-stopped
    user: "${UID:-1000}:${GID:-1000}"
    volumes:
      - ./data/bob:/data
    environment:
      TELE_FORWARDER_DATA_DIR: /data
```

Run the setup wizard on the host first for each instance before starting Docker:

```bash
./new-instance.sh alice   # creates data/alice/ and runs wizard
./new-instance.sh bob     # creates data/bob/ and runs wizard

echo "UID=$(id -u)" >> .env
echo "GID=$(id -g)" >> .env
docker compose up -d
```

The TUI always runs on the host — point it at the right instance with `--data-dir`:

```bash
uv run --extra tui python -m tui --data-dir data/alice
```

### API restrictions on same IP

`FloodWaitError` is per-account, not per-IP — instances don't share rate limit budgets. Running 5–10 instances on one VPS is fine operationally. The one constraint that matters:

> Each instance should use its **own** `api_id`/`api_hash` (registered at [my.telegram.org](https://my.telegram.org) by each user). Sharing one `api_id` across multiple unrelated accounts is a Telegram ToS violation and risks getting the key banned.

---

## Managing rules with the TUI

The TUI runs on the **host**, never inside the container. It connects to the daemon over a Unix socket (`data/rpc.sock`).

```bash
uv run --extra tui python -m tui                        # default ./data/
uv run --extra tui python -m tui --data-dir data/alice  # specific instance
```

### Key bindings

| Key | Action |
|-----|--------|
| `1` | Dashboard (health + stats + live log) |
| `2` | Rules list |
| `q` | Quit |
| **On Rules screen** | |
| `a` | Add new rule |
| `e` | Edit selected rule |
| `d` | Delete selected rule |
| `r` | Refresh list |
| **In chat picker** | |
| Type | Filter the list |
| `r` | Force-refresh dialog list |
| `Esc` | Cancel / close |
| **In rule edit form** | |
| `Ctrl+S` | Save |
| `Esc` | Cancel |

After saving a rule the TUI offers to restart the daemon automatically (using the `restart_cmd` in your config).

---

## Troubleshooting

### "Daemon not running" in the TUI

The daemon isn't started or `data/rpc.sock` doesn't exist. Run `uv run forwarder.py` first (or `docker compose up -d`).

### Messages aren't being forwarded

1. Check `data/forwarder.log` for errors.
2. Confirm the source `chat_id` is negative and correct (use [@JsonDumpBot](#method-3--jsondumpbot-for-any-chat-including-private) to verify).
3. Confirm the bot is a member of the destination group with "Send Messages" permission.
4. If using topic filters, confirm the topic IDs are correct — a wrong topic ID silently drops messages.

### "FloodWaitError" in the log

Telegram is rate-limiting your account. The daemon will retry automatically. If this is frequent, reduce the number of forwarded messages or add keyword filters to be more selective.

### Bot can't post to the destination

- The bot is not a member of the destination group → add it.
- The group only allows admins to post → make the bot an admin (only needs "Post Messages" right, not full admin).
- The destination is a private channel → bots can post to channels only if added as an admin.

### macOS + Docker: TUI can't reach daemon

Docker Desktop on macOS runs containers in a VM. Unix sockets created inside the container in a bind-mounted directory often can't be reached from the Mac host due to VirtioFS limitations. Run the daemon natively (`uv run forwarder.py`) for local Mac dev and use Docker only on a Linux VPS.

### Chat IDs look wrong after copy-pasting

Make sure you're using the full negative ID including the `-` sign and the `100` prefix (e.g., `-1001234567890`, not `1234567890`). The 100 prefix is always present for supergroups and channels.
