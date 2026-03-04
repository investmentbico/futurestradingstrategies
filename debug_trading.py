#!/usr/bin/env python3
"""
Debug script to check why no trades are being executed
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from mnq_live_bot import MNQ1MinBot, STRATEGY_PARAMS
import numpy as np

def debug_indicators():
    """Debug indicator calculations"""

    # Create a bot instance
    bot = MNQ1MinBot(demo=True, max_trades=10)

    # Simulate some price data with variation
    prices = [18000, 18005, 18002, 18008, 18003, 18010, 18007, 18012, 18009, 18015]

    print("🔍 DEBUGGING INDICATOR CALCULATIONS")
    print("=" * 50)

    # Add price data
    for i, price in enumerate(prices):
        # Create realistic OHLC bars
        base_price = price
        high = base_price + np.random.uniform(2, 8)  # Add some range
        low = base_price - np.random.uniform(2, 8)
        open_price = base_price + np.random.uniform(-3, 3)

        ohlc_bar = {
            'timestamp': f'2026-03-04 09:{i:02d}:00',
            'open': open_price,
            'high': max(open_price, high),
            'low': min(open_price, low),
            'close': base_price
        }

        bot.price_data.append(ohlc_bar)

    print(f"📊 Price data points: {len(bot.price_data)}")
    print(f"📈 Sample price data:")
    for i, bar in enumerate(bot.price_data[-5:]):
        print(f"  Bar {i}: O:{bar['open']:.2f} H:{bar['high']:.2f} L:{bar['low']:.2f} C:{bar['close']:.2f}")

    # Calculate indicators
    indicators = bot.calculate_indicators()

    print(f"\n📊 CALCULATED INDICATORS:")
    print(f"  EMA Fast (21): {indicators.get('ema_fast', 'N/A')}")
    print(f"  EMA Slow (55): {indicators.get('ema_slow', 'N/A')}")
    print(f"  ATR (9): {indicators.get('atr', 'N/A')}")
    print(f"  Stoch D: {indicators.get('stoch_d', 'N/A')}")
    print(f"  Uptrend: {indicators.get('uptrend', 'N/A')}")
    print(f"  Downtrend: {indicators.get('downtrend', 'N/A')}")
    print(f"  D Falling: {indicators.get('d_falling', 'N/A')}")
    print(f"  D Rising: {indicators.get('d_rising', 'N/A')}")

    # Test entry signals
    current_price = prices[-1]
    print(f"\n🎯 TESTING ENTRY SIGNALS:")
    print(f"  Current Price: ${current_price}")
    print(f"  Position: {bot.position}")

    entry_signal = bot.check_entry_signals(indicators, current_price)
    print(f"  Entry Signal: {entry_signal}")

    # Check individual conditions
    print(f"\n🔍 CONDITION ANALYSIS:")

    if indicators.get('atr', 0) > 0:
        print("  ✅ ATR > 0: PASS")
    else:
        print("  ❌ ATR > 0: FAIL (ATR is 0 - no price movement)")

    if indicators.get('uptrend', False):
        print("  ✅ Uptrend: PASS")
    else:
        print("  ❌ Uptrend: FAIL")

    if indicators.get('d_falling', False):
        print("  ✅ D Falling: PASS")
    else:
        print("  ❌ D Falling: FAIL")

    stoch_d = indicators.get('stoch_d', 100)
    if stoch_d <= STRATEGY_PARAMS["STOCH_LO"]:
        print(f"  ✅ Stoch D <= {STRATEGY_PARAMS['STOCH_LO']}: PASS ({stoch_d})")
    else:
        print(f"  ❌ Stoch D <= {STRATEGY_PARAMS['STOCH_LO']}: FAIL ({stoch_d})")

    if indicators.get('downtrend', False):
        print("  ✅ Downtrend: PASS")
    else:
        print("  ❌ Downtrend: FAIL")

    if indicators.get('d_rising', False):
        print("  ✅ D Rising: PASS")
    else:
        print("  ❌ D Rising: FAIL")

    if stoch_d >= STRATEGY_PARAMS["STOCH_HI"]:
        print(f"  ✅ Stoch D >= {STRATEGY_PARAMS['STOCH_HI']}: PASS ({stoch_d})")
    else:
        print(f"  ❌ Stoch D >= {STRATEGY_PARAMS['STOCH_HI']}: FAIL ({stoch_d})")

if __name__ == "__main__":
    debug_indicators()