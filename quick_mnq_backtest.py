#!/usr/bin/env python3
"""
Quick MNQ backtest on real data using Rithmic API integration
"""

import pandas as pd
import numpy as np
from datetime import datetime
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Import the Rithmic API
from rithmic_api import RithmicAPI

def quick_backtest():
    print("🚀 Quick MNQ Backtest with Rithmic API Integration")
    print("=" * 60)

    # Load recent MNQ data (last 1000 bars for speed)
    print("📊 Loading MNQ data...")
    df = pd.read_csv('data/mnq_1min.csv')
    df = df.tail(1000).reset_index(drop=True)  # Use last 1000 bars

    print(f"📈 Loaded {len(df)} bars of MNQ 1min data")
    print(f"📅 Date range: {pd.to_datetime(df['time'].iloc[0], unit='s')} to {pd.to_datetime(df['time'].iloc[-1], unit='s')}")

    # Test Rithmic API connection
    print("\n🔗 Testing Rithmic API connection...")
    api = RithmicAPI(is_demo=True)  # Use demo mode

    current_price = api.get_current_price('MNQ')
    print(f"💰 Current MNQ price via Rithmic API: ${current_price:.2f}")

    # Simple strategy: EMA crossover with basic signals
    print("\n🔬 Running simple EMA crossover strategy...")

    # Calculate indicators
    def calc_ema(prices, period):
        k = 2.0 / (period + 1)
        ema = [prices[0]]
        for price in prices[1:]:
            ema.append(price * k + ema[-1] * (1 - k))
        return ema

    closes = df['close'].values
    df['ema_fast'] = calc_ema(closes, 21)
    df['ema_slow'] = calc_ema(closes, 55)

    # Generate signals
    df['signal'] = 0
    df.loc[(df['ema_fast'] > df['ema_slow']) & (df['ema_fast'].shift(1) <= df['ema_slow'].shift(1)), 'signal'] = 1  # Buy
    df.loc[(df['ema_fast'] < df['ema_slow']) & (df['ema_fast'].shift(1) >= df['ema_slow'].shift(1)), 'signal'] = -1  # Sell

    # Simple backtest
    position = 0
    entry_price = 0
    trades = []
    equity = 100000  # Starting capital

    for i, row in df.iterrows():
        if row['signal'] == 1 and position == 0:  # Buy signal
            position = 1
            entry_price = row['close']
            print(f"📈 BUY at ${entry_price:.2f} (bar {i})")

        elif row['signal'] == -1 and position == 1:  # Sell signal
            exit_price = row['close']
            pnl = (exit_price - entry_price) * 20  # MNQ contract value
            equity += pnl
            trades.append(pnl)
            print(f"📉 SELL at ${exit_price:.2f} (bar {i}), P&L: ${pnl:.2f}")
            position = 0

    # Calculate results
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t > 0])
    losing_trades = len([t for t in trades if t < 0])
    win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0
    total_pnl = sum(trades)
    avg_win = np.mean([t for t in trades if t > 0]) if winning_trades > 0 else 0
    avg_loss = np.mean([t for t in trades if t < 0]) if losing_trades > 0 else 0

    print("
📊 BACKTEST RESULTS:"    print("=" * 40)
    print(f"Total Trades: {total_trades}")
    print(f"Winning Trades: {winning_trades}")
    print(f"Losing Trades: {losing_trades}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"Total P&L: ${total_pnl:.2f}")
    print(f"Average Win: ${avg_win:.2f}")
    print(f"Average Loss: ${avg_loss:.2f}")
    print(f"Final Equity: ${equity:.2f}")

    print("
✅ Backtest completed successfully!"    print("💡 This demonstrates the Rithmic API integration working with real MNQ data")

if __name__ == "__main__":
    quick_backtest()