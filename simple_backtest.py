#!/usr/bin/env python3
"""
Quick MNQ backtest on real data
"""

import pandas as pd
import numpy as np

print("🚀 Quick MNQ Strategy Backtest on Real Data")
print("=" * 50)

# Load MNQ data
df = pd.read_csv('data/mnq_1min.csv')
df = df.tail(1000).reset_index(drop=True)  # Last 1000 bars
print(f"📊 Loaded {len(df)} MNQ 1min bars")

# Calculate EMAs
def calc_ema(prices, period):
    k = 2.0 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema

closes = df['close'].values
df['ema21'] = calc_ema(closes, 21)
df['ema55'] = calc_ema(closes, 55)

# Simple EMA crossover strategy
position = 0
trades = []
equity = 100000

for i in range(1, len(df)):
    prev_fast, curr_fast = df['ema21'].iloc[i-1], df['ema21'].iloc[i]
    prev_slow, curr_slow = df['ema55'].iloc[i-1], df['ema55'].iloc[i]

    if curr_fast > curr_slow and prev_fast <= prev_slow and position == 0:
        # Buy signal
        position = 1
        entry_price = df['close'].iloc[i]

    elif curr_fast < curr_slow and prev_fast >= prev_slow and position == 1:
        # Sell signal
        exit_price = df['close'].iloc[i]
        pnl = (exit_price - entry_price) * 20  # MNQ contract
        trades.append(pnl)
        equity += pnl
        position = 0

# Results
total_trades = len(trades)
wins = len([t for t in trades if t > 0])
win_rate = wins / total_trades * 100 if total_trades > 0 else 0
total_pnl = sum(trades)

print(f"📈 Total Trades: {total_trades}")
print(f"🏆 Win Rate: {win_rate:.1f}%")
print(f"💰 Total P&L: ${total_pnl:,.0f}")
print(f"📊 Final Equity: ${equity:,.0f}")
print("✅ Backtest completed on real MNQ data!")