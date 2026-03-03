#!/usr/bin/env python3
import pandas as pd
import sys
sys.path.append('.')
from comprehensive_backtest import run_single_backtest

# Load ES 2min data
df = pd.read_csv('data/es_2min.csv')
print(f'Loaded {len(df)} bars')

# Test current parameters
result = run_single_backtest(df, 'ES', '2min', {}, 2, 300000)
print(f'Current params: ${result["total_pnl"]:,.0f} P&L, {result["win_rate"]:.1f}% win rate')

# Test alternative parameters
params = {'stoch_k_period': 21, 'stoch_d_period': 5, 'stoch_smooth': 5, 'overbought': 75, 'oversold': 25, 'ema_period': 30, 'threshold': 0.7}
result2 = run_single_backtest(df, 'ES', '2min', params, 2, 300000)
print(f'Alt params: ${result2["total_pnl"]:,.0f} P&L, {result2["win_rate"]:.1f}% win rate')