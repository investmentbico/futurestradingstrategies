#!/usr/bin/env python3
"""
MNQ 1min Strategy - Daily Drawdown Analysis
Focused analysis for 15 contracts, $200 hard stop combination
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# MNQ 1MIN STRATEGY PARAMETERS (BEST PERFORMER)
# =============================================================================

STRATEGY_PARAMS = {
    "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
    "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
}

# =============================================================================
# INDICATORS
# =============================================================================

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
# DAILY DRAWDOWN ANALYSIS
# =============================================================================

def analyze_daily_drawdown(contracts: int = 15, hard_stop: float = 200, data_file: str = 'data/mnq_1min.csv'):
    """Analyze daily drawdown for specific parameters"""

    # Load data
    df = pd.read_csv(data_file)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter last 6 months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=6)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)

    print(f"📊 Analyzing last 6 months: {start_date.date()} to {end_date.date()}")
    print(f"📈 Data points: {len(df)} bars")
    print(f"🎯 Parameters: {contracts} contracts, ${hard_stop} hard stop")

    # Calculate indicators
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values

    df['ema_fast'] = calc_ema(closes, STRATEGY_PARAMS["EMA_FAST"])
    df['ema_slow'] = calc_ema(closes, STRATEGY_PARAMS["EMA_SLOW"])
    df['atr'] = calc_atr(highs, lows, closes, STRATEGY_PARAMS["ATR_LEN"])
    k_sm, d_sm = calc_stoch(highs, lows, closes, STRATEGY_PARAMS["STOCH_K"],
                          STRATEGY_PARAMS["STOCH_D"], STRATEGY_PARAMS["STOCH_SMT"])
    df['stoch_k'] = k_sm
    df['stoch_d'] = d_sm

    # Calculate signals
    df['uptrend'] = df['ema_fast'] > df['ema_slow']
    df['downtrend'] = df['ema_fast'] < df['ema_slow']
    df['d_falling'] = df['stoch_d'] < df['stoch_d'].shift(1)
    df['d_rising'] = df['stoch_d'] > df['stoch_d'].shift(1)

    # Trading variables
    position = 0
    entry_price = 0
    stop_loss = 0
    take_profit = 0
    trailing_stop = 0
    breakeven_triggered = False

    trades = []
    cumulative_pnl = 0

    # Daily tracking
    daily_data = {}
    current_date = None
    daily_peak_pnl = 0
    daily_max_drawdown = 0
    daily_start_pnl = 0

    # Trading loop
    for idx in range(50, len(df)):
        row = df.iloc[idx]
        current_price = row['close']
        row_date = row['timestamp'].date()

        # New day initialization
        if current_date != row_date:
            if current_date is not None:
                # Save previous day's data
                daily_data[current_date] = {
                    'start_pnl': daily_start_pnl,
                    'end_pnl': cumulative_pnl,
                    'daily_pnl': cumulative_pnl - daily_start_pnl,
                    'peak_pnl': daily_peak_pnl,
                    'max_drawdown': daily_max_drawdown,
                    'trades': len([t for t in trades if t.get('exit_time', row['timestamp']).date() == current_date])
                }

            # Reset for new day
            current_date = row_date
            daily_start_pnl = cumulative_pnl
            daily_peak_pnl = cumulative_pnl
            daily_max_drawdown = 0

        # Update daily peak and drawdown
        if cumulative_pnl > daily_peak_pnl:
            daily_peak_pnl = cumulative_pnl

        current_drawdown = daily_peak_pnl - cumulative_pnl
        if current_drawdown > daily_max_drawdown:
            daily_max_drawdown = current_drawdown

        # Check for entry signals
        if position == 0 and not np.isnan(row['atr']):
            # Long entry
            if (row['uptrend'] and row['d_falling'] and
                row['stoch_d'] <= STRATEGY_PARAMS["STOCH_LO"]):

                position = contracts
                entry_price = current_price
                atr_value = row['atr']
                stop_loss = current_price - (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"])
                take_profit = current_price + (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])
                trailing_stop = stop_loss
                breakeven_triggered = False

                trades.append({
                    'entry_time': row['timestamp'],
                    'entry_price': entry_price,
                    'direction': 'long',
                    'contracts': contracts,
                    'hard_stop': hard_stop
                })

            # Short entry
            elif (row['downtrend'] and row['d_rising'] and
                  row['stoch_d'] >= STRATEGY_PARAMS["STOCH_HI"]):

                position = -contracts
                entry_price = current_price
                atr_value = row['atr']
                stop_loss = current_price + (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"])
                take_profit = current_price - (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])
                trailing_stop = stop_loss
                breakeven_triggered = False

                trades.append({
                    'entry_time': row['timestamp'],
                    'entry_price': entry_price,
                    'direction': 'short',
                    'contracts': contracts,
                    'hard_stop': hard_stop
                })

        # Check for exit signals
        elif position != 0:
            exit_reason = None
            exit_price = current_price

            # Check stop loss conditions
            if position > 0:  # Long position
                # Hard stop loss (dollar amount)
                hard_stop_price = entry_price - (hard_stop / (contracts * 20))  # MNQ point value
                if current_price <= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif current_price <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif current_price >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            else:  # Short position
                # Hard stop loss (dollar amount)
                hard_stop_price = entry_price + (hard_stop / (contracts * 20))  # MNQ point value
                if current_price >= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif current_price >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif current_price <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            if exit_reason:
                # Calculate P&L
                if position > 0:
                    pnl = (exit_price - entry_price) * abs(position) * 20  # MNQ point value
                else:
                    pnl = (entry_price - exit_price) * abs(position) * 20

                # Add commissions ($14.78 RT + $0.02/contract)
                commissions = 14.78 + (abs(position) * 0.02)
                pnl -= commissions

                # Record trade
                trades[-1].update({
                    'exit_time': row['timestamp'],
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'exit_reason': exit_reason
                })

                cumulative_pnl += pnl

                # Reset position
                position = 0
                entry_price = 0
                stop_loss = 0
                take_profit = 0
                trailing_stop = 0
                breakeven_triggered = False

    # Save last day's data
    if current_date is not None:
        daily_data[current_date] = {
            'start_pnl': daily_start_pnl,
            'end_pnl': cumulative_pnl,
            'daily_pnl': cumulative_pnl - daily_start_pnl,
            'peak_pnl': daily_peak_pnl,
            'max_drawdown': daily_max_drawdown,
            'trades': len([t for t in trades if t.get('exit_time', df.iloc[-1]['timestamp']).date() == current_date])
        }

    # Calculate statistics
    completed_trades = [t for t in trades if 'pnl' in t]
    total_trades = len(completed_trades)

    # Daily analysis
    daily_df = pd.DataFrame.from_dict(daily_data, orient='index')
    daily_df.index = pd.to_datetime(daily_df.index)
    daily_df = daily_df.sort_index()

    # Calculate overall statistics
    max_daily_drawdown = daily_df['max_drawdown'].max()
    avg_daily_drawdown = daily_df['max_drawdown'].mean()
    median_daily_drawdown = daily_df['max_drawdown'].median()

    # Find worst drawdown day
    worst_day = daily_df['max_drawdown'].idxmax()
    worst_day_drawdown = daily_df.loc[worst_day, 'max_drawdown']
    worst_day_pnl = daily_df.loc[worst_day, 'daily_pnl']

    # Trading days with drawdown > $1000
    days_with_large_drawdown = len(daily_df[daily_df['max_drawdown'] > 1000])

    print("\n" + "=" * 80)
    print("📊 DAILY DRAWDOWN ANALYSIS")
    print("=" * 80)
    print(f"Strategy: {contracts} contracts, ${hard_stop} hard stop")
    print(f"Total Trades: {total_trades}")
    print(f"Trading Days: {len(daily_df)}")
    print()

    print("🎯 DRAWDOWN STATISTICS:")
    print(f"  Maximum Daily Drawdown: ${max_daily_drawdown:,.0f}")
    print(f"  Average Daily Drawdown: ${avg_daily_drawdown:,.0f}")
    print(f"  Median Daily Drawdown: ${median_daily_drawdown:,.0f}")
    print(f"  Days with Drawdown > $1,000: {days_with_large_drawdown}")
    print()

    print("📅 WORST DRAWDOWN DAY:")
    print(f"  Date: {worst_day.strftime('%Y-%m-%d')}")
    print(f"  Max Drawdown: ${worst_day_drawdown:,.0f}")
    print(f"  Daily P&L: ${worst_day_pnl:,.0f}")
    print(f"  Trades on Day: {daily_df.loc[worst_day, 'trades']}")
    print()

    print("📈 DRAWDOWN DISTRIBUTION:")
    drawdown_ranges = [
        (0, 500), (500, 1000), (1000, 2000), (2000, 5000), (5000, float('inf'))
    ]

    for min_dd, max_dd in drawdown_ranges:
        if max_dd == float('inf'):
            count = len(daily_df[daily_df['max_drawdown'] >= min_dd])
            label = f"${min_dd:,.0f}+"
        else:
            count = len(daily_df[(daily_df['max_drawdown'] >= min_dd) & (daily_df['max_drawdown'] < max_dd)])
            label = f"${min_dd:,.0f} - ${max_dd:,.0f}"

        percentage = (count / len(daily_df)) * 100
        print(f"  {label}: {count} days ({percentage:.1f}%)")

    # Save detailed daily results
    daily_df.to_csv('mnq_daily_drawdown_analysis.csv')
    print("\n💾 Detailed daily results saved to: mnq_daily_drawdown_analysis.csv")

    return {
        'max_daily_drawdown': max_daily_drawdown,
        'avg_daily_drawdown': avg_daily_drawdown,
        'median_daily_drawdown': median_daily_drawdown,
        'worst_day': worst_day,
        'worst_day_drawdown': worst_day_drawdown,
        'days_with_large_drawdown': days_with_large_drawdown,
        'daily_data': daily_df
    }

# =============================================================================
# MAIN FUNCTION
# =============================================================================

def main():
    print("🔍 MNQ 1min Strategy - Daily Drawdown Analysis")
    print("=" * 80)
    print("Testing multiple hard stop levels with 15 contracts")
    print()

    stops_to_test = [100, 80]

    for stop in stops_to_test:
        print(f"\n🎯 Analyzing ${stop} hard stop:")
        print("-" * 50)

        # Run analysis
        results = analyze_daily_drawdown(contracts=15, hard_stop=stop)

        print(f"✅ Max Daily Drawdown: ${results['max_daily_drawdown']:,.0f}")
        print(f"📅 Worst Day: {results['worst_day'].strftime('%Y-%m-%d')}")
        print()

    print("=" * 80)
    print("✅ All drawdown analyses completed")

    print("\n" + "=" * 80)
    print("✅ ANALYSIS COMPLETE")
    print("=" * 80)
    print(f"📊 Maximum Daily Drawdown: ${results['max_daily_drawdown']:,.0f}")
    print(f"📅 Worst Day: {results['worst_day'].strftime('%Y-%m-%d')}")

if __name__ == "__main__":
    main()