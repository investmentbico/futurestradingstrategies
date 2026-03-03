#!/usr/bin/env python3
"""
MNQ 1min Strategy Comprehensive Backtest
Testing different contract sizes and hard stop loss levels
Last 6 months data analysis
"""

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
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
# BACKTEST ENGINE
# =============================================================================

def run_mnq_backtest(contracts: int, hard_stop: float, data_file: str = 'data/mnq_1min.csv'):
    """Run backtest with specific parameters"""

    # Load data
    df = pd.read_csv(data_file)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter last 6 months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=6)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)

    print(f"📊 Testing last 6 months: {start_date.date()} to {end_date.date()}")
    print(f"📈 Data points: {len(df)} bars")

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
    total_pnl = 0
    cumulative_pnl = 0
    cumulative_pnl_history = []
    daily_pnls = {}
    daily_pnl = 0
    daily_reset_date = df['timestamp'].iloc[0].date()

    # Trading loop
    for idx in range(50, len(df)):
        row = df.iloc[idx]
        current_price = row['close']

        # Reset daily P&L
        current_date = row['timestamp'].date()
        if current_date != daily_reset_date:
            daily_pnls[daily_reset_date] = daily_pnl
            daily_pnl = 0
            daily_reset_date = current_date

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

                # Add spread/slippage cost ($0.50 per contract RT for worse case)
                spread_cost = abs(position) * 0.50
                pnl -= spread_cost

                # Record trade
                trades[-1].update({
                    'exit_time': row['timestamp'],
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'exit_reason': exit_reason
                })

                total_pnl += pnl
                daily_pnl += pnl
                cumulative_pnl += pnl
                cumulative_pnl_history.append(cumulative_pnl)

                # Reset position
                position = 0
                entry_price = 0
                stop_loss = 0
                take_profit = 0
                trailing_stop = 0
                breakeven_triggered = False

    # Calculate statistics
    completed_trades = [t for t in trades if 'pnl' in t]
    total_trades = len(completed_trades)
    winning_trades = len([t for t in completed_trades if t['pnl'] > 0])
    losing_trades = len([t for t in completed_trades if t['pnl'] <= 0])
    win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0

    avg_win = np.mean([t['pnl'] for t in completed_trades if t['pnl'] > 0]) if winning_trades > 0 else 0
    avg_loss = np.mean([t['pnl'] for t in completed_trades if t['pnl'] <= 0]) if losing_trades > 0 else 0

    # Profit factor
    total_wins = sum([t['pnl'] for t in completed_trades if t['pnl'] > 0])
    total_losses = abs(sum([t['pnl'] for t in completed_trades if t['pnl'] <= 0]))
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

    # Calculate testing period
    trading_days = len(df['timestamp'].dt.date.unique())

    # Record last day's P&L
    daily_pnls[daily_reset_date] = daily_pnl

    # Calculate drawdowns
    max_drawdown = 0
    if cumulative_pnl_history:
        peak = cumulative_pnl_history[0]
        for pnl in cumulative_pnl_history:
            if pnl > peak:
                peak = pnl
            drawdown = peak - pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown

    max_daily_drawdown = min(daily_pnls.values()) if daily_pnls else 0

    return {
        'contracts': contracts,
        'hard_stop': hard_stop,
        'total_trades': total_trades,
        'winning_trades': winning_trades,
        'losing_trades': losing_trades,
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'trading_days': trading_days,
        'start_date': start_date,
        'end_date': end_date,
        'max_drawdown': max_drawdown,
        'max_daily_drawdown': max_daily_drawdown,
        'trades': completed_trades
    }

# =============================================================================
# MAIN TESTING FUNCTION
# =============================================================================

