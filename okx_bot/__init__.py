"""Signal parser bot package (OKX / Bybit via CCXT)."""
from .parser import Signal, parse_signal
from .trader import BybitTrader, OkxTrader, make_trader
from .metrics import PeriodMetrics, compute_metrics
from .trade_store import TradeStore
from .backtest import Backtester, BacktestConfig

__all__ = [
    "Signal",
    "parse_signal",
    "OkxTrader",
    "BybitTrader",
    "make_trader",
    "PeriodMetrics",
    "compute_metrics",
    "TradeStore",
    "Backtester",
    "BacktestConfig",
]
