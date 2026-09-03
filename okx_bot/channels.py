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

    def parse(self, text: str) -> Signal | None:
        return get_parser(self.parser)(text)


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

    return ChannelProfile(
        key=key,
        name=str(raw.get("name") or key),
        chat_id=int(chat_id),
        parser=str(raw.get("parser") or "dex_vip"),
        enabled=bool(raw.get("enabled", True)),
    )


def parse_for_active_channel(text: str, env: dict[str, str] | None = None) -> Signal | None:
    return get_active_channel(env).parse(text)
