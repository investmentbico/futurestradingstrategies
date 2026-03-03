#!/usr/bin/env python3
"""
Comprehensive Futures Backtest Script - Real Webull Costs & NY Session
=======================================================================

This script performs comprehensive backtesting of the Stoch-D mean reversion strategy
across multiple futures symbols and timeframes with realistic Webull trading costs.

Features:
- Multiple symbols: NQ, MNQ, ES, MES
- Multiple timeframes: 1min, 2min, 5min, 15min
- NY session only: 9:30 AM - 4:00 PM ET
- Real Webull fees: $14.78 RT + $0.02 per contract commission
- Realistic spreads and slippage
- Performance optimization for terminal execution

Setup:
1. Place data files in data/ folder: mnq_1min.csv, mnq_2min.csv, etc.
2. Run: python comprehensive_backtest.py
3. Results saved to results/ folder

Data Format: CSV with columns: time, open, high, low, close (Unix timestamp in seconds)
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
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================

# Symbols and their specifications
SYMBOL_CONFIG = {
    'MNQ': {
        'point_value': 20.0,
        'tick_size': 0.25,
        'tick_value': 5.0,  # $20 * 0.25
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/mnq_1min.csv',
            '2min': 'data/mnq_2min.csv',
            '5min': 'data/mnq_5min.csv',
            '15min': 'data/mnq_15min.csv'
        }
    },
    'NQ': {
        'point_value': 100.0,
        'tick_size': 0.25,
        'tick_value': 25.0,  # $100 * 0.25
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/nq_1min.csv',
            '2min': 'data/nq_2min.csv',
            '5min': 'data/nq_5min.csv',
            '15min': 'data/nq_15min.csv'
        }
    },
    'MES': {
        'point_value': 5.0,
        'tick_size': 0.25,
        'tick_value': 1.25,  # $5 * 0.25
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/mes_1min.csv',
            '2min': 'data/mes_2min.csv',
            '5min': 'data/mes_5min.csv',
            '15min': 'data/mes_15min.csv'
        }
    },
    'ES': {
        'point_value': 50.0,
        'tick_size': 0.25,
        'tick_value': 12.5,  # $50 * 0.25
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/es_1min.csv',
            '2min': 'data/es_2min.csv',
            '5min': 'data/es_5min.csv',
            '15min': 'data/es_15min.csv'
        }
    }
}

# Webull Real Trading Costs (as of 2024)
WEBULL_COSTS = {
    'commission_per_contract': 0.02,  # $0.02 per contract per side
    'routing_fee': 14.78,             # $14.78 RT fee per trade
    'exchange_fees': 0.0,             # Additional exchange fees
    'spread_slippage_ticks': 0.5,     # 0.5 ticks slippage on entries/exits
    'minimum_commission': 0.0         # No minimum commission
}

# NY Session Times (Eastern Time)
NY_SESSION = {
    'start_hour': 9,
    'start_minute': 30,
    'end_hour': 16,
    'end_minute': 0
}

# Strategy Parameters (optimized for each timeframe)
STRATEGY_PARAMS = {
    '1min': {
        "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 1.2, "TP_RR": 1.8,
        "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
    },
    '2min': {
        "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 11, "STOCH_LO": 18, "STOCH_HI": 78, "SL_ATR_MULT": 1.1, "TP_RR": 1.8,
        "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8
    },
    '5min': {
        "EMA_FAST": 34, "EMA_SLOW": 89, "STOCH_K": 14, "STOCH_D": 3, "STOCH_SMT": 3,
        "ATR_LEN": 14, "STOCH_LO": 29, "STOCH_HI": 71, "SL_ATR_MULT": 1.0, "TP_RR": 1.5,
        "TRAIL_ATR_MULT": 0.5, "BE_POINTS": 2.5
    },
    '15min': {
        "EMA_FAST": 45, "EMA_SLOW": 118, "STOCH_K": 18, "STOCH_D": 4, "STOCH_SMT": 3,
        "ATR_LEN": 18, "STOCH_LO": 32, "STOCH_HI": 68, "SL_ATR_MULT": 0.9, "TP_RR": 1.3,
        "TRAIL_ATR_MULT": 0.4, "BE_POINTS": 2.2
    }
}

# Risk Parameters
RISK_PARAMS = {
    "CONTRACTS": 4,
    "MAX_LOSS_TRADE": 4800.0,  # Scaled with contracts (1200 * 4)
    "MAX_LOSS_DAY": 4800.0,    # Scaled with contracts (1200 * 4)
    "STARTING_EQUITY": 300000.0
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

def calc_rsi(prices: np.ndarray, period: int = 14) -> np.ndarray:
    """Calculate RSI indicator"""
    delta = np.diff(prices)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    avg_gain = np.zeros_like(prices)
    avg_loss = np.zeros_like(prices)

    # First RSI value
    if len(gain) >= period:
        avg_gain[period] = np.mean(gain[:period])
        avg_loss[period] = np.mean(loss[:period])

    # Smoothed RSI
    for i in range(period + 1, len(prices)):
        if i < len(avg_gain) and i < len(avg_loss):
            avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i-1]) / period
            avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i-1]) / period

    rs = np.zeros_like(prices)
    mask = avg_loss != 0
    rs[mask] = avg_gain[mask] / avg_loss[mask]
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = 50  # Neutral value for initial periods
    return rsi

# =============================================================================
# TRADING CLASSES
# =============================================================================
class Position:
    def __init__(self):
        self.size = 0
        self.entry_price = 0.0
        self.entry_time = None
        self.sl_price = 0.0
        self.tp_price = 0.0
        self.be_price = 0.0
        self.trail_price = 0.0
        self.highest_price = 0.0
        self.lowest_price = 0.0

class RiskManager:
    def __init__(self):
        self.daily_loss = 0.0
        self.last_reset_date = None

    def reset_daily_loss(self, current_date):
        if self.last_reset_date != current_date.date():
            self.daily_loss = 0.0
            self.last_reset_date = current_date

    def can_trade(self, potential_loss, current_date, max_daily_loss):
        self.reset_daily_loss(current_date)
        return (self.daily_loss + potential_loss) <= max_daily_loss

    def add_loss(self, loss_amount):
        self.daily_loss += loss_amount

# =============================================================================
# COST CALCULATIONS (Webull Real Costs)
# =============================================================================
def calculate_trade_costs(symbol: str, contracts: int, entry_price: float, exit_price: float,
                         config: dict, costs: dict) -> float:
    """
    Calculate total trading costs for a round trip trade using Webull pricing
    """
    # Commission per contract per side
    commission = costs['commission_per_contract'] * contracts * 2  # Round trip

    # Routing fee per trade
    routing_fee = costs['routing_fee']

    # Spread/slippage (0.5 ticks each side)
    tick_size = config['tick_size']
    slippage_ticks = costs['spread_slippage_ticks']
    slippage_cost = slippage_ticks * tick_size * config['tick_value'] * contracts * 2

    total_cost = commission + routing_fee + slippage_cost
    return total_cost

# =============================================================================
# SESSION FILTERING
# =============================================================================
def is_ny_session(timestamp):
    """Check if timestamp is within NY session (9:30 AM - 4:00 PM ET)"""
    eastern = pytz.timezone('US/Eastern')
    if timestamp.tz is None:
        timestamp = eastern.localize(timestamp)
    else:
        timestamp = timestamp.astimezone(eastern)

    hour = timestamp.hour
    minute = timestamp.minute

    session_start = NY_SESSION['start_hour'] * 60 + NY_SESSION['start_minute']
    session_end = NY_SESSION['end_hour'] * 60 + NY_SESSION['end_minute']
    current_minutes = hour * 60 + minute

    return session_start <= current_minutes <= session_end

# =============================================================================
# BACKTEST ENGINE
# =============================================================================
def run_single_backtest(symbol: str, timeframe: str) -> Dict:
    """
    Run backtest for a single symbol and timeframe
    """
    print(f"🔄 Backtesting {symbol} on {timeframe} timeframe...")

    # Get configuration
    symbol_config = SYMBOL_CONFIG[symbol]
    strategy_params = STRATEGY_PARAMS[timeframe]
    data_file = symbol_config['data_files'].get(timeframe)

    if not data_file or not os.path.exists(data_file):
        print(f"⚠️  Data file not found: {data_file}")
        return None

    # Load and prepare data
    df = pd.read_csv(data_file)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter for NY session only
    df['in_session'] = df['timestamp'].apply(is_ny_session)
    df = df[df['in_session']].reset_index(drop=True)

    if len(df) == 0:
        print(f"⚠️  No data in NY session for {symbol} {timeframe}")
        return None

    print(f"📊 Loaded {len(df)} bars ({df['timestamp'].min()} to {df['timestamp'].max()})")

    # Precompute indicators
    c = df['close'].values
    h = df['high'].values
    l = df['low'].values

    ema_f = calc_ema(c, strategy_params["EMA_FAST"])
    ema_s = calc_ema(c, strategy_params["EMA_SLOW"])
    atr = calc_atr(h, l, c, strategy_params["ATR_LEN"])
    k_sm, d_sm = calc_stoch(h, l, c, strategy_params["STOCH_K"], strategy_params["STOCH_D"], strategy_params["STOCH_SMT"])

    # Calculate RSI if parameters exist
    rsi = None
    if "RSI_LEN" in strategy_params:
        rsi = calc_rsi(c, strategy_params["RSI_LEN"])

    # Initialize
    position = Position()
    risk_manager = RiskManager()
    trades = []
    equity_curve = [RISK_PARAMS["STARTING_EQUITY"]]
    equity = RISK_PARAMS["STARTING_EQUITY"]

    # Trading loop
    for idx in range(len(df)):
        ts = df.loc[idx, 'timestamp']
        close = c[idx]
        high = h[idx]
        low = l[idx]

        # Skip if not enough data for indicators
        if idx < max(strategy_params["EMA_SLOW"], strategy_params["STOCH_K"] + strategy_params["STOCH_D"], strategy_params["ATR_LEN"]) + 5:
            continue

        # Build signal dictionary
        sig = {
            "close": close,
            "ema_fast": ema_f[idx],
            "ema_slow": ema_s[idx],
            "atr": atr[idx],
            "stoch_d": d_sm[idx],
            "stoch_d_prev": d_sm[idx-1] if idx >= 1 else d_sm[idx],
            "uptrend": ema_f[idx] > ema_s[idx],
            "downtrend": ema_f[idx] < ema_s[idx],
            "d_rising": d_sm[idx] > d_sm[idx-1] if idx >= 1 else False,
            "d_falling": d_sm[idx] < d_sm[idx-1] if idx >= 1 else False,
            "atr_valid": not np.isnan(atr[idx]),
        }

        # Add RSI signals if available
        if rsi is not None and idx < len(rsi):
            sig["rsi"] = rsi[idx]
            sig["rsi_oversold"] = rsi[idx] < strategy_params.get("RSI_OVERSOLD", 30)
            sig["rsi_overbought"] = rsi[idx] > strategy_params.get("RSI_OVERBOUGHT", 70)

        if not sig or not sig.get("atr_valid"):
            continue

        # Risk management reset
        risk_manager.reset_daily_loss(ts)

        # Position management
        if position.size == 0:
            # Look for entry signals
            long_condition = (sig["uptrend"] and sig["d_falling"] and sig["stoch_d"] <= strategy_params["STOCH_LO"])
            short_condition = (sig["downtrend"] and sig["d_rising"] and sig["stoch_d"] >= strategy_params["STOCH_HI"])

            # Add RSI filter if available
            if "rsi_oversold" in sig:
                long_condition = long_condition and sig["rsi_oversold"]
                short_condition = short_condition and sig["rsi_overbought"]

            if long_condition:
                # Long entry
                entry_price = close + (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                contracts = RISK_PARAMS["CONTRACTS"]

                # Calculate stop loss
                sl_distance = max(atr[idx] * strategy_params["SL_ATR_MULT"],
                                symbol_config['tick_size'] * 2)
                sl_price = entry_price - sl_distance

                # Calculate take profit
                risk_per_contract = sl_distance * symbol_config['point_value']
                tp_distance = sl_distance * strategy_params["TP_RR"]
                tp_price = entry_price + tp_distance

                # Check risk limits
                potential_loss = risk_per_contract * contracts
                if not risk_manager.can_trade(potential_loss, ts, RISK_PARAMS["MAX_LOSS_TRADE"]):
                    continue

                # Enter position
                position.size = contracts
                position.entry_price = entry_price
                position.entry_time = ts
                position.sl_price = sl_price
                position.tp_price = tp_price
                position.be_price = entry_price + (strategy_params["BE_POINTS"] * symbol_config['tick_size'])
                position.trail_price = sl_price
                position.highest_price = entry_price
                position.lowest_price = entry_price

            elif short_condition:
                # Short entry
                entry_price = close - (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                contracts = RISK_PARAMS["CONTRACTS"]

                # Calculate stop loss
                sl_distance = max(atr[idx] * strategy_params["SL_ATR_MULT"],
                                symbol_config['tick_size'] * 2)
                sl_price = entry_price + sl_distance

                # Calculate take profit
                risk_per_contract = sl_distance * symbol_config['point_value']
                tp_distance = sl_distance * strategy_params["TP_RR"]
                tp_price = entry_price - tp_distance

                # Check risk limits
                potential_loss = risk_per_contract * contracts
                if not risk_manager.can_trade(potential_loss, ts, RISK_PARAMS["MAX_LOSS_TRADE"]):
                    continue

                # Enter position
                position.size = -contracts
                position.entry_price = entry_price
                position.entry_time = ts
                position.sl_price = sl_price
                position.tp_price = tp_price
                position.be_price = entry_price - (strategy_params["BE_POINTS"] * symbol_config['tick_size'])
                position.trail_price = sl_price
                position.highest_price = entry_price
                position.lowest_price = entry_price

        else:
            # Manage existing position
            pnl = 0
            exit_price = 0
            exit_reason = ""

            # Update trailing levels
            if position.size > 0:  # Long position
                position.highest_price = max(position.highest_price, high)

                # Trail stop
                trail_distance = atr[idx] * strategy_params["TRAIL_ATR_MULT"]
                new_trail = position.highest_price - trail_distance
                position.trail_price = max(position.trail_price, new_trail)

                # Break even
                if position.highest_price >= position.be_price:
                    position.sl_price = max(position.sl_price, position.entry_price)

                # Exit conditions
                if low <= position.trail_price:
                    exit_price = position.trail_price - (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                    exit_reason = "Trail Stop"
                elif high >= position.tp_price:
                    exit_price = position.tp_price - (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                    exit_reason = "Take Profit"

            else:  # Short position
                position.lowest_price = min(position.lowest_price, low)

                # Trail stop
                trail_distance = atr[idx] * strategy_params["TRAIL_ATR_MULT"]
                new_trail = position.lowest_price + trail_distance
                position.trail_price = min(position.trail_price, new_trail)

                # Break even
                if position.lowest_price <= position.be_price:
                    position.sl_price = min(position.sl_price, position.entry_price)

                # Exit conditions
                if high >= position.trail_price:
                    exit_price = position.trail_price + (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                    exit_reason = "Trail Stop"
                elif low <= position.tp_price:
                    exit_price = position.tp_price + (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                    exit_reason = "Take Profit"

            # Execute exit if conditions met
            if exit_price != 0:
                # Calculate P&L
                if position.size > 0:
                    pnl = (exit_price - position.entry_price) * abs(position.size) * symbol_config['point_value']
                else:
                    pnl = (position.entry_price - exit_price) * abs(position.size) * symbol_config['point_value']

                # Subtract trading costs
                costs = calculate_trade_costs(symbol, abs(position.size), position.entry_price, exit_price, symbol_config, WEBULL_COSTS)
                pnl -= costs

                # Update equity
                equity += pnl
                equity_curve.append(equity)

                # Record trade
                trade = {
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'entry_time': position.entry_time,
                    'exit_time': ts,
                    'direction': 'Long' if position.size > 0 else 'Short',
                    'entry_price': position.entry_price,
                    'exit_price': exit_price,
                    'contracts': abs(position.size),
                    'pnl': pnl,
                    'costs': costs,
                    'exit_reason': exit_reason,
                    'holding_period': (ts - position.entry_time).total_seconds() / 60  # minutes
                }
                trades.append(trade)

                # Update risk manager
                if pnl < 0:
                    risk_manager.add_loss(-pnl)

                # Reset position
                position = Position()

    # Calculate final statistics
    if len(trades) == 0:
        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'trades': 0,
            'win_rate': 0,
            'total_pnl': 0,
            'max_drawdown': 0,
            'profit_factor': 0,
            'avg_trade': 0,
            'sharpe_ratio': 0
        }

    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] < 0]

    total_pnl = sum(t['pnl'] for t in trades)
    win_rate = len(winning_trades) / len(trades) * 100

    gross_profit = sum(t['pnl'] for t in winning_trades)
    gross_loss = abs(sum(t['pnl'] for t in losing_trades))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Calculate drawdown
    peak = RISK_PARAMS["STARTING_EQUITY"]
    max_drawdown = 0
    for equity in equity_curve:
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak * 100
        max_drawdown = max(max_drawdown, drawdown)

    # Calculate Sharpe ratio (simplified)
    returns = np.diff(equity_curve) / equity_curve[:-1]
    if len(returns) > 1 and np.std(returns) > 0:
        sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252 * len(df) / len(returns))  # Annualized
    else:
        sharpe_ratio = 0

    results = {
        'symbol': symbol,
        'timeframe': timeframe,
        'trades': len(trades),
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'max_drawdown': max_drawdown,
        'profit_factor': profit_factor,
        'avg_trade': total_pnl / len(trades),
        'sharpe_ratio': sharpe_ratio,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'trades_data': trades,
        'equity_curve': equity_curve
    }

    print(f"✅ {symbol} {timeframe}: {len(trades)} trades, Win Rate: {win_rate:.1f}%, P&L: ${total_pnl:,.0f}")
    return results

# =============================================================================
# RESULTS ANALYSIS AND REPORTING
# =============================================================================
def generate_report(all_results: List[Dict]):
    """Generate comprehensive report from all backtest results"""
    print("\n" + "="*80)
    print("🎯 COMPREHENSIVE BACKTEST RESULTS - WEBULL REAL COSTS")
    print("="*80)

    # Filter out None results
    valid_results = [r for r in all_results if r is not None]

    if not valid_results:
        print("❌ No valid results found!")
        return

    # Group by symbol
    symbol_results = {}
    for result in valid_results:
        symbol = result['symbol']
        if symbol not in symbol_results:
            symbol_results[symbol] = []
        symbol_results[symbol].append(result)

    # Find best performing combinations
    best_by_symbol = {}
    best_overall = None
    best_pnl = float('-inf')

    for symbol, results in symbol_results.items():
        # Find best timeframe for this symbol
        best_for_symbol = max(results, key=lambda x: x['total_pnl'])
        best_by_symbol[symbol] = best_for_symbol

        if best_for_symbol['total_pnl'] > best_pnl:
            best_pnl = best_for_symbol['total_pnl']
            best_overall = best_for_symbol

        print(f"\n📊 {symbol} Results:")
        print("-" * 40)
        for result in sorted(results, key=lambda x: x['total_pnl'], reverse=True):
            print(f"  {result['timeframe']:>4}: {result['trades']:3d} trades, "
                  f"Win: {result['win_rate']:5.1f}%, "
                  f"P&L: ${result['total_pnl']:8,.0f}, "
                  f"PF: {result['profit_factor']:4.2f}, "
                  f"DD: {result['max_drawdown']:5.1f}%")

    print(f"\n🏆 BEST PERFORMERS BY SYMBOL:")
    print("-" * 50)
    for symbol, result in best_by_symbol.items():
        print(f"  {symbol}: {result['timeframe']} timeframe - "
              f"${result['total_pnl']:,.0f} P&L, "
              f"{result['win_rate']:.1f}% win rate")

    if best_overall:
        print(f"\n🎯 BEST OVERALL PERFORMANCE:")
        print("-" * 50)
        print(f"  {best_overall['symbol']} on {best_overall['timeframe']} timeframe")
        print(f"  Trades: {best_overall['trades']}")
        print(f"  Win Rate: {best_overall['win_rate']:.1f}%")
        print(f"  Total P&L: ${best_overall['total_pnl']:,.0f}")
        print(f"  Profit Factor: {best_overall['profit_factor']:.2f}")
        print(f"  Max Drawdown: {best_overall['max_drawdown']:.1f}%")
        print(f"  Average Trade: ${best_overall['avg_trade']:.0f}")
        print(f"  Sharpe Ratio: {best_overall['sharpe_ratio']:.2f}")

        # Cost analysis
        total_costs = sum(t['costs'] for t in best_overall['trades_data'])
        print(f"  Total Trading Costs: ${total_costs:,.0f}")
        print(f"  Cost per Trade: ${total_costs/best_overall['trades']:,.0f}")

    # Save detailed results
    os.makedirs('results', exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Save summary CSV
    summary_data = []
    for result in valid_results:
        summary_data.append({
            'Symbol': result['symbol'],
            'Timeframe': result['timeframe'],
            'Trades': result['trades'],
            'Win_Rate': result['win_rate'],
            'Total_PnL': result['total_pnl'],
            'Profit_Factor': result['profit_factor'],
            'Max_Drawdown': result['max_drawdown'],
            'Avg_Trade': result['avg_trade'],
            'Sharpe_Ratio': result['sharpe_ratio']
        })

    summary_df = pd.DataFrame(summary_data)
    summary_df.to_csv(f'results/backtest_summary_{timestamp}.csv', index=False)

    # Save best equity curve
    if best_overall and 'equity_curve' in best_overall:
        plt.figure(figsize=(12, 8))
        plt.plot(best_overall['equity_curve'])
        plt.title(f'Equity Curve - {best_overall["symbol"]} {best_overall["timeframe"]}\n'
                 f'P&L: ${best_overall["total_pnl"]:,.0f}, Win Rate: {best_overall["win_rate"]:.1f}%')
        plt.xlabel('Trades')
        plt.ylabel('Equity ($)')
        plt.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.savefig(f'results/best_equity_curve_{timestamp}.png', dpi=150, bbox_inches='tight')
        plt.close()

    print(f"\n💾 Results saved to results/ folder")
    print(f"   - Summary CSV: backtest_summary_{timestamp}.csv")
    print(f"   - Equity Chart: best_equity_curve_{timestamp}.png")

# =============================================================================
# MAIN EXECUTION
# =============================================================================
def main():
    parser = argparse.ArgumentParser(description='Comprehensive Futures Backtest with Webull Costs')
    parser.add_argument('--symbols', nargs='+', default=['MNQ', 'NQ', 'ES', 'MES'],
                       help='Symbols to test (default: MNQ NQ ES MES)')
    parser.add_argument('--timeframes', nargs='+', default=['1min', '2min', '5min', '15min'],
                       help='Timeframes to test (default: 1min 2min 5min 15min)')
    parser.add_argument('--parallel', action='store_true',
                       help='Run backtests in parallel for faster execution')
    parser.add_argument('--max-workers', type=int, default=4,
                       help='Maximum parallel workers (default: 4)')

    args = parser.parse_args()

    print("🚀 STARTING COMPREHENSIVE FUTURES BACKTEST")
    print("=" * 50)
    print(f"Symbols: {', '.join(args.symbols)}")
    print(f"Timeframes: {', '.join(args.timeframes)}")
    print(f"Webull Costs: ${WEBULL_COSTS['routing_fee']:.2f} RT + ${WEBULL_COSTS['commission_per_contract']:.2f}/contract")
    print(f"NY Session Only: {NY_SESSION['start_hour']}:{NY_SESSION['start_minute']:02d} - {NY_SESSION['end_hour']}:{NY_SESSION['end_minute']:02d} ET")
    print(f"Contracts per Trade: {RISK_PARAMS['CONTRACTS']}")
    print(f"Starting Equity: ${RISK_PARAMS['STARTING_EQUITY']:,.0f}")
    print()

    start_time = time.time()

    # Generate all combinations
    combinations = [(symbol, tf) for symbol in args.symbols for tf in args.timeframes]
    print(f"Total backtests to run: {len(combinations)}")

    # Run backtests
    all_results = []

    if args.parallel and len(combinations) > 1:
        print("🔄 Running in parallel mode...")
        with ProcessPoolExecutor(max_workers=min(args.max_workers, len(combinations))) as executor:
            futures = [executor.submit(run_single_backtest, symbol, tf) for symbol, tf in combinations]
            for future in futures:
                result = future.result()
                if result:
                    all_results.append(result)
    else:
        print("🔄 Running in sequential mode...")
        for symbol, timeframe in combinations:
            result = run_single_backtest(symbol, timeframe)
            if result:
                all_results.append(result)

    # Generate report
    generate_report(all_results)

    elapsed_time = time.time() - start_time
    print(f"\n⏱️  Total execution time: {elapsed_time:.1f} seconds")
    print("✅ Backtest complete!")

if __name__ == "__main__":
    main()