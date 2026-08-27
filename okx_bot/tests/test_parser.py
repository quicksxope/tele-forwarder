"""Parser self-test."""
from okx_bot.parser import parse_signal

SAMPLE = """
📥 Sinyal diterima
Pair: ETH/USDT
Side: buy
Entry: 2983.0


✅ Order berhasil dipasang!
Pair: ETH-USDT-SWAP
Side: buy
Entry: 2983.0
Order ID: 3107827300305575936
⏱ Berlaku sampai (UTC): 2025-12-07 19:55:00+00:00
"""

DEX = """Pair: GTC/USDT
Position: 🟢 Long
Entry Price: 0.09086
Leverage: 10x
Take Profit: 0.09224
Stop Loss: 0.08948
Timeframe: 15:07-19:07 WIB
"""


def test_legacy_confirm_block():
    s = parse_signal(SAMPLE, parser="okx_confirm")
    assert s is not None
    assert s.pair == "ETH/USDT"
    assert s.side == "buy"
    assert s.entry == 2983.0


def test_dex_vip_format():
    s = parse_signal(DEX, parser="dex_vip")
    assert s is not None
    assert s.pair == "GTC/USDT"
    assert s.side == "buy"
    assert s.entry == 0.09086
    assert s.leverage == 10
    assert s.take_profit == 0.09224
    assert s.stop_loss == 0.08948
    assert s.swap_symbol == "GTC/USDT:USDT"
    assert s.timeframe_raw is not None


if __name__ == "__main__":
    test_legacy_confirm_block()
    test_dex_vip_format()
    print("OK")
