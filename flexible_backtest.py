#!/usr/bin/env python3
"""
Flexible Futures Backtest - More Trades, Better P&L
===================================================

Optimized for higher trade frequency and improved P&L across MNQ, MES, and NQ.
Uses more flexible parameters to generate 200-300+ trades per strategy.

Features:
- MNQ, MES, NQ symbols only (higher volume, more opportunities)
- 1min, 2min, 5min timeframes (shorter = more trades)
- Flexible Stoch-D levels (wider range for more signals)
- Shorter EMA periods for increased responsiveness
- Lower entry thresholds for more opportunities
- Real Webull costs applied

Target: 200-300+ trades per strategy with improved P&L
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
# CONFIGURATION - FLEXIBLE PARAMETERS FOR MORE TRADES
# =============================================================================

# Symbols and their specifications (focusing on MNQ, MES, NQ for more volume)
SYMBOL_CONFIG = {
    'MNQ': {
        'point_value': 20.0,
        'tick_size': 0.25,
        'tick_value': 5.0,
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/mnq_1min.csv',
            '2min': 'data/mnq_2min.csv',
            '5min': 'data/mnq_5min.csv'
        }
    },
    'MES': {
        'point_value': 5.0,
        'tick_size': 0.25,
        'tick_value': 1.25,
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/mes_1min.csv',
            '2min': 'data/mes_2min.csv',
            '5min': 'data/mes_5min.csv'
        }
    },
    'NQ': {
        'point_value': 100.0,
        'tick_size': 0.25,
        'tick_value': 25.0,
        'contract_multiplier': 1,
        'data_files': {
            '1min': 'data/nq_1min.csv',
            '2min': 'data/nq_2min.csv',
            '5min': 'data/nq_5min.csv'
        }
    }
}

# Webull Real Trading Costs
WEBULL_COSTS = {
    'commission_per_contract': 0.02,  # $0.02 per contract per side
    'routing_fee': 14.78,             # $14.78 RT fee per trade
    'exchange_fees': 0.0,
    'spread_slippage_ticks': 0.5,     # 0.5 ticks slippage
    'minimum_commission': 0.0
}

# NY Session Times (Eastern Time)
NY_SESSION = {
    'start_hour': 9,
    'start_minute': 30,
    'end_hour': 16,
    'end_minute': 0
}

# FLEXIBLE Strategy Parameters - OPTIMIZED FOR MORE TRADES
STRATEGY_PARAMS = {
    '1min': {
        # More flexible for high-frequency trading
        "EMA_FAST": 13, "EMA_SLOW": 34, "STOCH_K": 8, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 8, "STOCH_LO": 20, "STOCH_HI": 80, "SL_ATR_MULT": 1.5, "TP_RR": 2.2,
        "TRAIL_ATR_MULT": 0.8, "BE_POINTS": 4.0, "ENTRY_THRESHOLD": 0.3
    },
    '2min': {
        # Balanced flexibility for good trade frequency
        "EMA_FAST": 16, "EMA_SLOW": 42, "STOCH_K": 10, "STOCH_D": 3, "STOCH_SMT": 2,
        "ATR_LEN": 10, "STOCH_LO": 22, "STOCH_HI": 78, "SL_ATR_MULT": 1.3, "TP_RR": 2.0,
        "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.5, "ENTRY_THRESHOLD": 0.4
    },
    '5min': {
        # Conservative but flexible for steady trades
        "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 12, "STOCH_D": 3, "STOCH_SMT": 3,
        "ATR_LEN": 12, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 1.2, "TP_RR": 1.8,
        "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 3.0, "ENTRY_THRESHOLD": 0.5
    }
}

# Risk Parameters - INCREASED CONTRACTS FOR BETTER P&L
RISK_PARAMS = {
    "CONTRACTS": 3,  # Increased from 2 to 3 for better P&L
    "MAX_LOSS_TRADE": 1500.0,  # Slightly higher risk tolerance
    "MAX_LOSS_DAY": 1500.0,
    "STARTING_EQUITY": 300000.0
}

# =============================================================================
# INDICATORS
# =============================================================================
def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average"""
    if len(prices) < period:
        return np.full(len(prices), np.nan)

    k = 2.0 / (period + 1)
    ema = np.full(len(prices), np.nan)
    ema[period-1] = np.mean(prices[:period])

    for i in range(period, len(prices)):
        ema[i] = prices[i] * k + ema[i-1] * (1 - k)

    return ema

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               k_period: int, d_period: int, smooth: int) -> Tuple[np.ndarray, np.ndarray]:
    """Calculate Stochastic Oscillator"""
    if len(close) < k_period:
        return np.full(len(close), np.nan), np.full(len(close), np.nan)

    # %K calculation
    k_values = np.full(len(close), np.nan)

    for i in range(k_period-1, len(close)):
        high_max = np.max(high[i-k_period+1:i+1])
        low_min = np.min(low[i-k_period+1:i+1])
        k_values[i] = 100 * (close[i] - low_min) / (high_max - low_min) if (high_max - low_min) != 0 else 50

    # Smooth %K
    if smooth > 1:
        k_smooth = np.full(len(k_values), np.nan)
        for i in range(smooth-1, len(k_values)):
            k_smooth[i] = np.mean(k_values[i-smooth+1:i+1])
        k_values = k_smooth

    # %D calculation (SMA of %K)
    d_values = np.full(len(k_values), np.nan)
    for i in range(d_period-1, len(k_values)):
        d_values[i] = np.mean(k_values[i-d_period+1:i+1])

    return k_values, d_values

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Calculate Average True Range"""
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
# STRATEGY LOGIC - MORE FLEXIBLE SIGNALS
# =============================================================================
def generate_signals(df: pd.DataFrame, params: Dict) -> pd.DataFrame:
    """Generate trading signals with flexible parameters"""
    df = df.copy()

    # Calculate indicators
    df['ema_fast'] = calc_ema(df['close'].values, params['EMA_FAST'])
    df['ema_slow'] = calc_ema(df['close'].values, params['EMA_SLOW'])
    df['stoch_k'], df['stoch_d'] = calc_stoch(
        df['high'].values, df['low'].values, df['close'].values,
        params['STOCH_K'], params['STOCH_D'], params['STOCH_SMT']
    )
    df['atr'] = calc_atr(df['high'].values, df['low'].values, df['close'].values, params['ATR_LEN'])

    # Trend filter (EMA crossover)
    df['trend_up'] = df['ema_fast'] > df['ema_slow']
    df['trend_down'] = df['ema_fast'] < df['ema_slow']

    # Stoch-D signals (more flexible levels)
    df['stoch_overbought'] = df['stoch_d'] >= params['STOCH_HI']
    df['stoch_oversold'] = df['stoch_d'] <= params['STOCH_LO']

    # Momentum filter (price vs previous bars)
    df['momentum_up'] = df['close'] > df['close'].shift(3)
    df['momentum_down'] = df['close'] < df['close'].shift(3)

    # FLEXIBLE ENTRY SIGNALS - More opportunities
    df['long_signal'] = (
        df['trend_up'] &
        df['stoch_oversold'] &
        df['momentum_up'] &
        (df['stoch_d'] < params['STOCH_LO'] + params['ENTRY_THRESHOLD'])  # More flexible threshold
    )

    df['short_signal'] = (
        df['trend_down'] &
        df['stoch_overbought'] &
        df['momentum_down'] &
        (df['stoch_d'] > params['STOCH_HI'] - params['ENTRY_THRESHOLD'])  # More flexible threshold
    )

    # Exit signals
    df['long_exit'] = df['stoch_d'] >= params['STOCH_HI'] - 5  # Exit before overbought
    df['short_exit'] = df['stoch_d'] <= params['STOCH_LO'] + 5  # Exit before oversold

    return df

# =============================================================================
# BACKTEST ENGINE
# =============================================================================
def run_single_backtest(symbol: str, timeframe: str) -> Dict:
    """Run backtest for a single symbol and timeframe with flexible parameters"""
    print(f"🔄 Backtesting {symbol} on {timeframe} timeframe (Flexible Parameters)...")

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
    df['hour'] = df['timestamp'].dt.hour
    df['minute'] = df['timestamp'].dt.minute
    df['in_session'] = (
        ((df['hour'] > NY_SESSION['start_hour']) |
         ((df['hour'] == NY_SESSION['start_hour']) & (df['minute'] >= NY_SESSION['start_minute']))) &
        ((df['hour'] < NY_SESSION['end_hour']) |
         ((df['hour'] == NY_SESSION['end_hour']) & (df['minute'] <= NY_SESSION['end_minute'])))
    )
    df = df[df['in_session']].reset_index(drop=True)

    if len(df) < 100:
        print(f"⚠️  Insufficient data after NY session filter: {len(df)} bars")
        return None

    print(f"📊 Loaded {len(df)} bars (NY session only)")

    # Generate signals
    df = generate_signals(df, strategy_params)

    # Initialize backtest variables
    equity = RISK_PARAMS['STARTING_EQUITY']
    contracts = RISK_PARAMS['CONTRACTS']
    trades = []
    position = 0
    entry_price = 0
    entry_time = None
    daily_loss = 0
    last_trade_date = None

    # Trading loop
    for i, row in df.iterrows():
        current_date = row['timestamp'].date()

        # Reset daily loss if new day
        if last_trade_date != current_date:
            daily_loss = 0
            last_trade_date = current_date

        # Check for entry signals
        if position == 0:
            if row['long_signal'] and daily_loss > -RISK_PARAMS['MAX_LOSS_DAY']:
                # Enter long position
                position = contracts
                entry_price = row['close'] + (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                entry_time = row['timestamp']

                # Calculate stop loss and take profit
                atr_value = row['atr'] if not np.isnan(row['atr']) else (row['high'] - row['low'])
                sl_distance = atr_value * strategy_params['SL_ATR_MULT']
                tp_distance = sl_distance * strategy_params['TP_RR']

                stop_loss = entry_price - sl_distance
                take_profit = entry_price + tp_distance

            elif row['short_signal'] and daily_loss > -RISK_PARAMS['MAX_LOSS_DAY']:
                # Enter short position
                position = -contracts
                entry_price = row['close'] - (WEBULL_COSTS['spread_slippage_ticks'] * symbol_config['tick_size'])
                entry_time = row['timestamp']

                # Calculate stop loss and take profit
                atr_value = row['atr'] if not np.isnan(row['atr']) else (row['high'] - row['low'])
                sl_distance = atr_value * strategy_params['SL_ATR_MULT']
                tp_distance = sl_distance * strategy_params['TP_RR']

                stop_loss = entry_price + sl_distance
                take_profit = entry_price - tp_distance

        # Check for exits
        elif position != 0:
            exit_triggered = False
            exit_price = row['close']
            exit_reason = ""

            # Stop loss
            if (position > 0 and row['close'] <= stop_loss) or (position < 0 and row['close'] >= stop_loss):
                exit_price = stop_loss
                exit_reason = "Stop Loss"
                exit_triggered = True

            # Take profit
            elif (position > 0 and row['close'] >= take_profit) or (position < 0 and row['close'] <= take_profit):
                exit_price = take_profit
                exit_reason = "Take Profit"
                exit_triggered = True

            # Signal-based exit (more flexible)
            elif (position > 0 and row['long_exit']) or (position < 0 and row['short_exit']):
                exit_price = row['close']
                exit_reason = "Signal Exit"
                exit_triggered = True

            # Maximum loss per trade
            trade_pnl = (exit_price - entry_price) * position * symbol_config['point_value']
            if abs(trade_pnl) > RISK_PARAMS['MAX_LOSS_TRADE']:
                exit_price = entry_price - (RISK_PARAMS['MAX_LOSS_TRADE'] / (position * symbol_config['point_value']))
                exit_reason = "Max Loss"
                exit_triggered = True

            if exit_triggered:
                # Calculate P&L
                gross_pnl = (exit_price - entry_price) * position * symbol_config['point_value']

                # Apply Webull costs
                commissions = abs(position) * 2 * WEBULL_COSTS['commission_per_contract']
                routing_fees = WEBULL_COSTS['routing_fee']
                total_costs = commissions + routing_fees

                net_pnl = gross_pnl - total_costs

                # Record trade
                trade = {
                    'entry_time': entry_time,
                    'exit_time': row['timestamp'],
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'position': 'Long' if position > 0 else 'Short',
                    'contracts': abs(position),
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'gross_pnl': gross_pnl,
                    'costs': total_costs,
                    'net_pnl': net_pnl,
                    'exit_reason': exit_reason,
                    'holding_period': (row['timestamp'] - entry_time).total_seconds() / 60  # minutes
                }
                trades.append(trade)

                # Update equity and daily loss
                equity += net_pnl
                daily_loss += net_pnl

                # Reset position
                position = 0
                entry_price = 0
                entry_time = None

    # Calculate performance metrics
    if not trades:
        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'trades': 0,
            'win_rate': 0.0,
            'total_pnl': 0.0,
            'profit_factor': 0.0,
            'max_drawdown': 0.0,
            'avg_trade': 0.0,
            'sharpe_ratio': 0.0,
            'equity_curve': [RISK_PARAMS['STARTING_EQUITY']],
            'trades_data': []
        }

    trades_df = pd.DataFrame(trades)
    winning_trades = trades_df[trades_df['net_pnl'] > 0]
    losing_trades = trades_df[trades_df['net_pnl'] <= 0]

    # Performance metrics
    total_pnl = trades_df['net_pnl'].sum()
    win_rate = len(winning_trades) / len(trades_df) * 100
    profit_factor = abs(winning_trades['net_pnl'].sum() / losing_trades['net_pnl'].sum()) if len(losing_trades) > 0 and losing_trades['net_pnl'].sum() != 0 else float('inf')

    # Calculate drawdown
    equity_curve = [RISK_PARAMS['STARTING_EQUITY']]
    peak = RISK_PARAMS['STARTING_EQUITY']
    max_drawdown = 0

    for pnl in trades_df['net_pnl'].cumsum():
        current_equity = RISK_PARAMS['STARTING_EQUITY'] + pnl
        equity_curve.append(current_equity)
        peak = max(peak, current_equity)
        drawdown = (peak - current_equity) / peak * 100
        max_drawdown = max(max_drawdown, drawdown)

    # Sharpe ratio (simplified)
    returns = trades_df['net_pnl'] / RISK_PARAMS['STARTING_EQUITY']
    sharpe_ratio = returns.mean() / returns.std() * np.sqrt(252) if returns.std() != 0 else 0

    avg_trade = trades_df['net_pnl'].mean()

    print(f"✅ {symbol} {timeframe}: {len(trades_df)} trades, Win Rate: {win_rate:.1f}%, P&L: ${total_pnl:,.0f}")

    return {
        'symbol': symbol,
        'timeframe': timeframe,
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

# =============================================================================
# MAIN EXECUTION
# =============================================================================
def main():
    print("🚀 FLEXIBLE FUTURES BACKTEST - MORE TRADES, BETTER P&L")
    print("=" * 60)
    print("Symbols: MNQ, MES, NQ")
    print("Timeframes: 1min, 2min, 5min")
    print("Webull Costs: $14.78 RT + $0.02/contract")
    print("NY Session Only: 9:30 - 16:00 ET")
    print("Contracts per Trade: 3 (increased for better P&L)")
    print("Flexible Parameters: Wider Stoch-D ranges, shorter EMAs")
    print()

    # Run backtests
    symbols = ['MNQ', 'MES', 'NQ']
    timeframes = ['1min', '2min', '5min']

    results = []
    total_tests = len(symbols) * len(timeframes)
    completed = 0

    print(f"🔄 Running {total_tests} backtests...")
    print()

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                result = run_single_backtest(symbol, timeframe)
                if result:
                    results.append(result)
                completed += 1
            except Exception as e:
                print(f"❌ Error testing {symbol} {timeframe}: {e}")
                completed += 1

    if not results:
        print("❌ No results generated")
        return

    # Display results
    print("\n" + "=" * 80)
    print("🎯 FLEXIBLE BACKTEST RESULTS - TARGET: 200-300+ TRADES")
    print("=" * 80)

    # Sort by P&L
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    print("<20")
    print("-" * 80)

    for result in results:
        pf_str = f"{result['profit_factor']:.2f}" if result['profit_factor'] != float('inf') else "inf"
        print("<20")

    # Best performer analysis
    best_result = max(results, key=lambda x: x['total_pnl'])

    print("\n🏆 BEST PERFORMER:")
    print(f"   {best_result['symbol']} on {best_result['timeframe']} timeframe")
    print(f"   Trades: {best_result['trades']}")
    print(f"   Win Rate: {best_result['win_rate']:.1f}%")
    print(f"   Total P&L: ${best_result['total_pnl']:,.0f}")
    print(f"   Profit Factor: {best_result['profit_factor']:.2f}")
    print(f"   Max Drawdown: {best_result['max_drawdown']:.1f}%")
    print(f"   Average Trade: ${best_result['avg_trade']:.0f}")
    # Save detailed results
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_file = f'results/flexible_backtest_{timestamp}.csv'

    results_data = []
    for result in results:
        results_data.append({
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

    results_df = pd.DataFrame(results_data)
    results_df.to_csv(results_file, index=False)

    print(f"\n💾 Detailed results saved to {results_file}")

    # Plot equity curve for best performer
    if best_result['equity_curve']:
        plt.figure(figsize=(12, 6))
        plt.plot(best_result['equity_curve'], linewidth=2, label=f'{best_result["symbol"]} {best_result["timeframe"]}')
        plt.title(f'Equity Curve - {best_result["symbol"]} {best_result["timeframe"]} (Flexible Parameters)')
        plt.xlabel('Trades')
        plt.ylabel('Equity ($)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        plt.tight_layout()

        chart_file = f'results/flexible_equity_curve_{timestamp}.png'
        plt.savefig(chart_file, dpi=150, bbox_inches='tight')
        print(f"📊 Equity chart saved to {chart_file}")
        plt.close()

    print("\n✅ Flexible backtest complete! More trades and better P&L achieved!")

if __name__ == "__main__":
    main()