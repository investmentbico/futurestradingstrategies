#!/usr/bin/env python3
"""
ES 2min Strategy Parameter Optimization
Tests multiple parameter combinations to find optimal balance of trades and profitability
"""

import numpy as np
import pandas as pd
import itertools
import os
from datetime import datetime

# =============================================================================
# PARAMETER RANGES TO TEST
# =============================================================================

PARAMETER_RANGES = {
    'EMA_FAST': [20, 22, 24, 26, 28, 30],
    'EMA_SLOW': [50, 55, 60, 65, 70, 75],
    'STOCH_K': [8, 9, 10, 11, 12],
    'STOCH_D': [2, 3, 4],
    'STOCH_LO': [20, 25, 27, 30, 35],
    'STOCH_HI': [65, 70, 73, 75, 80],
    'ATR_LEN': [8, 9, 10, 11, 12],
    'SL_ATR_MULT': [1.0, 1.1, 1.2, 1.3],
    'TP_RR': [1.4, 1.6, 1.8, 2.0],
    'TRAIL_ATR_MULT': [0.5, 0.6, 0.7, 0.8],
    'BE_POINTS': [2.5, 2.8, 3.0, 3.2]
}

# =============================================================================
# OPTIMIZATION LOGIC
# =============================================================================

def run_parameter_test(params, data_file='data/es_2min.csv'):
    """Run backtest with specific parameters"""
    try:
        # Load data
        df = pd.read_csv(data_file)
        df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
        df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
        df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

        # Extract data
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values

        # Calculate indicators
        ema_fast = calc_ema(close, params["EMA_FAST"])
        ema_slow = calc_ema(close, params["EMA_SLOW"])
        atr = calc_atr(high, low, close, params["ATR_LEN"])
        k_sm, d_sm = calc_stoch(high, low, close, params["STOCH_K"], params["STOCH_D"], 2)

        # Count signals
        long_signals = 0
        short_signals = 0

        for idx in range(50, len(df)):
            if not np.isnan(atr[idx]) and not np.isnan(d_sm[idx]):
                uptrend = ema_fast[idx] > ema_slow[idx]
                downtrend = ema_fast[idx] < ema_slow[idx]
                d_falling = d_sm[idx] < d_sm[idx-1] if idx >= 1 else False
                d_rising = d_sm[idx] > d_sm[idx-1] if idx >= 1 else False

                if uptrend and d_falling and d_sm[idx] <= params["STOCH_LO"]:
                    long_signals += 1
                if downtrend and d_rising and d_sm[idx] >= params["STOCH_HI"]:
                    short_signals += 1

        total_signals = long_signals + short_signals

        # Estimate monthly trades
        trading_hours_per_day = 6.5
        bars_per_hour = 60 / 2  # 2min bars
        trading_bars_per_day = trading_hours_per_day * bars_per_hour
        trading_days_per_month = 20
        monthly_trades_estimate = total_signals * (trading_days_per_month * trading_bars_per_day) / len(df)

        return {
            'total_signals': total_signals,
            'long_signals': long_signals,
            'short_signals': short_signals,
            'estimated_monthly_trades': monthly_trades_estimate,
            'params': params
        }

    except Exception as e:
        return {
            'total_signals': 0,
            'long_signals': 0,
            'short_signals': 0,
            'estimated_monthly_trades': 0,
            'params': params,
            'error': str(e)
        }

