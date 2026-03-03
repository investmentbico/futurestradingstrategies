#!/usr/bin/env python3
"""
Collect real-time MNQ data using Rithmic API for backtesting
"""

import sys
import os
import time
import pandas as pd
from datetime import datetime
import random

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Import the Rithmic API
from rithmic_api import RithmicAPI

def collect_realtime_data(symbol='MNQ', duration_minutes=5, interval_seconds=10):
    """
    Collect real-time data for backtesting
    """
    print(f"📊 Collecting {duration_minutes} minutes of {symbol} data...")
    print("⚠️  Note: Using mock data since real Rithmic credentials not provided")

    api = RithmicAPI(is_demo=True)  # Use demo mode to avoid connection issues

    data_points = []
    start_time = time.time()
    end_time = start_time + (duration_minutes * 60)

    print(f"⏰ Collecting data from {datetime.now().strftime('%H:%M:%S')} for {duration_minutes} minutes...")

    iteration = 0
    while time.time() < end_time:
        try:
            # Get current price
            price = api.get_current_price(symbol)

            # Create OHLC data (in real trading, we'd get actual OHLC)
            # For demo, we'll simulate OHLC around the current price
            base_price = price
            volatility = 5.0  # Simulate some volatility

            open_price = base_price + random.uniform(-volatility, volatility)
            high_price = open_price + random.uniform(0, volatility*2)
            low_price = open_price - random.uniform(0, volatility*2)
            close_price = base_price + random.uniform(-volatility, volatility)

            # Ensure OHLC relationships
            high_price = max(high_price, open_price, close_price)
            low_price = min(low_price, open_price, close_price)

            timestamp = int(time.time())

            data_points.append({
                'time': timestamp,
                'open': round(open_price, 2),
                'high': round(high_price, 2),
                'low': round(low_price, 2),
                'close': round(close_price, 2)
            })

            iteration += 1
            print(f"📈 [{iteration}] {datetime.now().strftime('%H:%M:%S')} - {symbol}: O:{open_price:.2f} H:{high_price:.2f} L:{low_price:.2f} C:{close_price:.2f}")

            # Wait for next interval
            time.sleep(interval_seconds)

        except KeyboardInterrupt:
            print("\n🛑 Data collection stopped by user")
            break
        except Exception as e:
            print(f"❌ Error collecting data: {e}")
            time.sleep(2)  # Wait before retry

    return data_points

def save_data_to_csv(data_points, filename):
    """Save collected data to CSV file"""
    if not data_points:
        print("❌ No data collected")
        return False

    df = pd.DataFrame(data_points)
    df.to_csv(filename, index=False)
    print(f"💾 Saved {len(data_points)} data points to {filename}")
    return True

def run_backtest_on_collected_data(data_file):
    """Run the MNQ backtest on collected data"""
    print(f"\n🔬 Running backtest on collected data: {data_file}")

    # Import and run the backtest
    from mnq_comprehensive_backtest import run_mnq_backtest

    # Run with default parameters (15 contracts, $400 hard stop)
    result = run_mnq_backtest(contracts=15, hard_stop=400, data_file=data_file)

    return result

def main():
    print("🚀 MNQ Real-Time Data Collection & Backtest")
    print("=" * 60)

    # Configuration
    symbol = 'MNQ'
    duration_minutes = 2  # Collect 2 minutes of data (12 data points at 10s intervals)
    data_file = f'realtime_mnq_{int(time.time())}.csv'

    # Step 1: Collect data
    print("\n📡 Step 1: Collecting real-time data...")
    data_points = collect_realtime_data(symbol, duration_minutes)

    if not data_points:
        print("❌ Failed to collect data")
        return

    # Step 2: Save data
    print("\n💾 Step 2: Saving data...")
    if not save_data_to_csv(data_points, data_file):
        return

    # Step 3: Run backtest
    print("\n🔬 Step 3: Running backtest...")
    try:
        result = run_backtest_on_collected_data(data_file)
        print("\n✅ Backtest completed!")
        print(f"📊 Results: {result}")
    except Exception as e:
        print(f"❌ Backtest failed: {e}")

if __name__ == "__main__":
    main()