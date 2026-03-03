#!/usr/bin/env python3
"""
Enhanced ES 2min Strategy with Hybrid Indicators
Adds RSI, momentum, and hybrid signal combinations for improved performance
"""

import numpy as np
import pandas as pd
from datetime import datetime
import os
import time

# Copy indicator functions from comprehensive_backtest.py
def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period-1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
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

# =============================================================================
# ENHANCED INDICATORS
# =============================================================================

def calc_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate RSI indicator"""
    delta = np.diff(prices)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    avg_gain = np.zeros_like(prices)
    avg_loss = np.zeros_like(prices)

    # First RSI value
    avg_gain[period] = np.mean(gain[:period])
    avg_loss[period] = np.mean(loss[:period])

    # Smoothed RSI
    for i in range(period + 1, len(prices)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i-1]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i-1]) / period

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = 50  # Neutral value for initial periods
    return rsi

def calc_momentum(prices: np.ndarray, period: int = 10) -> np.ndarray:
    """Calculate momentum indicator (rate of change)"""
    momentum = np.zeros_like(prices)
    momentum[period:] = (prices[period:] - prices[:-period]) / prices[:-period] * 100
    return momentum

def calc_tsi(close: np.ndarray, short_period: int = 13, long_period: int = 25) -> np.ndarray:
    """Calculate True Strength Index (TSI) - momentum oscillator"""
    momentum = np.diff(close)
    momentum = np.concatenate([[0], momentum])  # Add zero for first element

    # Double smoothed momentum
    ema_short = calc_ema(momentum, short_period)
    ema_long = calc_ema(ema_short, long_period)

    # Signal line
    signal = calc_ema(ema_long, short_period)

    # TSI
    tsi = np.zeros_like(close)
    mask = ema_long != 0
    tsi[mask] = 100 * (ema_long[mask] / abs(ema_long[mask]))

    return tsi, signal

def calc_bollinger_bands(prices: np.ndarray, period: int = 20, std_dev: float = 2.0):
    """Calculate Bollinger Bands"""
    sma = calc_ema(prices, period)  # Using EMA as basis
    std = np.zeros_like(prices)

    for i in range(period, len(prices)):
        std[i] = np.std(prices[i-period:i+1])

    upper = sma + (std * std_dev)
    lower = sma - (std * std_dev)

    return upper, sma, lower

# =============================================================================
# ENHANCED STRATEGY PARAMETERS
# =============================================================================

# Enhanced strategy variants with adjusted parameters for more trades
ENHANCED_STRATEGIES = {
    'original': {  # Current winning strategy
        "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 11, "STOCH_LO": 27, "STOCH_HI": 73, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
        "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8
    },

    'more_trades': {  # Relaxed parameters for more signals
        "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 9, "STOCH_LO": 35, "STOCH_HI": 65, "SL_ATR_MULT": 1.2, "TP_RR": 1.8,
        "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
    },

    'rsi_filter': {  # Add RSI filter with relaxed stoch
        "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 9, "STOCH_LO": 35, "STOCH_HI": 65, "SL_ATR_MULT": 1.2, "TP_RR": 1.8,
        "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0, "RSI_LEN": 14, "RSI_OVERSOLD": 35, "RSI_OVERBOUGHT": 65
    },

    'momentum_boost': {  # Add momentum with relaxed params
        "EMA_FAST": 18, "EMA_SLOW": 45, "STOCH_K": 8, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 8, "STOCH_LO": 40, "STOCH_HI": 60, "SL_ATR_MULT": 1.3, "TP_RR": 2.0,
        "TRAIL_ATR_MULT": 0.8, "BE_POINTS": 3.5, "MOMENTUM_LEN": 8, "MOMENTUM_THRESH": 0.05
    },

    'hybrid_aggressive': {  # Very relaxed for maximum trades
        "EMA_FAST": 15, "EMA_SLOW": 35, "STOCH_K": 7, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 7, "STOCH_LO": 45, "STOCH_HI": 55, "SL_ATR_MULT": 1.4, "TP_RR": 2.2,
        "TRAIL_ATR_MULT": 0.9, "BE_POINTS": 4.0, "RSI_LEN": 12, "RSI_OVERSOLD": 40, "RSI_OVERBOUGHT": 60,
        "MOMENTUM_LEN": 6, "MOMENTUM_THRESH": 0.02
    },

    'hybrid_conservative': {  # Balanced approach
        "EMA_FAST": 24, "EMA_SLOW": 60, "STOCH_K": 10, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 10, "STOCH_LO": 32, "STOCH_HI": 68, "SL_ATR_MULT": 1.1, "TP_RR": 1.7,
        "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 3.0, "RSI_LEN": 16, "RSI_OVERSOLD": 32, "RSI_OVERBOUGHT": 68,
        "MOMENTUM_LEN": 9, "MOMENTUM_THRESH": 0.08
    },

    'tsi_momentum': {  # TSI + Momentum hybrid
        "EMA_FAST": 22, "EMA_SLOW": 50, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 9, "STOCH_LO": 38, "STOCH_HI": 62, "SL_ATR_MULT": 1.2, "TP_RR": 1.9,
        "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.2, "TSI_SHORT": 13, "TSI_LONG": 25,
        "TSI_OVERSOLD": -20, "TSI_OVERBOUGHT": 20, "MOMENTUM_LEN": 7, "MOMENTUM_THRESH": 0.06
    }
}

# =============================================================================
# ENHANCED SIGNAL GENERATION
# =============================================================================

def generate_enhanced_signals(df: pd.DataFrame, strategy_name: str) -> dict:
    """Generate signals with enhanced indicators"""
    params = ENHANCED_STRATEGIES[strategy_name]
    close = df['close'].values
    high = df['high'].values
    low = df['low'].values

    # Base indicators
    ema_fast = calc_ema(close, params["EMA_FAST"])
    ema_slow = calc_ema(close, params["EMA_SLOW"])
    stoch_k, stoch_d = calc_stoch(high, low, close, params["STOCH_K"], params["STOCH_D"], params["STOCH_SMT"])
    atr = calc_atr(high, low, close, params["ATR_LEN"])

    # Trend signals
    uptrend = ema_fast > ema_slow
    downtrend = ema_fast < ema_slow

    # Stochastic signals
    stoch_d_smooth = calc_ema(stoch_d, 2)  # Additional smoothing
    d_rising = np.zeros_like(stoch_d_smooth, dtype=bool)
    d_falling = np.zeros_like(stoch_d_smooth, dtype=bool)
    d_rising[1:] = stoch_d_smooth[1:] > stoch_d_smooth[:-1]
    d_falling[1:] = stoch_d_smooth[1:] < stoch_d_smooth[:-1]

    signals = {
        'uptrend': uptrend,
        'downtrend': downtrend,
        'd_rising': d_rising,
        'd_falling': d_falling,
        'stoch_d': stoch_d_smooth,
        'atr': atr
    }

    # Add enhanced indicators based on strategy
    if 'rsi' in strategy_name.lower() or 'hybrid' in strategy_name.lower():
        rsi = calc_rsi(close, params.get("RSI_LEN", 14))
        signals['rsi'] = rsi
        signals['rsi_oversold'] = rsi < params.get("RSI_OVERSOLD", 35)
        signals['rsi_overbought'] = rsi > params.get("RSI_OVERBOUGHT", 65)

    if 'momentum' in strategy_name.lower() or 'hybrid' in strategy_name.lower():
        momentum = calc_momentum(close, params.get("MOMENTUM_LEN", 10))
        signals['momentum'] = momentum
        signals['momentum_positive'] = momentum > params.get("MOMENTUM_THRESH", 0.1)
        signals['momentum_negative'] = momentum < -params.get("MOMENTUM_THRESH", 0.1)

    if 'tsi' in strategy_name.lower():
        tsi, tsi_signal = calc_tsi(close, params.get("TSI_SHORT", 13), params.get("TSI_LONG", 25))
        signals['tsi'] = tsi
        signals['tsi_signal'] = tsi_signal
        signals['tsi_bullish'] = (tsi > tsi_signal) & (tsi > params.get("TSI_OVERSOLD", -25))
        signals['tsi_bearish'] = (tsi < tsi_signal) & (tsi < params.get("TSI_OVERBOUGHT", 25))

    return signals

# =============================================================================
# ENHANCED ENTRY LOGIC
# =============================================================================

def check_enhanced_entry(signals: dict, strategy_name: str, idx: int) -> tuple:
    """Check for enhanced entry signals"""
    params = ENHANCED_STRATEGIES[strategy_name]

    # Base conditions (same for all strategies)
    base_long = (signals['uptrend'][idx] and signals['d_falling'][idx] and
                signals['stoch_d'][idx] <= params["STOCH_LO"])

    base_short = (signals['downtrend'][idx] and signals['d_rising'][idx] and
                 signals['stoch_d'][idx] >= params["STOCH_HI"])

    # Enhanced conditions based on strategy
    if strategy_name == 'original':
        return base_long, base_short

    elif strategy_name == 'rsi_filter':
        # Add RSI filter
        rsi_long = signals.get('rsi_oversold', [False] * len(signals['uptrend']))[idx]
        rsi_short = signals.get('rsi_overbought', [False] * len(signals['uptrend']))[idx]
        return base_long and rsi_long, base_short and rsi_short

    elif strategy_name == 'momentum_boost':
        # Add momentum confirmation
        mom_long = signals.get('momentum_positive', [False] * len(signals['uptrend']))[idx]
        mom_short = signals.get('momentum_negative', [False] * len(signals['uptrend']))[idx]
        return base_long and mom_long, base_short and mom_short

    elif strategy_name == 'hybrid_aggressive':
        # RSI + Momentum filter
        rsi_long = signals.get('rsi_oversold', [False] * len(signals['uptrend']))[idx]
        rsi_short = signals.get('rsi_overbought', [False] * len(signals['uptrend']))[idx]
        mom_long = signals.get('momentum_positive', [False] * len(signals['uptrend']))[idx]
        mom_short = signals.get('momentum_negative', [False] * len(signals['uptrend']))[idx]
        return base_long and (rsi_long or mom_long), base_short and (rsi_short or mom_short)

    elif strategy_name == 'hybrid_conservative':
        # RSI AND Momentum filter (stricter)
        rsi_long = signals.get('rsi_oversold', [False] * len(signals['uptrend']))[idx]
        rsi_short = signals.get('rsi_overbought', [False] * len(signals['uptrend']))[idx]
        mom_long = signals.get('momentum_positive', [False] * len(signals['uptrend']))[idx]
        mom_short = signals.get('momentum_negative', [False] * len(signals['uptrend']))[idx]
        return base_long and rsi_long and mom_long, base_short and rsi_short and mom_short

    elif strategy_name == 'tsi_momentum':
        # TSI + Momentum hybrid
        tsi_long = signals.get('tsi_bullish', [False] * len(signals['uptrend']))[idx]
        tsi_short = signals.get('tsi_bearish', [False] * len(signals['uptrend']))[idx]
        mom_long = signals.get('momentum_positive', [False] * len(signals['uptrend']))[idx]
        mom_short = signals.get('momentum_negative', [False] * len(signals['uptrend']))[idx]
        return base_long and (tsi_long or mom_long), base_short and (tsi_short or mom_short)

    return False, False

# =============================================================================
# MAIN ENHANCED BACKTEST
# =============================================================================

def run_enhanced_backtest(strategy_name: str = 'original', contracts: int = 3) -> dict:
    """Run enhanced backtest with specified strategy"""
    print(f"🚀 Running Enhanced {strategy_name} Strategy (ES 2min, {contracts} contracts)")

    # Load data
    df = pd.read_csv('data/es_2min.csv')
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Generate enhanced signals
    signals = generate_enhanced_signals(df, strategy_name)

    # Count potential signals
    long_signals = 0
    short_signals = 0

    for idx in range(50, len(df)):  # Start after indicator warmup
        try:
            long_entry, short_entry = check_enhanced_entry(signals, strategy_name, idx)

            if long_entry:
                long_signals += 1
            if short_entry:
                short_signals += 1
        except:
            continue  # Skip if there's an error

    total_signals = long_signals + short_signals

    # Estimate monthly trades (rough calculation)
    # Assuming ~20 trading days per month, ~6.5 hours of trading per day
    # 2min bars = ~195 bars per day, but only during market hours
    trading_hours_per_day = 6.5
    bars_per_hour = 60 / 2  # 2min bars
    trading_bars_per_day = trading_hours_per_day * bars_per_hour
    trading_days_per_month = 20

    monthly_trades_estimate = total_signals * (trading_days_per_month * trading_bars_per_day) / len(df)

    return {
        'strategy': strategy_name,
        'contracts': contracts,
        'total_signals': total_signals,
        'long_signals': long_signals,
        'short_signals': short_signals,
        'estimated_monthly_trades': monthly_trades_estimate,
        'data_points': len(df)
    }

# =============================================================================
# STRATEGY OPTIMIZATION
# =============================================================================

def optimize_enhanced_strategies():
    """Test all enhanced strategies and find the best one"""
    print("🎯 Testing Enhanced ES 2min Strategies")
    print("=" * 60)

    results = []

    for strategy_name in ENHANCED_STRATEGIES.keys():
        for contracts in [3, 4, 5]:
            result = run_enhanced_backtest(strategy_name, contracts)
            results.append(result)

            print(f"📊 {strategy_name:<20} {contracts} contracts: "
                  f"{result['total_signals']} signals, "
                  f"~{result['estimated_monthly_trades']:.0f} monthly trades")

    # Sort by estimated monthly trades (targeting 50-300 range)
    optimal_results = [r for r in results if 50 <= r['estimated_monthly_trades'] <= 300]
    optimal_results.sort(key=lambda x: x['estimated_monthly_trades'], reverse=True)

    print("\n🏆 OPTIMAL STRATEGIES (50-300 monthly trades):")
    print("-" * 60)

    for result in optimal_results[:5]:  # Top 5
        print(f"🥇 {result['strategy']:<20} {result['contracts']} contracts: "
              f"{result['estimated_monthly_trades']:.0f} monthly trades, "
              f"{result['total_signals']} total signals")

    return optimal_results

if __name__ == "__main__":
    optimize_enhanced_strategies()