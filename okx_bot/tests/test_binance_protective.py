"""Binance entry vs protective order behavior."""
from __future__ import annotations

from okx_bot.bot import _sl_breached
from okx_bot.parser import Signal
from okx_bot.trader import BinanceTrader, _binance_exit_trigger_valid


def test_binance_entry_params_omit_ccxt_bracket_keys() -> None:
    trader = BinanceTrader(
        api_key="x",
        secret="y",
        dry_run=True,
    )
    sig = Signal(
        pair="XMR/USDT",
        side="sell",
        entry=515.65,
        raw_pair="XMR/USDT",
        take_profit=509.24,
        stop_loss=522.06,
    )
    params = trader._params(sig)
    assert "takeProfitPrice" not in params
    assert "stopLossPrice" not in params


def test_binance_short_sl_tp_vs_mark() -> None:
    mark = 515.0
    assert _binance_exit_trigger_valid(
        entry_side="sell", mark=mark, trigger=522.06, kind="sl"
    )
    assert _binance_exit_trigger_valid(
        entry_side="sell", mark=mark, trigger=509.24, kind="tp"
    )
    assert not _binance_exit_trigger_valid(
        entry_side="sell", mark=523.0, trigger=522.06, kind="sl"
    )


def test_sl_breached_long_short() -> None:
    assert _sl_breached(side="buy", mark=0.10, stop_loss=0.102)
    assert not _sl_breached(side="buy", mark=0.103, stop_loss=0.102)
    assert _sl_breached(side="sell", mark=522.0, stop_loss=520.0)
    assert not _sl_breached(side="sell", mark=515.0, stop_loss=520.0)


def test_protective_success_note_filter() -> None:
    """'not placed' must not count as success (substring 'placed')."""
    note = 'TP/SL not placed (SL(binance {"code":-4005}))'
    note_l = note.lower()
    ok = "already open" in note_l or (
        "placed" in note_l and "not placed" not in note_l
    )
    assert not ok
    assert "placed" in "TP/SL: SL(all), TP placed".lower() and "not placed" not in "TP/SL: SL(all), TP placed".lower()


def _wire(trader: BinanceTrader, *, market_max: float, lot_max: float, mark: float, algos: list):
    symbol = "BANANA/USDT:USDT"
    market = {
        "id": "BANANAUSDT",
        "symbol": symbol,
        "spot": False,
        "swap": True,
        "linear": True,
        "contract": True,
        "precision": {"amount": 0.1, "price": 0.001},
        "limits": {
            "amount": {"min": 0.1, "max": lot_max},
            "market": {"min": 0.1, "max": market_max},
            "cost": {"min": 5},
        },
    }
    ex = trader.exchange
    ex.markets = {symbol: market}
    ex.markets_by_id = {"BANANAUSDT": market}
    orders: list[tuple] = []

    def create_order(sym, typ, side, amount, price, params):
        orders.append((typ, amount, params))
        return {"id": str(len(orders))}

    ex.create_order = create_order
    ex.fetch_ticker = lambda sym: {"mark": mark}
    ex.fetch_open_orders = lambda sym: []
    ex.fapiPrivateGetOpenAlgoOrders = lambda params: algos
    ex.amount_to_precision = lambda sym, amount: float(amount)
    ex.price_to_precision = lambda sym, price: float(price)
    return orders


def _sig() -> Signal:
    return Signal(
        pair="BANANA/USDT",
        side="buy",
        entry=4.229,
        raw_pair="BANANA/USDT",
        take_profit=4.257,
        stop_loss=4.201,
    )


def test_tp_splits_on_market_lot_not_limit_lot() -> None:
    trader = BinanceTrader(api_key="x", secret="y", dry_run=True)
    orders = _wire(trader, market_max=10_000, lot_max=1_000_000, mark=4.23, algos=[])
    note = trader._place_binance_protective_orders("BANANA/USDT:USDT", _sig(), 25_000)
    tp = [amount for typ, amount, _ in orders if typ == "TAKE_PROFIT_MARKET"]
    sl = [params for typ, _, params in orders if typ == "STOP_MARKET"]
    assert tp == [10_000, 10_000, 5_000]
    assert sl and sl[0].get("closePosition") is True
    assert "TP(3)" in note


def test_past_tp_closes_uncovered_qty_even_when_sl_exists() -> None:
    trader = BinanceTrader(api_key="x", secret="y", dry_run=True)
    orders = _wire(
        trader,
        market_max=1_500,
        lot_max=100_000,
        mark=4.40,
        algos=[
            {
                "algoId": "1",
                "orderType": "STOP_MARKET",
                "quantity": "0",
                "closePosition": True,
            }
        ],
    )
    note = trader._place_binance_protective_orders("BANANA/USDT:USDT", _sig(), 3_300)
    assert "closed market" in note
    assert [amount for typ, amount, _ in orders if typ == "market"] == [1_500, 1_500, 300]
    assert not any(typ == "TAKE_PROFIT_MARKET" for typ, _, _ in orders)


def test_partial_tp_is_topped_up_to_position() -> None:
    trader = BinanceTrader(api_key="x", secret="y", dry_run=True)
    orders = _wire(
        trader,
        market_max=1_500,
        lot_max=100_000,
        mark=4.24,
        algos=[
            {
                "algoId": "sl",
                "orderType": "STOP_MARKET",
                "quantity": "0",
                "closePosition": True,
            },
            {
                "algoId": "tp",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "400",
                "closePosition": False,
            },
        ],
    )
    note = trader._place_binance_protective_orders("BANANA/USDT:USDT", _sig(), 3_300)
    assert [amount for typ, amount, _ in orders if typ == "TAKE_PROFIT_MARKET"] == [1_500, 1_400]
    assert not any(typ == "STOP_MARKET" for typ, _, _ in orders)
    assert "TP" in note


def test_full_coverage_does_not_add_orders() -> None:
    trader = BinanceTrader(api_key="x", secret="y", dry_run=True)
    orders = _wire(
        trader,
        market_max=1_500,
        lot_max=100_000,
        mark=4.24,
        algos=[
            {
                "algoId": "sl",
                "orderType": "STOP_MARKET",
                "quantity": "0",
                "closePosition": True,
            },
            {
                "algoId": "tp",
                "orderType": "TAKE_PROFIT_MARKET",
                "quantity": "3300",
                "closePosition": False,
            },
        ],
    )
    note = trader._place_binance_protective_orders("BANANA/USDT:USDT", _sig(), 3_300)
    assert orders == []
    assert "already open" in note


def test_clamp_amount_respects_max() -> None:
    trader = BinanceTrader(api_key="x", secret="y", dry_run=True)
    trader.exchange.markets = {
        "ACE/USDT:USDT": {
            "symbol": "ACE/USDT:USDT",
            "spot": False,
            "swap": True,
            "linear": True,
            "precision": {"amount": 1.0},
            "limits": {"amount": {"min": 1.0, "max": 100.0}},
        }
    }
    trader.exchange.markets_by_id = {}
    trader.exchange.precisionMode = 4
    assert trader._clamp_amount("ACE/USDT:USDT", 137.0) == 100.0