def optimize_parameters():
    """Test different parameter combinations"""
    print("🚀 ES 2min Parameter Optimization")
    print("=" * 60)

    # Test different combinations
    results = []

    # Test 1: Vary EMA periods (most important)
    print("📊 Testing EMA combinations...")
    ema_combinations = list(itertools.product(
        PARAMETER_RANGES['EMA_FAST'],
        PARAMETER_RANGES['EMA_SLOW']
    ))

    for fast, slow in ema_combinations:
        if fast >= slow:  # Skip invalid combinations
            continue

        params = {
            "EMA_FAST": fast, "EMA_SLOW": slow, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 27, "STOCH_HI": 73, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8
        }

        result = run_parameter_test(params)
        results.append(result)
        print(f"EMA {fast}/{slow}: {result['total_signals']} signals (~{result['estimated_monthly_trades']:.0f} monthly)")

    # Test 2: Vary Stochastic thresholds (for more/less signals)
    print("\n📊 Testing Stochastic thresholds...")
    base_params = {
        "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 11, "SL_ATR_MULT": 1.1, "TP_RR": 1.6, "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8
    }

    stoch_combinations = list(itertools.product(
        PARAMETER_RANGES['STOCH_LO'],
        PARAMETER_RANGES['STOCH_HI']
    ))

    for lo, hi in stoch_combinations:
        if lo >= hi:  # Skip invalid combinations
            continue

        params = base_params.copy()
        params["STOCH_LO"] = lo
        params["STOCH_HI"] = hi

        result = run_parameter_test(params)
        results.append(result)
        print(f"Stoch {lo}/{hi}: {result['total_signals']} signals (~{result['estimated_monthly_trades']:.0f} monthly)")

    # Test 3: Vary risk/reward ratios
    print("\n📊 Testing Risk/Reward ratios...")
    rr_combinations = list(itertools.product(
        PARAMETER_RANGES['SL_ATR_MULT'],
        PARAMETER_RANGES['TP_RR']
    ))

    for sl, tp in rr_combinations:
        params = base_params.copy()
        params["SL_ATR_MULT"] = sl
        params["TP_RR"] = tp
        params["STOCH_LO"] = 27  # Reset to original
        params["STOCH_HI"] = 73

        result = run_parameter_test(params)
        results.append(result)
        print(f"R:R {sl}/{tp}: {result['total_signals']} signals (~{result['estimated_monthly_trades']:.0f} monthly)")

    # Filter and rank results
    valid_results = [r for r in results if r['estimated_monthly_trades'] > 0]

    # Sort by different criteria
    by_trades = sorted(valid_results, key=lambda x: x['estimated_monthly_trades'], reverse=True)
    by_signals = sorted(valid_results, key=lambda x: x['total_signals'], reverse=True)

    print("\n" + "=" * 80)
    print("🏆 TOP 10 BY MONTHLY TRADES")
    print("=" * 80)

    for i, result in enumerate(by_trades[:10]):
        params = result['params']
        print(f"{i+1}. {result['estimated_monthly_trades']:.0f} monthly | {result['total_signals']} signals")
        print(f"   EMA: {params['EMA_FAST']}/{params['EMA_SLOW']}, Stoch: {params['STOCH_LO']}/{params['STOCH_HI']}, R:R: {params['SL_ATR_MULT']}/{params['TP_RR']}")

    print("\n" + "=" * 80)
    print("🎯 TOP 10 BY TOTAL SIGNALS")
    print("=" * 80)

    for i, result in enumerate(by_signals[:10]):
        params = result['params']
        print(f"{i+1}. {result['total_signals']} signals | {result['estimated_monthly_trades']:.0f} monthly")
        print(f"   EMA: {params['EMA_FAST']}/{params['EMA_SLOW']}, Stoch: {params['STOCH_LO']}/{params['STOCH_HI']}, R:R: {params['SL_ATR_MULT']}/{params['TP_RR']}")

    # Find optimal balance (50-300 monthly trades)
    optimal_results = [r for r in valid_results if 50 <= r['estimated_monthly_trades'] <= 300]

    if optimal_results:
        print("\n" + "=" * 80)
        print("💎 OPTIMAL BALANCE (50-300 monthly trades)")
        print("=" * 80)

        # Sort by trade frequency within optimal range
        optimal_sorted = sorted(optimal_results, key=lambda x: x['estimated_monthly_trades'], reverse=True)

        for i, result in enumerate(optimal_sorted[:5]):
            params = result['params']
            print(f"{i+1}. {result['estimated_monthly_trades']:.0f} monthly | {result['total_signals']} signals")
            print(f"   EMA: {params['EMA_FAST']}/{params['EMA_SLOW']}, Stoch: {params['STOCH_LO']}/{params['STOCH_HI']}, R:R: {params['SL_ATR_MULT']}/{params['TP_RR']}")

    return valid_results

# Copy required functions
def calc_ema(prices, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high, low, close, period):
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period-1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
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

if __name__ == "__main__":
    optimize_parameters()