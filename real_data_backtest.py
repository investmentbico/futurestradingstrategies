#!/usr/bin/env python3
"""
REAL MARKET DATA BACKTEST - Using Original CME Data
==================================================

Tests the Stoch-D strategy on real CME futures data instead of synthetic data.
Uses the original CME_MINI_NQ1___1-3.csv file for authentic market conditions.

Strategy: Stoch-D Mean Reversion with EMA Trend Filter
Data: Real CME MNQ futures data
Target: 200+ trades with positive P&L
"""

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import pytz
import time
from typing import Dict, List, Tuple
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - REAL MARKET DATA
# =============================================================================

WEBULL_COSTS = {
    'commission_per_contract': 0.02,
    'routing_fee': 14.78,
    'exchange_fees': 0.0,
    'spread_slippage_ticks': 0.5,
    'minimum_commission': 0.0
}

NY_SESSION = {
    'start_hour': 9,
    'start_minute': 30,
    'end_hour': 16,
    'end_minute': 0
}

# OPTIMIZED PARAMETERS BASED ON ES SUCCESS
STRATEGY_PARAMS = {
    '2min': {
        "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 11, "STOCH_LO": 27, "STOCH_HI": 73, "SL_ATR_MULT": 1.2, "TP_RR": 1.8,
        "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
    }
}

RISK_PARAMS = {
    "CONTRACTS": 2,
    "MAX_LOSS_TRADE": 1200.0,
    "MAX_LOSS_DAY": 1200.0,
    "STARTING_EQUITY": 300000.0
}

SYMBOL_CONFIG = {
    'point_value': 20.0,
    'tick_size': 0.25,
    'tick_value': 5.0,
    'contract_multiplier': 1
}

# =============================================================================
# INDICATORS
# =============================================================================
def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    if len(prices) < period:
        return np.full(len(prices), np.nan)

    k = 2.0 / (period + 1)
    ema = np.full(len(prices), np.nan)
    ema[period-1] = np.mean(prices[:period])

    for i in range(period, len(prices)):
        ema[i] = prices[i] * k + ema[i-1] * (1 - k)

    return ema

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               k_period: int, d_period: int, smooth: int):
    if len(close) < k_period:
        return np.full(len(close), np.nan), np.full(len(close), np.nan)

    k_values = np.full(len(close), np.nan)

    for i in range(k_period-1, len(close)):
        high_max = np.max(high[i-k_period+1:i+1])
        low_min = np.min(low[i-k_period+1:i+1])
        k_values[i] = 100 * (close[i] - low_min) / (high_max - low_min) if (high_max - low_min) != 0 else 50

    if smooth > 1:
        k_smooth = np.full(len(k_values), np.nan)
        for i in range(smooth-1, len(k_values)):
            k_smooth[i] = np.mean(k_values[i-smooth+1:i+1])
        k_values = k_smooth

    d_values = np.full(len(k_values), np.nan)
    for i in range(d_period-1, len(k_values)):
        d_values[i] = np.mean(k_values[i-d_period+1:i+1])

    return k_values, d_values

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    if len(close) < period + 1:
        return np.full(len(close), np.nan)

    tr = np.full(len(close), np.nan)
    for i in range(1, len(close)):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))

    atr = np.full(len(tr), np.nan)
    atr[period] = np.mean(tr[1:period+1])

    for i in range(period+1, len(tr)):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period

    return atr

# =============================================================================
# LOAD REAL CME DATA
# =============================================================================
def load_cme_data(filepath: str) -> pd.DataFrame:
    """Load and clean the original CME data"""
    print(f"📊 Loading real CME data from {filepath}")

    df = pd.read_csv(filepath)

    # Keep only OHLC columns (first 5 columns)
    df = df.iloc[:, :5]
    df.columns = ['time', 'open', 'high', 'low', 'close']

    # Convert time to datetime
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter for NY session
    df['hour'] = df['timestamp'].dt.hour
    df['minute'] = df['timestamp'].dt.minute
    df['in_session'] = (
        ((df['hour'] > NY_SESSION['start_hour']) |
         ((df['hour'] == NY_SESSION['start_hour']) & (df['minute'] >= NY_SESSION['start_minute']))) &
        ((df['hour'] < NY_SESSION['end_hour']) |
         ((df['hour'] == NY_SESSION['end_hour']) & (df['minute'] <= NY_SESSION['end_minute'])))
    )
    df = df[df['in_session']].reset_index(drop=True)

    print(f"✅ Loaded {len(df)} real market bars (NY session only)")
    return df