def main():
    print("🚀 MNQ 1min Comprehensive Backtest")
    print("=" * 80)
    print("Testing different contract sizes and hard stop loss levels")
    print("Strategy: EMA 21/55, Stoch 25/75, ATR 9, SL 2.0 ATR, TP 1.8 RR")
    print("Data: Last 6 months")
    print()

    # Test parameters
    contract_sizes = [1, 2, 3]
    hard_stops = [20, 30]

    results = []

    print("🔬 Testing combinations...")
    print("-" * 80)

    for contracts in contract_sizes:
        for hard_stop in hard_stops:
            print(f"\n📊 Testing: {contracts} contracts, ${hard_stop} hard stop")

            result = run_mnq_backtest(contracts, hard_stop)
            results.append(result)

            print(f"   ✅ {result['total_trades']} trades over {result['trading_days']} days")
            print(f"   📈 Win Rate: {result['win_rate']:.1f}%")
            print(f"   💰 Total P&L: ${result['total_pnl']:,.0f}")
            print(f"   📊 Profit Factor: {result['profit_factor']:.2f}")
            print(f"   🎯 Avg Win/Loss: ${result['avg_win']:,.0f} / ${result['avg_loss']:,.0f}")
            print(f"   📉 Max Drawdown: ${result['max_drawdown']:,.0f}")
            print(f"   📅 Max Daily Drawdown: ${result['max_daily_drawdown']:,.0f}")

    # Sort and display top results
    print("\n" + "=" * 80)
    print("🏆 TOP PERFORMERS BY TOTAL P&L")
    print("=" * 80)

    sorted_by_pnl = sorted(results, key=lambda x: x['total_pnl'], reverse=True)

    for i, result in enumerate(sorted_by_pnl[:10]):
        print(f"\n{i+1}. ${result['total_pnl']:,.0f} P&L")
        print(f"   {result['contracts']} contracts, ${result['hard_stop']} hard stop")
        print(f"   {result['total_trades']} trades, {result['win_rate']:.1f}% win rate")
        print(f"   Profit Factor: {result['profit_factor']:.2f}")

    print("\n" + "=" * 80)
    print("🎯 TOP PERFORMERS BY WIN RATE")
    print("=" * 80)

    sorted_by_winrate = sorted(results, key=lambda x: x['win_rate'], reverse=True)

    for i, result in enumerate(sorted_by_winrate[:10]):
        print(f"\n{i+1}. {result['win_rate']:.1f}% Win Rate")
        print(f"   {result['contracts']} contracts, ${result['hard_stop']} hard stop")
        print(f"   ${result['total_pnl']:,.0f} P&L, {result['total_trades']} trades")
        print(f"   Profit Factor: {result['profit_factor']:.2f}")

    print("\n" + "=" * 80)
    print("💎 TOP PERFORMERS BY PROFIT FACTOR")
    print("=" * 80)

    sorted_by_pf = sorted(results, key=lambda x: x['profit_factor'], reverse=True)

    for i, result in enumerate(sorted_by_pf[:10]):
        print(f"\n{i+1}. {result['profit_factor']:.2f} Profit Factor")
        print(f"   {result['contracts']} contracts, ${result['hard_stop']} hard stop")
        print(f"   ${result['total_pnl']:,.0f} P&L, {result['win_rate']:.1f}% win rate")
        print(f"   {result['total_trades']} trades")

    # Save detailed results
    print("\n💾 Saving detailed results to CSV...")

    detailed_results = []
    for result in results:
        row = {
            'contracts': result['contracts'],
            'hard_stop': result['hard_stop'],
            'total_trades': result['total_trades'],
            'winning_trades': result['winning_trades'],
            'losing_trades': result['losing_trades'],
            'win_rate': result['win_rate'],
            'total_pnl': result['total_pnl'],
            'avg_win': result['avg_win'],
            'avg_loss': result['avg_loss'],
            'profit_factor': result['profit_factor'],
            'trading_days': result['trading_days'],
            'max_drawdown': result['max_drawdown'],
            'max_daily_drawdown': result['max_daily_drawdown'],
            'start_date': result['start_date'],
            'end_date': result['end_date']
        }
        detailed_results.append(row)

    results_df = pd.DataFrame(detailed_results)
    results_df.to_csv('mnq_comprehensive_backtest_results.csv', index=False)

    print("✅ Results saved to: mnq_comprehensive_backtest_results.csv")

    # Summary statistics
    print("\n" + "=" * 80)
    print("📊 SUMMARY STATISTICS")
    print("=" * 80)

    print(f"Total combinations tested: {len(results)}")
    print(f"Date range: {results[0]['start_date'].date()} to {results[0]['end_date'].date()}")
    print(f"Trading days: {results[0]['trading_days']}")

    best_pnl = max(results, key=lambda x: x['total_pnl'])
    best_winrate = max(results, key=lambda x: x['win_rate'])
    best_pf = max(results, key=lambda x: x['profit_factor'])

    print("\n🎯 BEST RESULTS:")
    print(f"  Highest P&L: ${best_pnl['total_pnl']:,.0f} ({best_pnl['contracts']} contracts, ${best_pnl['hard_stop']} stop)")
    print(f"  Highest Win Rate: {best_winrate['win_rate']:.1f}% ({best_winrate['contracts']} contracts, ${best_winrate['hard_stop']} stop)")
    print(f"  Highest Profit Factor: {best_pf['profit_factor']:.2f} ({best_pf['contracts']} contracts, ${best_pf['hard_stop']} stop)")

if __name__ == "__main__":
    main()