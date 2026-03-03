#!/usr/bin/env python3
"""
AMP FUTURES MNQ Backtest - FULL HISTORICAL DATA with Real AMP Fees
=======================================================================

This script performs backtesting of the winning MNQ strategy using AMP FUTURES
real commission rates, spreads, and slippage for ALL available historical data.

AMP FUTURES Fee Structure (Ultra Low Pricing):
- Commission: $0.015 per contract (round trip)
- Routing Fee: $8.50 per trade
- Spread/Slippage: 0.25 ticks
- Exchange Fees: $0.00

Strategy: Winning MNQ Stochastic D Strategy
- EMA Fast: 34, Slow: 89
- Stochastic: K=14, D=3, Smooth=3
- ATR: 14-period for stops
- Entry: Stoch D falling below 30 (long) / rising above 70 (short)
- Risk: 15 contracts, $80 hard stop, ATR 1.0x secondary stop

Data: ALL Available Historical Data (~6 months)
"""

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import pytz
import time
import logging
import argparse
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# AMP FUTURES COST STRUCTURE (Ultra Low Pricing)
# =============================================================================

AMP_COSTS = {
    'commission_per_contract': 0.015,  # $0.015 per contract per side ($0.03 round trip)
    'routing_fee': 8.50,               # $8.50 RT fee (much lower than Webull's $14.78)
    'exchange_fees': 0.0,              # No additional exchange fees
    'spread_slippage_ticks': 0.25,     # 0.25 ticks slippage (better than Webull's 0.5)
    'minimum_commission': 0.0          # No minimum commission
}

# =============================================================================
# STRATEGY PARAMETERS (WINNING CONFIGURATION)
# =============================================================================

STRATEGY_PARAMS = {
    "EMA_FAST": 34, "EMA_SLOW": 89,
    "STOCH_K": 14, "STOCH_D": 3, "STOCH_SMT": 3,
    "ATR_LEN": 14,
    "STOCH_LO": 30, "STOCH_HI": 70,
    "SL_ATR_MULT": 1.0,
    "TP_RR": 1.5,
    "CONTRACTS": 15,
    "HARD_STOP_DOLLARS": 80.0,
    "POINT_VALUE": 20.0,
    "TICK_SIZE": 0.25,
    "MAX_LOSS_TRADE": 1200.0,
    "MAX_LOSS_DAY": 2400.0,
    "STARTING_EQUITY": 100000.0
}

# =============================================================================
# DATA FILTERING - ALL HISTORICAL DATA
# =============================================================================

def filter_last_4_months(df: pd.DataFrame) -> pd.DataFrame:
    """Use ALL available historical data (no filtering)"""
    df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)

    # Use ALL available data (no filtering)
    df = df.sort_values('timestamp').reset_index(drop=True)

    start_date = df['timestamp'].min()
    end_date = df['timestamp'].max()

    print(f"📅 Using ALL available data: {start_date.date()} to {end_date.date()}")
    print(f"📊 Data points: {len(df)}")
    print(f"📅 Trading period: {(end_date - start_date).days} days")

    return df

# =============================================================================
# TECHNICAL INDICATORS
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
        out[i] = (out[i-1] * (period-1) + tr[i]) / period
    return out

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
    n = len(close)
    raw_k = np.empty(n, dtype=float)
    for i in range(n):
        lo = low[max(0, i - k_period + 1) : i + 1].min()
        hi = high[max(0, i - k_period + 1) : i + 1].max()
        rng = hi - lo
        raw_k[i] = 100.0 * (close[i] - lo) / rng if rng > 1e-9 else 50.0
    k_smooth = pd.Series(raw_k).rolling(smooth).mean().values
    d_smooth = pd.Series(k_smooth).rolling(d_period).mean().values
    return k_smooth, d_smooth

# =============================================================================
# SESSION FILTERING (NY Session Only)
# =============================================================================

def in_trading_session(timestamp: datetime) -> bool:
    """Check if timestamp is within NY trading session (9:30 AM - 4:00 PM ET)"""
    ny_tz = pytz.timezone('America/New_York')
    ny_time = timestamp.astimezone(ny_tz)

    # Trading hours: 9:30 AM - 4:00 PM ET
    start_time = ny_time.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = ny_time.replace(hour=16, minute=0, second=0, microsecond=0)

    return start_time <= ny_time <= end_time

