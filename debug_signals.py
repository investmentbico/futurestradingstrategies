#!/usr/bin/env python3
"""
Debug script for enhanced strategy signals
"""

import pandas as pd
import numpy as np

# Copy indicator functions
def calc_ema(prices, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_stoch(high, low, close, k_period, d_period, smooth):
    lowest_low = np.array([np.min(low[i-k_period+1:i+1]) for i in range(k_period-1, len(low))])
    highest_high = np.array([np.max(high[i-k_period+1:i+1]) for i in range(k_period-1, len(high))])
    k = 100 * (close[k_period-1:] - lowest_low) / (highest_high - lowest_low)
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    d = calc_ema(k[~np.isnan(k)], d_period)
    d_full = np.full(len(k), np.nan)
    d_full[~np.isnan(k)] = d
    d_smooth = calc_ema(d_full[~np.isnan(d_full)], smooth)
    d_smooth_full = np.full(len(d_full), np.nan)
    d_smooth_full[~np.isnan(d_full)] = d_smooth
    return k, d_smooth_full

# Load data
df = pd.read_csv('data/es_2min.csv')
df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

print('Data loaded:', len(df), 'rows')

# Check basic indicators
close = df['close'].values
high = df['high'].values
low = df['low'].values

print('Data shapes - close:', close.shape)

# Test EMA
ema_fast = calc_ema(close, 26)
ema_slow = calc_ema(close, 68)
print('EMA fast at index 100:', ema_fast[100])
print('EMA slow at index 100:', ema_slow[100])
print('Uptrend at index 100:', ema_fast[100] > ema_slow[100])

# Check stochastic
stoch_k, stoch_d = calc_stoch(high, low, close, 11, 3, 2)
print('Stoch D at index 100:', stoch_d[100])
print('Stoch D is NaN:', np.isnan(stoch_d[100]))
print('Stoch condition check:', stoch_d[100] <= 27 if not np.isnan(stoch_d[100]) else 'NaN')

# Check for valid signals
valid_indices = []
for idx in range(50, min(200, len(df))):
    if (not np.isnan(stoch_d[idx]) and
        ema_fast[idx] > ema_slow[idx] and
        stoch_d[idx] <= 27):
        valid_indices.append(idx)

print('Found', len(valid_indices), 'valid long signals in first 200 bars')
if valid_indices:
    print('Sample indices:', valid_indices[:5])