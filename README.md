# tele-forwarder

Self-hosted Telegram message forwarder. Monitors private groups/channels you're a member of and silently re-sends messages to a destination group — no "Forwarded from" banner, no attribution.

**How it works:** Your personal account reads messages (the only way to see private groups you're a member of). A bot you own posts them to the destination. The destination group sees the bot posting, not you.

---

## Table of contents

1. [Prerequisites](#prerequisites)
2. [Quick setup](#quick-setup)
3. [Finding chat IDs](#finding-chat-ids)
4. [Finding topic IDs (forum groups)](#finding-topic-ids-forum-groups)
5. [Writing your first rule](#writing-your-first-rule)
6. [Running the daemon](#running-the-daemon)
7. [Managing rules with the TUI](#managing-rules-with-the-tui)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- A Telegram account
- A Telegram bot (created via [@BotFather](https://t.me/BotFather))
- Docker + docker compose (for production deploy — optional for local dev)

---

## Quick setup

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

The wizard does two things:
- Logs you into your **personal account** (prompts for phone number + OTP, saves `data/forwarder.session`)
- Validates your **bot token** and saves `data/secrets.yaml` (chmod 600)

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

### Method 1 — TUI topic picker (easiest)

In the rule edit form, after selecting a **source chat** that is a forum group, the **Pick topic** button becomes active. Click it to see a list of all topics with their IDs. Selecting one adds its ID to the topics field automatically.

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
uv run forwarder.py
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

## Managing rules with the TUI

The TUI runs on the **host**, never inside the container. It connects to the daemon over a Unix socket (`data/rpc.sock`).

```bash
uv run --extra tui python -m tui
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
| **In pickers** | |
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