# =============================================================================
# AMP COST CALCULATION
# =============================================================================

def calculate_amp_costs(contracts: int, entry_price: float, exit_price: float, tick_size: float) -> float:
    """
    Calculate total trading costs using AMP FUTURES fee structure
    """
    # Commission per contract per side
    commission = AMP_COSTS['commission_per_contract'] * contracts * 2  # Round trip

    # Routing fee per trade
    routing_fee = AMP_COSTS['routing_fee']

    # Spread/slippage (0.25 ticks each side for round trip)
    slippage_ticks = AMP_COSTS['spread_slippage_ticks'] * 2
    slippage_cost = slippage_ticks * tick_size * contracts * STRATEGY_PARAMS['POINT_VALUE']

    # Total cost
    total_cost = commission + routing_fee + slippage_cost

    return total_cost

# =============================================================================
# BACKTEST ENGINE
# =============================================================================

def run_amp_backtest(data_file: str) -> Dict:
    """Run backtest with AMP fees and ALL available historical data"""

    print("🏆 AMP FUTURES MNQ BACKTEST")
    print("=" * 50)
    print("📊 ALL Historical Data | Ultra Low AMP Fees")
    print(f"💰 Commission: ${AMP_COSTS['commission_per_contract']:.3f}/contract")
    print(f"🚚 Routing Fee: ${AMP_COSTS['routing_fee']:.2f}/trade")
    print(f"📈 Slippage: {AMP_COSTS['spread_slippage_ticks']} ticks")
    print()

    # Load and filter data
    df = pd.read_csv(data_file)
    df = filter_last_4_months(df)

    if len(df) < 200:
        raise ValueError("Insufficient data for backtest")

    # Calculate indicators
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values

    ema_fast = calc_ema(closes, STRATEGY_PARAMS["EMA_FAST"])
    ema_slow = calc_ema(closes, STRATEGY_PARAMS["EMA_SLOW"])
    atr = calc_atr(highs, lows, closes, STRATEGY_PARAMS["ATR_LEN"])
    k_sm, d_sm = calc_stoch(highs, lows, closes, STRATEGY_PARAMS["STOCH_K"],
                           STRATEGY_PARAMS["STOCH_D"], STRATEGY_PARAMS["STOCH_SMT"])

    # Trading simulation
    position = 0
    entry_price = 0
    hard_stop_price = 0
    atr_stop_price = 0
    take_profit_price = 0

    trades = []
    equity = STRATEGY_PARAMS["STARTING_EQUITY"]
    daily_pnl = {}
    total_costs = 0

    for i in range(max(STRATEGY_PARAMS["EMA_SLOW"], 50), len(df)):
        current_price = closes[i]
        timestamp = df['timestamp'][i]

        # Skip if not in trading session
        if not in_trading_session(timestamp):
            continue

        # Skip if indicators not ready
        if (np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]) or
            np.isnan(d_sm[i]) or np.isnan(atr[i])):
            continue

        # Check entry signals
        uptrend = ema_fast[i] > ema_slow[i]
        downtrend = ema_fast[i] < ema_slow[i]
        d_falling = d_sm[i] < d_sm[i-1] if i > 0 else False
        d_rising = d_sm[i] > d_sm[i-1] if i > 0 else False

        # Long entry
        if (position == 0 and uptrend and d_falling and d_sm[i] <= STRATEGY_PARAMS["STOCH_LO"]):
            # Apply slippage to entry price
            slippage = AMP_COSTS['spread_slippage_ticks'] * STRATEGY_PARAMS['TICK_SIZE']
            entry_price = current_price + slippage

            position = STRATEGY_PARAMS["CONTRACTS"]
            hard_stop_price = entry_price - (STRATEGY_PARAMS["HARD_STOP_DOLLARS"] / (position * STRATEGY_PARAMS["POINT_VALUE"]))
            atr_stop_price = entry_price - (atr[i] * STRATEGY_PARAMS["SL_ATR_MULT"])
            take_profit_price = entry_price + (atr[i] * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

            trades.append({
                'type': 'long',
                'entry_time': timestamp,
                'entry_price': entry_price,
                'contracts': position,
                'hard_stop': hard_stop_price,
                'atr_stop': atr_stop_price,
                'take_profit': take_profit_price
            })

        # Short entry
        elif (position == 0 and downtrend and d_rising and d_sm[i] >= STRATEGY_PARAMS["STOCH_HI"]):
            # Apply slippage to entry price
            slippage = AMP_COSTS['spread_slippage_ticks'] * STRATEGY_PARAMS['TICK_SIZE']
            entry_price = current_price - slippage

            position = -STRATEGY_PARAMS["CONTRACTS"]
            hard_stop_price = entry_price + (STRATEGY_PARAMS["HARD_STOP_DOLLARS"] / (abs(position) * STRATEGY_PARAMS["POINT_VALUE"]))
            atr_stop_price = entry_price + (atr[i] * STRATEGY_PARAMS["SL_ATR_MULT"])
            take_profit_price = entry_price - (atr[i] * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

            trades.append({
                'type': 'short',
                'entry_time': timestamp,
                'entry_price': entry_price,
                'contracts': abs(position),
                'hard_stop': hard_stop_price,
                'atr_stop': atr_stop_price,
                'take_profit': take_profit_price
            })

        # Exit logic
        elif position != 0:
            exit_trade = False
            exit_reason = ""
            exit_price = current_price

            if position > 0:  # Long position
                if current_price <= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = "hard_stop"
                    exit_trade = True
                elif current_price <= atr_stop_price:
                    exit_price = atr_stop_price
                    exit_reason = "atr_stop"
                    exit_trade = True
                elif current_price >= take_profit_price:
                    exit_price = take_profit_price
                    exit_reason = "take_profit"
                    exit_trade = True
            else:  # Short position
                if current_price >= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = "hard_stop"
                    exit_trade = True
                elif current_price >= atr_stop_price:
                    exit_price = atr_stop_price
                    exit_reason = "atr_stop"
                    exit_trade = True
                elif current_price <= take_profit_price:
                    exit_price = take_profit_price
                    exit_reason = "take_profit"
                    exit_trade = True

            if exit_trade:
                # Calculate P&L before costs
                if position > 0:
                    gross_pnl = (exit_price - entry_price) * abs(position) * STRATEGY_PARAMS["POINT_VALUE"]
                else:
                    gross_pnl = (entry_price - exit_price) * abs(position) * STRATEGY_PARAMS["POINT_VALUE"]

                # Calculate AMP costs
                costs = calculate_amp_costs(abs(position), entry_price, exit_price, STRATEGY_PARAMS['TICK_SIZE'])
                total_costs += costs
                net_pnl = gross_pnl - costs

                # Update equity
                equity += net_pnl

                # Update trade record
                last_trade = trades[-1]
                last_trade.update({
                    'exit_time': timestamp,
                    'exit_price': exit_price,
                    'exit_reason': exit_reason,
                    'gross_pnl': gross_pnl,
                    'costs': costs,
                    'net_pnl': net_pnl,
                    'equity': equity
                })

                # Reset position
                position = 0
                entry_price = 0

                # Track daily P&L
                day = timestamp.date()
                if day not in daily_pnl:
                    daily_pnl[day] = 0
                daily_pnl[day] += net_pnl

    # Calculate performance metrics
    if trades:
        trade_df = pd.DataFrame(trades)
        winning_trades = trade_df[trade_df['net_pnl'] > 0]
        losing_trades = trade_df[trade_df['net_pnl'] < 0]

        total_trades = len(trade_df)
        win_rate = len(winning_trades) / total_trades * 100
        total_gross_pnl = trade_df['gross_pnl'].sum()
        total_net_pnl = trade_df['net_pnl'].sum()
        avg_win = winning_trades['net_pnl'].mean() if len(winning_trades) > 0 else 0
        avg_loss = abs(losing_trades['net_pnl'].mean()) if len(losing_trades) > 0 else 0
        profit_factor = abs(winning_trades['net_pnl'].sum() / losing_trades['net_pnl'].sum()) if len(losing_trades) > 0 else float('inf')
        max_drawdown = (trade_df['equity'] - trade_df['equity'].cummax()).min()

        # Cost analysis
        avg_cost_per_trade = total_costs / total_trades
        cost_percentage = (total_costs / abs(total_gross_pnl)) * 100 if total_gross_pnl != 0 else 0

        results = {
            'total_trades': total_trades,
            'win_rate': win_rate,
            'total_gross_pnl': total_gross_pnl,
            'total_net_pnl': total_net_pnl,
            'total_costs': total_costs,
            'avg_cost_per_trade': avg_cost_per_trade,
            'cost_percentage': cost_percentage,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_drawdown': max_drawdown,
            'final_equity': equity,
            'daily_pnl': daily_pnl,
            'trades': trades
        }

        return results
    else:
        return {'error': 'No trades generated'}

# =============================================================================
# RESULTS DISPLAY
# =============================================================================

def display_results(results: Dict):
    """Display backtest results"""
    if 'error' in results:
        print(f"❌ Error: {results['error']}")
        return

    print("\n📊 AMP FUTURES MNQ BACKTEST RESULTS")
    print("=" * 60)
    print("🏆 WINNING STRATEGY | ALL Historical Data | Real AMP Fees")
    print()

    print("📈 PERFORMANCE METRICS:")
    print(f"Total Trades: {results['total_trades']}")
    print(f"Win Rate: {results['win_rate']:.1f}%")
    print(f"Gross P&L: ${results['total_gross_pnl']:.2f}")
    print(f"Net P&L: ${results['total_net_pnl']:.2f}")
    print(f"Profit Factor: {results['profit_factor']:.2f}")
    print(f"Avg Win: ${results['avg_win']:.2f}")
    print(f"Avg Loss: ${results['avg_loss']:.2f}")
    print(f"Max Drawdown: ${results['max_drawdown']:.2f}")

    print("\n💰 COST ANALYSIS:")
    print(f"Total Costs: ${results['total_costs']:.2f}")
    print(f"Avg Cost/Trade: ${results['avg_cost_per_trade']:.2f}")
    print(f"Cost % of Gross P&L: {results['cost_percentage']:.2f}%")

    print("\n🎯 RISK METRICS:")
    print(f"Max Drawdown: ${results['max_drawdown']:.2f}")
    print(f"Final Equity: ${results['final_equity']:.2f}")

    print("\n🏆 FINAL RESULTS:")
    print(f"Net Return: ${results['total_net_pnl']:.2f}")
    print(f"Return %: {((results['final_equity'] / STRATEGY_PARAMS['STARTING_EQUITY']) - 1) * 100:.2f}%")
    # Daily analysis
    daily_returns = list(results['daily_pnl'].values())
    if daily_returns:
        winning_days = sum(1 for pnl in daily_returns if pnl > 0)
        total_days = len(daily_returns)
        daily_win_rate = winning_days / total_days * 100 if total_days > 0 else 0

        print("\n📅 DAILY ANALYSIS:")
        print(f"Trading Days: {total_days}")
        print(f"Daily Win Rate: {daily_win_rate:.1f}%")
        print(f"Avg Daily P&L: ${sum(daily_returns)/len(daily_returns):.2f}")
        print(f"Best Day: ${max(daily_returns):.2f}")
        print(f"Worst Day: ${min(daily_returns):.2f}")
    # Trade analysis
    trades = results['trades']
    if trades:
        exit_reasons = {}
        for trade in trades:
            reason = trade.get('exit_reason', 'unknown')
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        print("\n🎪 TRADE ANALYSIS:")
        for reason, count in exit_reasons.items():
            pct = count / len(trades) * 100
            print(f"{reason}: {count} trades ({pct:.1f}%)")

    print("\n✅ Backtest completed with AMP FUTURES real fees!")

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='AMP FUTURES MNQ Backtest')
    parser.add_argument('--data', default='data/mnq_1min.csv', help='Data file path')
    parser.add_argument('--output', help='Output results file')
    args = parser.parse_args()

    try:
        results = run_amp_backtest(args.data)
        display_results(results)

        if args.output:
            # Save detailed results
            output_data = {
                'results': {k: v for k, v in results.items() if k not in ['daily_pnl', 'trades']},
                'daily_pnl': results.get('daily_pnl', {}),
                'trades': results.get('trades', [])
            }

            import json
            with open(args.output, 'w') as f:
                json.dump(output_data, f, indent=2, default=str)
            print(f"\n💾 Results saved to: {args.output}")

    except Exception as e:
        print(f"❌ Backtest failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()