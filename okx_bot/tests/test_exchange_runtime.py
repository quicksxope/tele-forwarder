from pathlib import Path

from okx_bot.exchange_runtime import (
    default_enabled_from_env,
    enabled_exchange_names,
    load_enabled,
    save_enabled,
    toggle_exchange,
)


def test_default_from_env_both_keys(tmp_path: Path) -> None:
    cfg = {
        "OKX_API_KEY": "a",
        "OKX_SECRET": "b",
        "OKX_PASSWORD": "c",
        "BINANCE_API_KEY": "x",
        "BINANCE_SECRET": "y",
    }
    state = default_enabled_from_env(cfg)
    assert state["okx"] is True
    assert state["binance"] is True
    assert state["bybit"] is False


def test_toggle_persists(tmp_path: Path) -> None:
    path = tmp_path / "okx_bot_runtime.json"
    cfg = {"ENABLED_EXCHANGES": "okx,binance"}
    save_enabled(path, default_enabled_from_env(cfg), owner_id=1)
    state = toggle_exchange(path, "okx", cfg=cfg, owner_id=1)
    assert state["okx"] is False
    assert state["binance"] is True
    loaded = load_enabled(path, cfg=cfg)
    assert loaded == state
    assert enabled_exchange_names(path, cfg=cfg) == ["binance"]
