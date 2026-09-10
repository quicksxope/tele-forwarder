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


CRYPTOCIUM = """
☄️ SETUP - SHORT

📊 Pair : $VVV

⌛ Time frame : 1h

🔔 Entry : 16.700

🎯 Target : on chart

❌ Stop loss : 17.200
"""

CRYPTOCIUM_SWING = """
☄️ SWING SETUP - Short/sell

🔘 Pair : $ADA

🔘 Time frame : 30m

🟡 Entry limit : 0.2143

🔘 Target : di chart

🔴 Stop loss : 0.2233

🔖 ENTRY REASON : Supply, EMA, Bearish Structure

📌 Risk Adjustment :
Max Loss / Risk Per Trade 1% of Total Trading Balance
"""


def test_cryptocium_2r_short():
    s = parse_signal(CRYPTOCIUM, parser="cryptocium")
    assert s is not None
    assert s.pair == "VVV/USDT"
    assert s.side == "sell"
    assert s.entry == 16.7
    assert s.stop_loss == 17.2
    assert s.take_profit == 15.7
    assert s.leverage == 10
    assert s.window_start is None
    assert s.window_end is None
    assert s.timeframe_raw == "1h"


def test_cryptocium_swing_entry_limit():
    s = parse_signal(CRYPTOCIUM_SWING, parser="cryptocium")
    assert s is not None
    assert s.pair == "ADA/USDT"
    assert s.side == "sell"
    assert s.entry == 0.2143
    assert s.stop_loss == 0.2233
    # 2R: entry - 2*(0.2233-0.2143) = 0.1963
    assert abs(s.take_profit - 0.1963) < 1e-9
    assert s.leverage == 10
    assert s.window_start is None
    assert s.window_end is None
    assert s.timeframe_raw == "30m"


if __name__ == "__main__":
    test_legacy_confirm_block()
    test_dex_vip_format()
    test_cryptocium_2r_short()
    test_cryptocium_swing_entry_limit()
    print("OK")
