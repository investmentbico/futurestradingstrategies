#!/usr/bin/env python3
"""
Force test trading signals
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from mnq_live_bot import MNQ1MinBot, STRATEGY_PARAMS
from tastytrade_api import TastytradeAPI
import numpy as np
from datetime import datetime

def force_test_signals():
    """Force test signal generation with pre-loaded data"""

    print("🧪 FORCE TESTING TRADING SIGNALS")
    print("=" * 40)

    # Create API instance
    api = TastytradeAPI(paper_trading=True)

    # Create bot
    bot = MNQ1MinBot(api)

    # Pre-load price data with PERFECT signal conditions
    print("📊 Pre-loading price data for LONG signal...")

    base_price = 18000
    stoch_values = []

    for i in range(100):  # Need lots of data
        # Create perfect long signal conditions:
        # 1. Uptrend (EMA fast > EMA slow) - gradual upward trend
        # 2. D falling (Stoch D decreasing) - decreasing Stochastic
        # 3. Stoch D <= 25 - low Stochastic

        # Create upward trend
        trend_component = i * 2

        # Create decreasing Stochastic D (oversold and falling)
        if i < 20:
            stoch_d = 80  # Start high
        elif i < 40:
            stoch_d = 80 - (i-20) * 2.5  # Decrease to 30
        else:
            stoch_d = 25 - (i-40) * 0.1  # Stay low around 22-23

        stoch_values.append(stoch_d)

        # Price correlates with Stochastic (lower Stochastic = lower prices)
        stoch_component = (stoch_d - 50) * 2
        current_price = base_price + trend_component + stoch_component + np.random.normal(0, 3)

        # Create OHLC with range
        volatility = 20
        high = current_price + volatility
        low = current_price - volatility

        if bot.price_data:
            open_price = bot.price_data[-1]['close']
        else:
            open_price = current_price

        ohlc_bar = {
            'timestamp': datetime.now(),
            'open': open_price,
            'high': max(high, open_price, current_price),
            'low': min(low, open_price, current_price),
            'close': current_price
        }

        bot.price_data.append(ohlc_bar)

    print(f"✅ Loaded {len(bot.price_data)} price bars")

    # Calculate indicators
    indicators = bot.calculate_indicators()

    print("📊 CALCULATED INDICATORS:")
    print(f"  ATR: {indicators.get('atr', 'N/A')}")
    print(f"  EMA Fast: {indicators.get('ema_fast', 'N/A')}")
    print(f"  EMA Slow: {indicators.get('ema_slow', 'N/A')}")
    print(f"  Stoch D: {indicators.get('stoch_d', 'N/A')}")
    print(f"  Uptrend: {indicators.get('uptrend', 'N/A')}")
    print(f"  Downtrend: {indicators.get('downtrend', 'N/A')}")
    print(f"  D Falling: {indicators.get('d_falling', 'N/A')}")
    print(f"  D Rising: {indicators.get('d_rising', 'N/A')}")

    # FORCE a signal by directly setting indicator values
    print("\n🧪 FORCE TESTING - Direct Indicator Override")

    # Override indicators to meet long signal conditions
    test_indicators = indicators.copy()
    test_indicators['stoch_d'] = 20  # Below 25
    test_indicators['d_falling'] = True  # Force falling condition
    test_indicators['uptrend'] = True
    test_indicators['atr'] = 50

    print("📊 FORCED INDICATORS:")
    print(f"  ATR: {test_indicators.get('atr', 'N/A')}")
    print(f"  Stoch D: {test_indicators.get('stoch_d', 'N/A')}")
    print(f"  Uptrend: {test_indicators.get('uptrend', 'N/A')}")
    print(f"  D Falling: {test_indicators.get('d_falling', 'N/A')}")

    # Test with forced indicators
    forced_signal = bot.check_entry_signals(test_indicators, current_price)
    print(f"  Forced Signal Result: {forced_signal}")

    if forced_signal:
        print("  ✅ SIGNAL LOGIC WORKS! The issue is indicator calculation.")
        print("  ✅ Bot will trade when real market conditions meet the criteria.")
    else:
        print("  ❌ Even forced indicators don't work - check signal logic")

    # Check conditions manually
    print(f"\n🔍 MANUAL CONDITION CHECK:")

    atr_ok = indicators.get('atr', 0) > 0
    uptrend_ok = indicators.get('uptrend', False)
    d_falling_ok = indicators.get('d_falling', False)
    stoch_low_ok = indicators.get('stoch_d', 100) <= STRATEGY_PARAMS["STOCH_LO"]

    print(f"  ATR > 0: {atr_ok} ({indicators.get('atr', 0):.4f})")
    print(f"  Uptrend: {uptrend_ok}")
    print(f"  D Falling: {d_falling_ok}")
    print(f"  Stoch D <= {STRATEGY_PARAMS['STOCH_LO']}: {stoch_low_ok} ({indicators.get('stoch_d', 100):.2f})")

    long_condition = atr_ok and uptrend_ok and d_falling_ok and stoch_low_ok
    print(f"  LONG SIGNAL: {long_condition}")

    # Test short conditions
    downtrend_ok = indicators.get('downtrend', False)
    d_rising_ok = indicators.get('d_rising', False)
    stoch_high_ok = indicators.get('stoch_d', 0) >= STRATEGY_PARAMS["STOCH_HI"]

    print(f"  Downtrend: {downtrend_ok}")
    print(f"  D Rising: {d_rising_ok}")
    print(f"  Stoch D >= {STRATEGY_PARAMS['STOCH_HI']}: {stoch_high_ok} ({indicators.get('stoch_d', 0):.2f})")

    short_condition = atr_ok and downtrend_ok and d_rising_ok and stoch_high_ok
    print(f"  SHORT SIGNAL: {short_condition}")

    print(f"\n🎯 FINAL RESULT:")
    if entry_signal:
        print(f"  ✅ SIGNAL GENERATED: {entry_signal.upper()}")
        print("  ✅ Trading logic is working!")
    else:
        print("  ❌ No signal generated")
        print("  ❌ Need to adjust test data or check logic")

if __name__ == "__main__":
    force_test_signals()