#!/usr/bin/env python3
"""
SIL Strategy Backtest with Daily Historical Data
===============================================

Backtests the trading strategy using daily SIL (Micro Silver) data.
Adapted for daily timeframe with appropriate parameters for silver futures.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import warnings
import os
warnings.filterwarnings('ignore')

# =============================================================================
# STRATEGY PARAMETERS (Adapted for Daily SIL)
# =============================================================================

STRATEGY_PARAMS = {
    'EMA_FAST': 20,   # Longer for daily data
    'EMA_SLOW': 50,   # Longer for daily data
    'STOCH_LO': 25,
    'STOCH_HI': 75,
    'ATR_PERIOD': 14,  # Standard ATR for daily
    'SL_ATR_MULT': 2.0,
    'TP_RR': 1.8
}

RISK_PARAMS = {
    'CONTRACTS': 1,
    'MAX_LOSS_TRADE': 200.0,  # Lower for silver volatility
    'HARD_STOP_DOLLARS': 200.0,  # $200 hard stop for silver
    'ATR_MULTIPLIER': 2.0,
    'ATR_PERIOD': 14,
    'TAKE_PROFIT_MULTIPLIER': 2.0
}

# =============================================================================
# INDICATOR CALCULATIONS
# =============================================================================

def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    """Calculate Exponential Moving Average"""
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Calculate Average True Range"""
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period-1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
    """Calculate Stochastic Oscillator"""
    lowest_low = np.array([np.min(low[i-k_period+1:i+1]) for i in range(k_period-1, len(low))])
    highest_high = np.array([np.max(high[i-k_period+1:i+1]) for i in range(k_period-1, len(high))])

    k_values = 100 * (close[k_period-1:] - lowest_low) / (highest_high - lowest_low)
    k_smooth = np.convolve(k_values, np.ones(smooth)/smooth, mode='valid')

    d_values = np.convolve(k_smooth, np.ones(d_period)/d_period, mode='valid')

    # Pad with NaN to match original array length
    k_full = np.full(len(close), np.nan)
    d_full = np.full(len(close), np.nan)

    k_full[k_period-1 + smooth-1:] = k_smooth
    d_full[k_period-1 + smooth-1 + d_period-1:] = d_values

    return k_full, d_full

# =============================================================================
# BACKTEST ENGINE
# =============================================================================

