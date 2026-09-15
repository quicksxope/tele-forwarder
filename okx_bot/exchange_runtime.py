"""Persist which exchanges are active for live orders (Telegram toggles)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_EXCHANGES = ("okx", "bybit", "binance")


def runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / "okx_bot_runtime.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_enabled_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    for part in raw.replace(" ", "").split(","):
        ex = part.lower().strip()
        if ex in SUPPORTED_EXCHANGES and ex not in out:
            out.append(ex)
    return out


def default_enabled_from_env(cfg: dict) -> dict[str, bool]:
    """Initial toggle state when no runtime file exists yet."""
    listed = _parse_enabled_list(cfg.get("ENABLED_EXCHANGES"))
    if listed:
        return {ex: ex in listed for ex in SUPPORTED_EXCHANGES}

    # Infer from env API keys (multi-exchange friendly).
    state = {ex: False for ex in SUPPORTED_EXCHANGES}
    if cfg.get("OKX_API_KEY") and cfg.get("OKX_SECRET") and cfg.get("OKX_PASSWORD"):
        state["okx"] = True
    if cfg.get("BYBIT_API_KEY") and cfg.get("BYBIT_SECRET"):
        state["bybit"] = True
    if cfg.get("BINANCE_API_KEY") and cfg.get("BINANCE_SECRET"):
        state["binance"] = True

    if any(state.values()):
        return state

    # Legacy single EXCHANGE=…
    legacy = (cfg.get("EXCHANGE") or "okx").lower().strip()
    if legacy in SUPPORTED_EXCHANGES:
        state[legacy] = True
    return state


def load_enabled(path: Path, *, cfg: dict | None = None) -> dict[str, bool]:
    cfg = cfg or {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
            enabled = data.get("enabled") or {}
            return {
                ex: bool(enabled.get(ex, False)) for ex in SUPPORTED_EXCHANGES
            }
        except Exception:
            logger.exception("Invalid runtime file %s", path)
    return default_enabled_from_env(cfg)


def save_enabled(path: Path, enabled: dict[str, bool], *, owner_id: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "enabled": {ex: bool(enabled.get(ex, False)) for ex in SUPPORTED_EXCHANGES},
        "updated_at": _utc_now(),
    }
    if owner_id is not None:
        payload["owner_id"] = owner_id
    path.write_text(json.dumps(payload, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def toggle_exchange(path: Path, exchange: str, *, cfg: dict, owner_id: int) -> dict[str, bool]:
    ex = exchange.lower().strip()
    if ex not in SUPPORTED_EXCHANGES:
        raise ValueError(f"Unknown exchange {exchange!r}")
    state = load_enabled(path, cfg=cfg)
    state[ex] = not state[ex]
    save_enabled(path, state, owner_id=owner_id)
    return state


def enabled_exchange_names(path: Path, *, cfg: dict) -> list[str]:
    state = load_enabled(path, cfg=cfg)
    return [ex for ex in SUPPORTED_EXCHANGES if state.get(ex)]


def format_enabled_line(path: Path, *, cfg: dict) -> str:
    state = load_enabled(path, cfg=cfg)
    parts = []
    for ex in SUPPORTED_EXCHANGES:
        mark = "🟢" if state.get(ex) else "⚫"
        parts.append(f"{mark} {ex.upper()}")
    return "Venue aktif: " + " · ".join(parts)