# =============================================================================
# STRATEGY LOGIC
# =============================================================================
def generate_signals(df: pd.DataFrame, params: Dict) -> pd.DataFrame:
    df = df.copy()

    # Calculate indicators
    df['ema_fast'] = calc_ema(df['close'].values, params['EMA_FAST'])
    df['ema_slow'] = calc_ema(df['close'].values, params['EMA_SLOW'])
    df['stoch_k'], df['stoch_d'] = calc_stoch(
        df['high'].values, df['low'].values, df['close'].values,
        params['STOCH_K'], params['STOCH_D'], params['STOCH_SMT']
    )
    df['atr'] = calc_atr(df['high'].values, df['low'].values, df['close'].values, params['ATR_LEN'])

    # Trend filter
    df['trend_up'] = df['ema_fast'] > df['ema_slow']
    df['trend_down'] = df['ema_fast'] < df['ema_slow']

    # Stoch-D signals
    df['stoch_overbought'] = df['stoch_d'] >= params['STOCH_HI']
    df['stoch_oversold'] = df['stoch_d'] <= params['STOCH_LO']

    # Momentum filter
    df['momentum_up'] = df['close'] > df['close'].shift(3)
    df['momentum_down'] = df['close'] < df['close'].shift(3)

    # Entry signals
    df['long_signal'] = (
        df['trend_up'] &
        df['stoch_oversold'] &
        df['momentum_up']
    )

    df['short_signal'] = (
        df['trend_down'] &
        df['stoch_overbought'] &
        df['momentum_down']
    )

    # Exit signals
    df['long_exit'] = df['stoch_d'] >= params['STOCH_HI'] - 5
    df['short_exit'] = df['stoch_d'] <= params['STOCH_LO'] + 5

    return df