class SILBacktest:
    """Backtest engine for MNQ strategy with trade analysis improvements"""

    def __init__(self, symbol="MNQ", days=90):
        self.symbol = symbol
        self.days = days
        self.data = None
        self.results = []        # Local copy of risk params that can be modified
        self.risk_params = RISK_PARAMS.copy()

        # Trade analysis improvements
        self.consecutive_losses = 0
        self.trading_paused = False
        self.best_trading_hours = {11, 12, 15}  # Hours with highest win rates
    def download_data(self):
        """Load historical data from local CSV file"""
        print(f"📊 Loading local SIL daily data...")

        import os
        data_file = "data/sil_daily.csv"

        if not os.path.exists(data_file):
            raise ValueError(f"Data file not found: {data_file}")

        # Load CSV data
        df = pd.read_csv(data_file)

        # Check if we have the required columns
        required_cols = ['time', 'open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"CSV file missing required columns: {required_cols}")

        # Convert timestamp to datetime (Unix timestamp)
        df['timestamp'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('timestamp', inplace=True)
        df = df.drop('time', axis=1)

        # Add volume column if missing (use 0 for futures)
        if 'volume' not in df.columns:
            df['volume'] = 0

        # Ensure numeric columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Handle timezone - assume US/Eastern
        if df.index.tz is None:
            df.index = df.index.tz_localize('US/Eastern')

        # Sort by timestamp
        df = df.sort_index()

        # Limit to recent data (last 30 days to match our timeframe)
        end_date = df.index.max()
        start_date = end_date - timedelta(days=self.days)
        df = df[df.index >= start_date]

        if df.empty:
            raise ValueError(f"No data available in date range for {self.symbol}")

        self.data = df
        print(f"✅ Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")
        return df

    def download_data_specific(self, data_file):
        """Load historical data from a specific CSV file"""
        print(f"📊 Loading data from {data_file}...")

        if not os.path.exists(data_file):
            raise ValueError(f"Data file not found: {data_file}")

        # Load CSV data
        df = pd.read_csv(data_file)

        # Check if we have the required columns
        required_cols = ['time', 'open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            raise ValueError(f"CSV file missing required columns: {required_cols}")

        # Convert timestamp to datetime (Unix timestamp)
        df['timestamp'] = pd.to_datetime(df['time'], unit='s')
        df.set_index('timestamp', inplace=True)
        df = df.drop('time', axis=1)

        # Add volume column if missing (use 0 for futures)
        if 'volume' not in df.columns:
            df['volume'] = 0

        # Ensure numeric columns
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Handle timezone - assume US/Eastern
        if df.index.tz is None:
            df.index = df.index.tz_localize('US/Eastern')

        # Sort by timestamp
        df = df.sort_index()

        # Limit to recent data (last 30 days to match our timeframe)
        end_date = df.index.max()
        start_date = end_date - timedelta(days=self.days)
        df = df[df.index >= start_date]

        if df.empty:
            raise ValueError(f"No data available in date range for timeframe")

        self.data = df
        print(f"✅ Loaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")
        return df

    def calculate_indicators(self, df):
        """Calculate all technical indicators"""
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values

        # EMAs
        df['ema_fast'] = calc_ema(close, STRATEGY_PARAMS['EMA_FAST'])
        df['ema_slow'] = calc_ema(close, STRATEGY_PARAMS['EMA_SLOW'])

        # Trend detection
        df['uptrend'] = df['ema_fast'] > df['ema_slow']
        df['downtrend'] = df['ema_fast'] < df['ema_slow']

        # ATR
        df['atr'] = calc_atr(high, low, close, self.risk_params['ATR_PERIOD'])

        # Stochastic
        df['stoch_k'], df['stoch_d'] = calc_stoch(high, low, close,
                                                 k_period=14, d_period=3, smooth=1)

        # Stochastic signals
        df['stoch_overbought'] = df['stoch_d'] >= STRATEGY_PARAMS['STOCH_HI']
        df['stoch_oversold'] = df['stoch_d'] <= STRATEGY_PARAMS['STOCH_LO']

        # D line rising/falling (momentum)
        df['d_rising'] = df['stoch_d'] > df['stoch_d'].shift(1)
        df['d_falling'] = df['stoch_d'] < df['stoch_d'].shift(1)

        return df

    def check_entry_signals(self, row):
        """Check for entry signals - very simple for daily data"""
        # Long signal: EMA crossover
        if (row['ema_fast'] > row['ema_slow'] and  # Uptrend
            row['atr'] > 0):
            return 'long'

        # Short signal: EMA crossover
        if (row['ema_fast'] < row['ema_slow'] and  # Downtrend
            row['atr'] > 0):
            return 'short'

        return None

    def check_exit_signals(self, row, position, entry_price, stop_loss, take_profit):
        """Check for exit signals"""
        current_price = row['close']

        if position > 0:  # Long position
            # Hard stop loss
            hard_stop = entry_price - (self.risk_params['HARD_STOP_DOLLARS'] / (abs(position) * 20))
            if current_price <= hard_stop:
                return 'hard_stop'

            # ATR stop loss
            if current_price <= stop_loss:
                return 'atr_stop'

            # Take profit
            if current_price >= take_profit:
                return 'take_profit'

        else:  # Short position
            # Hard stop loss
            hard_stop = entry_price + (self.risk_params['HARD_STOP_DOLLARS'] / (abs(position) * 20))
            if current_price >= hard_stop:
                return 'hard_stop'

            # ATR stop loss
            if current_price >= stop_loss:
                return 'atr_stop'

            # Take profit
            if current_price <= take_profit:
                return 'take_profit'

        return None

    def run_backtest(self):
        """Run the complete backtest"""
        print("🎯 Running MNQ strategy backtest...")

        df = self.calculate_indicators(self.data.copy())

        # Trading variables
        position = 0
        entry_price = 0
        stop_loss = 0
        take_profit = 0
        trades = []
        equity_curve = [10000.0]  # Start with $10,000
        peak_equity = 10000.0
        max_drawdown = 0
        daily_pnl = {}
        current_day = None

        for idx, row in df.iterrows():
            current_time = idx
            current_day = current_time.date()

            # Reset daily P&L at start of new day
            if current_day not in daily_pnl:
                daily_pnl[current_day] = 0

            # Check for entry signals if no position
            if position == 0:
                signal = self.check_entry_signals(row)
                if signal and not np.isnan(row['atr']):
                    # Calculate position size and levels
                    quantity = self.risk_params['CONTRACTS']
                    atr_value = row['atr']

                    if signal == 'long':
                        entry_price = row['close']
                        stop_loss = entry_price - (atr_value * self.risk_params['ATR_MULTIPLIER'])
                        take_profit = entry_price + (atr_value * self.risk_params['ATR_MULTIPLIER'] * self.risk_params['TAKE_PROFIT_MULTIPLIER'])
                        position = quantity
                    else:  # short
                        entry_price = row['close']
                        stop_loss = entry_price + (atr_value * self.risk_params['ATR_MULTIPLIER'])
                        take_profit = entry_price - (atr_value * self.risk_params['ATR_MULTIPLIER'] * self.risk_params['TAKE_PROFIT_MULTIPLIER'])
                        position = -quantity

                    trades.append({
                        'entry_time': current_time,
                        'entry_price': entry_price,
                        'quantity': position,
                        'stop_loss': stop_loss,
                        'take_profit': take_profit,
                        'exit_time': None,
                        'exit_price': None,
                        'pnl': 0,
                        'exit_reason': None
                    })

            # Check for exit signals if in position
            elif position != 0:
                exit_signal = self.check_exit_signals(row, position, entry_price, stop_loss, take_profit)
                if exit_signal:
                    exit_price = row['close']
                    pnl = (exit_price - entry_price) * abs(position) * 20  # MNQ point value

                    # Update trade record
                    trades[-1].update({
                        'exit_time': current_time,
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'exit_reason': exit_signal
                    })

                    # Update equity
                    current_equity = equity_curve[-1] + pnl
                    equity_curve.append(current_equity)

                    # Update drawdown
                    peak_equity = max(peak_equity, current_equity)
                    drawdown = (peak_equity - current_equity) / peak_equity
                    max_drawdown = max(max_drawdown, drawdown)

                    # Update daily P&L
                    daily_pnl[current_day] += pnl

                    # Reset position
                    position = 0

        # Calculate final statistics
        winning_trades = [t for t in trades if t['pnl'] > 0]
        losing_trades = [t for t in trades if t['pnl'] < 0]

        total_trades = len(trades)
        winning_trades_count = len(winning_trades)
        win_rate = winning_trades_count / total_trades if total_trades > 0 else 0

        total_pnl = sum(t['pnl'] for t in trades)
        avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0
        biggest_win = max([t['pnl'] for t in winning_trades]) if winning_trades else 0
        biggest_loss = min([t['pnl'] for t in losing_trades]) if losing_trades else 0

        profit_factor = abs(sum(t['pnl'] for t in winning_trades) / sum(t['pnl'] for t in losing_trades)) if losing_trades else float('inf')

        # Daily statistics
        daily_returns = [pnl / 10000 for pnl in daily_pnl.values() if pnl != 0]  # Returns as percentage
        sharpe_ratio = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252) if daily_returns else 0

        results = {
            'total_trades': total_trades,
            'winning_trades': winning_trades_count,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'biggest_win': biggest_win,
            'biggest_loss': biggest_loss,
            'profit_factor': profit_factor,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'final_equity': equity_curve[-1] if equity_curve else 10000,
            'trades': trades,
            'equity_curve': equity_curve,
            'daily_pnl': daily_pnl
        }

        self.results = results
        return results

    def print_results(self):
        """Print comprehensive backtest results"""
        r = self.results

        print("\n" + "="*80)
        print(f"📊 MNQ STRATEGY BACKTEST RESULTS ({self.days} Days - 1-Minute Data from Local CSV)")
        print("="*80)

        print("\n🎯 TRADING STATISTICS:")
        print(f"   Total Trades: {r['total_trades']}")
        print(f"   Winning Trades: {r['winning_trades']}")
        print(f"   Win Rate: {r['win_rate']:.1%}")
        print(f"   Profit Factor: {r['profit_factor']:.2f}")

        print("\n💰 P&L ANALYSIS:")
        print(f"   Total P&L: ${r['total_pnl']:.2f}")
        print(f"   Average Win: ${r['avg_win']:.2f}")
        print(f"   Average Loss: ${r['avg_loss']:.2f}")
        print(f"   Biggest Win: ${r['biggest_win']:.2f}")
        print(f"   Biggest Loss: ${r['biggest_loss']:.2f}")

        print("\n📈 RISK METRICS:")
        print(f"   Max Drawdown: {r['max_drawdown']:.2%}")
        print(f"   Sharpe Ratio: {r['sharpe_ratio']:.3f}")
        print(f"   Final Equity: ${r['final_equity']:.2f}")

        print("\n📊 TRADE DETAILS:")
        print(f"   Total Trades: {r['total_trades']}")
        print(f"   Winning Trades: {r['winning_trades']}")
        print(f"   Losing Trades: {r['total_trades'] - r['winning_trades']}")
        print(f"   Win Rate: {r['win_rate']:.1%}")

        print("\n📅 PERFORMANCE SUMMARY:")
        print(f"   Start Date: {self.data.index[0].strftime('%Y-%m-%d')}")
        print(f"   End Date: {self.data.index[-1].strftime('%Y-%m-%d')}")
        print(f"   Data Points: {len(self.data)}")
        print(f"   Total Return: ${(r['final_equity'] - 10000):.2f}")
        if r['final_equity'] > 0:
            print(f"   Annualized Return: {((r['final_equity']/10000)**(365/(self.days)) - 1):.2%}")
        else:
            print("   Annualized Return: N/A (negative equity)")

        # Exit reason breakdown
        exit_reasons = {}
        for trade in r['trades']:
            reason = trade['exit_reason']
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        print("\n🎯 EXIT REASONS:")
        for reason, count in exit_reasons.items():
            print(f"   {reason}: {count} ({count/r['total_trades']:.1%})")

        print("\n" + "="*80)

def main():
    """Test 1 contract configuration with last 3 months data"""
    print("🚀 SIL BACKTEST: 1 CONTRACT - DAILY DATA")
    print("=" * 70)

    # Load data (last 90 days = 3 months)
    backtest = SILBacktest(symbol='SIL', days=90)
    backtest.download_data()

    # Test 1 contract configuration
    configs = [
        {
            'name': 'SIL_OPTIMIZED',
            'contracts': 1,
            'hard_stop': 200,  # $200 hard stop for silver volatility
            'atr_mult': 2.0,
            'atr_period': 14,
            'tp_mult': 2.0,
            'description': 'Optimized params for SIL daily data'
        }
    ]

    results = []

    for config in configs:
        print(f"\n🎯 Testing {config['name']}: {config['description']}")
        print("-" * 60)

        # Set parameters
        params = {
            'CONTRACTS': config['contracts'],
            'HARD_STOP_DOLLARS': config['hard_stop'],
            'ATR_MULTIPLIER': config['atr_mult'],
            'ATR_PERIOD': config['atr_period'],
            'TAKE_PROFIT_MULTIPLIER': config['tp_mult']
        }

        test_backtest = SILBacktest(symbol='SIL', days=30)
        test_backtest.data = backtest.data.copy()
        test_backtest.risk_params = params

        result = test_backtest.run_backtest()

        # Calculate exit reasons
        exit_reasons = {}
        for trade in result['trades']:
            reason = trade.get('exit_reason')
            if reason:
                exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # Calculate account metrics starting with $10K
        starting_balance = 10000
        final_balance = starting_balance + result['total_pnl']
        total_return_pct = ((final_balance - starting_balance) / starting_balance) * 100
        daily_return_pct = total_return_pct / 30  # 30 days

        config_result = {
            'name': config['name'],
            'description': config['description'],
            'contracts': config['contracts'],
            'hard_stop': config['hard_stop'],
            'atr_mult': config['atr_mult'],
            'atr_period': config['atr_period'],
            'tp_mult': config['tp_mult'],
            'total_trades': result['total_trades'],
            'win_rate': result['win_rate'],
            'total_pnl': result['total_pnl'],
            'profit_factor': result['profit_factor'],
            'max_drawdown': result['max_drawdown'],
            'sharpe_ratio': result['sharpe_ratio'],
            'starting_balance': starting_balance,
            'final_balance': final_balance,
            'total_return_pct': total_return_pct,
            'daily_return_pct': daily_return_pct,
            'hard_stop_exits': exit_reasons.get('hard_stop', 0),
            'take_profit_exits': exit_reasons.get('take_profit', 0),
            'atr_stop_exits': exit_reasons.get('atr_stop', 0),
            'avg_win': result['avg_win'],
            'avg_loss': result['avg_loss']
        }

        results.append(config_result)

        print(f"Trades: {result['total_trades']} | Win Rate: {result['win_rate']:.1f}%")
        print(f"P&L: ${result['total_pnl']:,.0f} | Final Balance: ${final_balance:,.0f}")
        print(f"Total Return: {total_return_pct:.1f}% | Daily Return: {daily_return_pct:.2f}%")
        print(f"Profit Factor: {result['profit_factor']:.2f} | Max DD: {result['max_drawdown']:.1f}%")
        print(f"Exit Breakdown: Hard Stop {exit_reasons.get('hard_stop', 0)}, Take Profit {exit_reasons.get('take_profit', 0)}, ATR Stop {exit_reasons.get('atr_stop', 0)}")

    # Sort by total P&L
    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    print(f"\n🏆 TOP PERFORMERS RANKED BY TOTAL P&L:")
    print("=" * 70)

    for i, result in enumerate(results[:5], 1):  # Show top 5
        print(f"\n🥇 RANK {i}: {result['name']}")
        print(f"   📋 {result['description']}")
        print(f"   ⚙️  Contracts: {result['contracts']} | Hard Stop: ${result['hard_stop']} | ATR: {result['atr_mult']}x")
        print(f"   📊 Trades: {result['total_trades']} | Win Rate: {result['win_rate']:.1f}% | Days: 30")
        print(f"   💰 Total P&L: ${result['total_pnl']:,.0f} | Final Balance: ${result['final_balance']:,.0f}")
        print(f"   📈 Total Return: {result['total_return_pct']:.1f}% | Daily Return: {result['daily_return_pct']:.2f}%")
        print(f"   🎯 Profit Factor: {result['profit_factor']:.2f} | Max Drawdown: {result['max_drawdown']:.1f}%")
        print(f"   📊 Sharpe Ratio: {result['sharpe_ratio']:.2f}")
        print(f"   🎪 Exit Reasons: Hard Stop {result['hard_stop_exits']}, Take Profit {result['take_profit_exits']}, ATR Stop {result['atr_stop_exits']}")
        print(f"   💎 Avg Win: ${result['avg_win']:,.0f} | Avg Loss: ${result['avg_loss']:,.0f}")

    # Find absolute best
    best_result = results[0]
    print(f"\n🎯 ABSOLUTE BEST CONFIGURATION:")
    print("=" * 70)
    print(f"🏆 {best_result['name']}: {best_result['description']}")
    print(f"💰 TOTAL P&L: ${best_result['total_pnl']:,.0f} (${best_result['total_return_pct']:.1f}% return)")
    print(f"📊 {best_result['total_trades']} trades over 30 days")
    print(f"⚙️  {best_result['contracts']} contracts, ${best_result['hard_stop']} hard stop, {best_result['atr_mult']}x ATR")
    print(f"🎪 Win Rate: {best_result['win_rate']:.1f}% | Profit Factor: {best_result['profit_factor']:.2f}")
    print(f"📈 Daily Return: {best_result['daily_return_pct']:.2f}% | Max Drawdown: {best_result['max_drawdown']:.1f}%")

    print(f"\n✅ Advanced optimization completed! Use the top configuration for your real account.")
    print("=" * 70)

if __name__ == "__main__":
    main()