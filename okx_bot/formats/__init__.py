"""Registered signal text formats (parsers). Lazy-loaded to avoid circular imports."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..parser import Signal

ParseFn = Callable[[str], "Signal | None"]

_PARSERS: dict[str, ParseFn] | None = None


def _load() -> dict[str, ParseFn]:
    global _PARSERS
    if _PARSERS is None:
        from . import dex_vip, okx_confirm

        _PARSERS = {
            "dex_vip": dex_vip.parse,
            "okx_confirm": okx_confirm.parse,
        }
    return _PARSERS


def list_parsers() -> list[str]:
    return sorted(_load())


def get_parser(name: str) -> ParseFn:
    parsers = _load()
    if name not in parsers:
        known = ", ".join(sorted(parsers))
        raise KeyError(f"Unknown parser {name!r}. Available: {known}")
    return parsers[name]