# =============================================================================
# BACKTEST ENGINE
# =============================================================================
def run_backtest_on_real_data():
    """Run backtest on real CME data"""
    print("🚀 REAL MARKET DATA BACKTEST")
    print("=" * 40)
    print("Using original CME_MINI_NQ1___1-3.csv data")
    print("Parameters from ES 2min success")
    print("Target: 200+ trades with positive P&L")
    print()

    # Load real data
    df = load_cme_data('CME_MINI_NQ1___1-3.csv')
    if len(df) < 1000:
        print("❌ Insufficient real market data")
        return None

    # Generate signals
    df = generate_signals(df, STRATEGY_PARAMS['2min'])

    # Backtest variables
    equity = RISK_PARAMS['STARTING_EQUITY']
    contracts = RISK_PARAMS['CONTRACTS']
    trades = []
    position = 0
    entry_price = 0
    entry_time = None
    daily_loss = 0
    last_trade_date = None

    for i, row in df.iterrows():
        current_date = row['timestamp'].date()

        if last_trade_date != current_date:
            daily_loss = 0
            last_trade_date = current_date

        if position == 0:
            if row['long_signal'] and daily_loss > -RISK_PARAMS['MAX_LOSS_DAY']:
                position = contracts
                entry_price = row['close'] + (WEBULL_COSTS['spread_slippage_ticks'] * SYMBOL_CONFIG['tick_size'])
                entry_time = row['timestamp']

                atr_value = row['atr'] if not np.isnan(row['atr']) else (row['high'] - row['low'])
                sl_distance = atr_value * STRATEGY_PARAMS['2min']['SL_ATR_MULT']
                tp_distance = sl_distance * STRATEGY_PARAMS['2min']['TP_RR']

                stop_loss = entry_price - sl_distance
                take_profit = entry_price + tp_distance

            elif row['short_signal'] and daily_loss > -RISK_PARAMS['MAX_LOSS_DAY']:
                position = -contracts
                entry_price = row['close'] - (WEBULL_COSTS['spread_slippage_ticks'] * SYMBOL_CONFIG['tick_size'])
                entry_time = row['timestamp']

                atr_value = row['atr'] if not np.isnan(row['atr']) else (row['high'] - row['low'])
                sl_distance = atr_value * STRATEGY_PARAMS['2min']['SL_ATR_MULT']
                tp_distance = sl_distance * STRATEGY_PARAMS['2min']['TP_RR']

                stop_loss = entry_price + sl_distance
                take_profit = entry_price - tp_distance

        elif position != 0:
            exit_triggered = False
            exit_price = row['close']
            exit_reason = ""

            if (position > 0 and row['close'] <= stop_loss) or (position < 0 and row['close'] >= stop_loss):
                exit_price = stop_loss
                exit_reason = "Stop Loss"
                exit_triggered = True

            elif (position > 0 and row['close'] >= take_profit) or (position < 0 and row['close'] <= take_profit):
                exit_price = take_profit
                exit_reason = "Take Profit"
                exit_triggered = True

            elif (position > 0 and row['long_exit']) or (position < 0 and row['short_exit']):
                exit_price = row['close']
                exit_reason = "Signal Exit"
                exit_triggered = True

            trade_pnl = (exit_price - entry_price) * position * SYMBOL_CONFIG['point_value']
            if abs(trade_pnl) > RISK_PARAMS['MAX_LOSS_TRADE']:
                exit_price = entry_price - (RISK_PARAMS['MAX_LOSS_TRADE'] / (position * SYMBOL_CONFIG['point_value']))
                exit_reason = "Max Loss"
                exit_triggered = True

            if exit_triggered:
                gross_pnl = (exit_price - entry_price) * position * SYMBOL_CONFIG['point_value']
                commissions = abs(position) * 2 * WEBULL_COSTS['commission_per_contract']
                routing_fees = WEBULL_COSTS['routing_fee']
                total_costs = commissions + routing_fees
                net_pnl = gross_pnl - total_costs

                trade = {
                    'entry_time': entry_time,
                    'exit_time': row['timestamp'],
                    'symbol': 'MNQ_REAL',
                    'timeframe': '2min',
                    'position': 'Long' if position > 0 else 'Short',
                    'contracts': abs(position),
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'gross_pnl': gross_pnl,
                    'costs': total_costs,
                    'net_pnl': net_pnl,
                    'exit_reason': exit_reason,
                    'holding_period': (row['timestamp'] - entry_time).total_seconds() / 60
                }
                trades.append(trade)

                equity += net_pnl
                daily_loss += net_pnl

                position = 0
                entry_price = 0
                entry_time = None

    if not trades:
        print("❌ No trades generated on real data")
        return None

    trades_df = pd.DataFrame(trades)
    winning_trades = trades_df[trades_df['net_pnl'] > 0]
    losing_trades = trades_df[trades_df['net_pnl'] <= 0]

    total_pnl = trades_df['net_pnl'].sum()
    win_rate = len(winning_trades) / len(trades_df) * 100
    profit_factor = abs(winning_trades['net_pnl'].sum() / losing_trades['net_pnl'].sum()) if len(losing_trades) > 0 and losing_trades['net_pnl'].sum() != 0 else float('inf')

    equity_curve = [RISK_PARAMS['STARTING_EQUITY']]
    peak = RISK_PARAMS['STARTING_EQUITY']
    max_drawdown = 0

    for pnl in trades_df['net_pnl'].cumsum():
        current_equity = RISK_PARAMS['STARTING_EQUITY'] + pnl
        equity_curve.append(current_equity)
        peak = max(peak, current_equity)
        drawdown = (peak - current_equity) / peak * 100
        max_drawdown = max(max_drawdown, drawdown)

    returns = trades_df['net_pnl'] / RISK_PARAMS['STARTING_EQUITY']
    sharpe_ratio = returns.mean() / returns.std() * np.sqrt(252) if returns.std() != 0 else 0
    avg_trade = trades_df['net_pnl'].mean()

    print(f"✅ MNQ_REAL 2min: {len(trades_df)} trades, Win Rate: {win_rate:.1f}%, P&L: ${total_pnl:,.0f}")

    result = {
        'symbol': 'MNQ_REAL',
        'timeframe': '2min',
        'trades': len(trades_df),
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'avg_trade': avg_trade,
        'sharpe_ratio': sharpe_ratio,
        'equity_curve': equity_curve,
        'trades_data': trades
    }

    # Display results
    print("\n" + "=" * 60)
    print("🎯 REAL MARKET DATA BACKTEST RESULTS")
    print("=" * 60)
    print("<20")
    print("-" * 60)

    pf_str = f"{result['profit_factor']:.2f}" if result['profit_factor'] != float('inf') else "inf"
    print("<20")

    print("\n🏆 PERFORMANCE SUMMARY:")
    print(f"   Trades: {result['trades']}")
    print(f"   Win Rate: {result['win_rate']:.1f}%")
    print(f"   Total P&L: ${result['total_pnl']:,.0f}")
    print(f"   Profit Factor: {result['profit_factor']:.2f}")
    print(f"   Max Drawdown: {result['max_drawdown']:.1f}%")
    print(f"   Average Trade: ${result['avg_trade']:.0f}")
    # Save results
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_file = f'results/real_data_backtest_{timestamp}.csv'

    results_data = [{
        'Symbol': result['symbol'],
        'Timeframe': result['timeframe'],
        'Trades': result['trades'],
        'Win_Rate': result['win_rate'],
        'Total_PnL': result['total_pnl'],
        'Profit_Factor': result['profit_factor'],
        'Max_Drawdown': result['max_drawdown'],
        'Avg_Trade': result['avg_trade'],
        'Sharpe_Ratio': result['sharpe_ratio']
    }]

    results_df = pd.DataFrame(results_data)
    results_df.to_csv(results_file, index=False)
    print(f"\n💾 Results saved to {results_file}")

    # Equity chart
    if result['equity_curve']:
        plt.figure(figsize=(12, 6))
        plt.plot(result['equity_curve'], linewidth=2, label='MNQ Real Data')
        plt.title('Equity Curve - MNQ Real Market Data (ES Parameters)')
        plt.xlabel('Trades')
        plt.ylabel('Equity ($)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()

        chart_file = f'results/real_data_equity_curve_{timestamp}.png'
        plt.savefig(chart_file, dpi=150, bbox_inches='tight')
        print(f"📊 Chart saved to {chart_file}")
        plt.close()

    print("\n✅ Real market data backtest complete!")
    return result

if __name__ == "__main__":
    run_backtest_on_real_data()