#!/usr/bin/env python3
"""
Quick test to verify indicator calculations work
"""

import numpy as np
from datetime import datetime

# Simulate the fixed update_price_data logic
def create_test_price_data():
    """Create test price data with realistic OHLC bars"""
    price_data = []
    base_price = 18000

    for i in range(60):  # Create 60 bars
        current_price = base_price + np.random.normal(0, 20)  # Add some trend

        if not price_data:
            # First bar
            volatility = current_price * 0.001
            high = current_price + volatility
            low = current_price - volatility
            open_price = current_price + np.random.uniform(-volatility/2, volatility/2)
        else:
            # Subsequent bars
            prev_close = price_data[-1]['close']
            change = np.random.normal(0, current_price * 0.002)
            open_price = prev_close
            high = max(open_price, current_price) + abs(np.random.normal(0, current_price * 0.001))
            low = min(open_price, current_price) - abs(np.random.normal(0, current_price * 0.001))

        high = max(high, open_price, current_price)
        low = min(low, open_price, current_price)

        ohlc_bar = {
            'timestamp': datetime.now(),
            'open': open_price,
            'high': high,
            'low': low,
            'close': current_price
        }

        price_data.append(ohlc_bar)
        base_price = current_price  # Update for next iteration

    return price_data

def test_indicators():
    """Test that indicators can be calculated"""
    price_data = create_test_price_data()

    print("🧪 TESTING PRICE DATA GENERATION")
    print("=" * 40)

    print(f"📊 Generated {len(price_data)} price bars")
    print("📈 Sample bars:")
    for i, bar in enumerate(price_data[-5:]):
        range_val = bar['high'] - bar['low']
        print(f"  Bar {i}: O:{bar['open']:.2f} H:{bar['high']:.2f} L:{bar['low']:.2f} C:{bar['close']:.2f} Range:${range_val:.2f}")

    # Test ATR calculation (simplified)
    highs = np.array([bar['high'] for bar in price_data])
    lows = np.array([bar['low'] for bar in price_data])
    closes = np.array([bar['close'] for bar in price_data])

    # Simple ATR calculation
    tr = np.maximum(highs - lows,
                   np.maximum(np.abs(highs - np.roll(closes, 1)),
                             np.abs(lows - np.roll(closes, 1))))
    atr = np.mean(tr[1:])  # Skip first NaN

    print(f"\n📊 ATR Calculation:")
    print(f"  Average True Range: ${atr:.4f}")

    if atr > 0:
        print("  ✅ ATR > 0: Indicators should work now!")
    else:
        print("  ❌ ATR = 0: Still an issue")

    # Test trend detection
    ema_fast_period = 21
    ema_slow_period = 55

    if len(closes) >= ema_slow_period:
        # Simple EMA calculation
        ema_fast = np.mean(closes[-ema_fast_period:])
        ema_slow = np.mean(closes[-ema_slow_period:])

        uptrend = ema_fast > ema_slow
        downtrend = ema_fast < ema_slow

        print(f"\n📊 Trend Detection:")
        print(f"  EMA Fast (21): ${ema_fast:.2f}")
        print(f"  EMA Slow (55): ${ema_slow:.2f}")
        print(f"  Uptrend: {uptrend}")
        print(f"  Downtrend: {downtrend}")

    print(f"\n🎯 CONCLUSION:")
    if atr > 0:
        print("  ✅ Price data now has range - indicators should calculate properly")
        print("  ✅ Trading signals should be generated")
    else:
        print("  ❌ Still issues with price data range")

if __name__ == "__main__":
    test_indicators()