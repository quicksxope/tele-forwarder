"""Channel profiles — switch signal source without code changes."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .formats import get_parser
from .parser import Signal

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("TELE_FORWARDER_DATA_DIR", ROOT.parent / "data")).resolve()


@dataclass(frozen=True)
class ChannelProfile:
    key: str
    name: str
    chat_id: int
    parser: str
    enabled: bool = True
    topic_id: int | None = None  # forum topic; None = entire chat
    parse_only: bool = False  # DM parse result, skip exchange orders

    def parse(self, text: str) -> Signal | None:
        return get_parser(self.parser)(text)

    def matches(self, chat_id: int, topic_id: int | None) -> bool:
        if int(chat_id) != self.chat_id:
            return False
        if self.topic_id is None:
            return True
        return topic_id == self.topic_id


def _config_paths() -> list[Path]:
    return [
        DATA / "channels.yaml",
        ROOT / "channels.yaml",
        ROOT / "channels.example.yaml",
    ]


def load_channels_config() -> dict[str, Any]:
    for path in _config_paths():
        if path.exists():
            with open(path) as f:
                data = yaml.safe_load(f) or {}
            data["_config_path"] = str(path)
            return data
    raise FileNotFoundError(
        "No channels.yaml found. Copy okx_bot/channels.example.yaml → "
        "okx_bot/channels.yaml (or data/channels.yaml) and edit."
    )


def get_active_channel(env: dict[str, str] | None = None) -> ChannelProfile:
    """Resolve active channel from ACTIVE_CHANNEL env or channels.yaml `active`."""
    env = env or {}
    cfg = load_channels_config()
    key = (
        env.get("ACTIVE_CHANNEL")
        or os.environ.get("ACTIVE_CHANNEL")
        or cfg.get("active")
        or "dex_vip"
    )
    channels = cfg.get("channels") or {}
    if key not in channels:
        known = ", ".join(sorted(channels)) or "(none)"
        raise KeyError(f"Channel {key!r} not in config. Known: {known}")

    raw = channels[key] or {}
    chat_id = raw.get("chat_id")
    # Optional override
    if env.get("SIGNAL_CHAT_ID") or os.environ.get("SIGNAL_CHAT_ID"):
        chat_id = int(env.get("SIGNAL_CHAT_ID") or os.environ["SIGNAL_CHAT_ID"])
    if chat_id is None:
        raise ValueError(f"Channel {key!r} missing chat_id")

    topic_raw = raw.get("topic_id")
    topic_id = int(topic_raw) if topic_raw not in (None, "", "null") else None

    return ChannelProfile(
        key=key,
        name=str(raw.get("name") or key),
        chat_id=int(chat_id),
        parser=str(raw.get("parser") or "dex_vip"),
        enabled=bool(raw.get("enabled", True)),
        topic_id=topic_id,
        parse_only=bool(raw.get("parse_only", False)),
    )


def _profile_from_raw(key: str, raw: dict) -> ChannelProfile:
    chat_id = raw.get("chat_id")
    if chat_id is None:
        raise ValueError(f"Channel {key!r} missing chat_id")
    topic_raw = raw.get("topic_id")
    topic_id = int(topic_raw) if topic_raw not in (None, "", "null") else None
    return ChannelProfile(
        key=key,
        name=str(raw.get("name") or key),
        chat_id=int(chat_id),
        parser=str(raw.get("parser") or "dex_vip"),
        enabled=bool(raw.get("enabled", True)),
        topic_id=topic_id,
        parse_only=bool(raw.get("parse_only", False)),
    )


def list_enabled_channels(env: dict[str, str] | None = None) -> list[ChannelProfile]:
    """All enabled channel profiles (dex_vip + cryptocium, etc.)."""
    _ = env
    cfg = load_channels_config()
    out: list[ChannelProfile] = []
    for key, raw in (cfg.get("channels") or {}).items():
        raw = raw or {}
        if not bool(raw.get("enabled", True)):
            continue
        try:
            out.append(_profile_from_raw(key, raw))
        except (TypeError, ValueError):
            continue
    return out


def match_channel(
    channels: list[ChannelProfile], chat_id: int, topic_id: int | None
) -> ChannelProfile | None:
    """Prefer a topic-specific profile over a whole-chat profile."""
    hits = [c for c in channels if c.matches(chat_id, topic_id)]
    if not hits:
        return None
    topic_hits = [c for c in hits if c.topic_id is not None]
    return topic_hits[0] if topic_hits else hits[0]


def parse_for_active_channel(text: str, env: dict[str, str] | None = None) -> Signal | None:
    return get_active_channel(env).parse(text)
