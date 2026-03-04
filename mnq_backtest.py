#!/usr/bin/env python3
"""
MNQ Strategy Backtest with Real Historical Data
===============================================

Backtests the live trading strategy using 6 months of real MNQ data from Yahoo Finance.
Provides comprehensive performance metrics including win rate, drawdown, P&L, etc.
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# STRATEGY PARAMETERS (Same as live bot)
# =============================================================================

STRATEGY_PARAMS = {
    'EMA_FAST': 21,
    'EMA_SLOW': 55,
    'STOCH_LO': 25,
    'STOCH_HI': 75,
    'ATR_PERIOD': 9,
    'SL_ATR_MULT': 2.0,
    'TP_RR': 1.8  # Take profit at 1.8:1 reward-to-risk ratio
}

RISK_PARAMS = {
    'CONTRACTS': 3,
    'MAX_LOSS_TRADE': 75.0,  # $75 hard stop per trade
    'HARD_STOP_DOLLARS': 75.0  # $75 hard stop
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

class MNQBacktest:
    """Backtest engine for MNQ strategy"""

    def __init__(self, symbol='MNQ=F', months=6):
        self.symbol = symbol
        self.months = months
        self.data = None
        self.results = []

    def download_data(self):
        """Download historical data from Yahoo Finance"""
        print(f"📊 Downloading {self.months} months of {self.symbol} data...")

        end_date = datetime.now()
        start_date = end_date - timedelta(days=self.months*30)

        ticker = yf.Ticker(self.symbol)
        df = ticker.history(start=start_date, end=end_date, interval='1d')

        if df.empty:
            raise ValueError(f"No data available for {self.symbol}")

        # Clean and prepare data
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
        df.columns = ['open', 'high', 'low', 'close', 'volume']

        # Handle timezone - convert to US/Eastern if already tz-aware
        if df.index.tz is not None:
            df.index = df.index.tz_convert('US/Eastern')
        else:
            df.index = df.index.tz_localize('UTC').tz_convert('US/Eastern')

        # For daily data, no need to filter trading hours
        # Remove weekends for futures data
        df = df[df.index.weekday < 5]

        self.data = df
        print(f"✅ Downloaded {len(df)} bars from {df.index[0]} to {df.index[-1]}")
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
        df['atr'] = calc_atr(high, low, close, STRATEGY_PARAMS['ATR_PERIOD'])

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
        """Check for entry signals"""
        # Long signal: Uptrend + D falling + Stoch oversold
        if (row['uptrend'] and
            row['d_falling'] and
            row['stoch_oversold'] and
            row['atr'] > 0):
            return 'long'

        # Short signal: Downtrend + D rising + Stoch overbought
        if (row['downtrend'] and
            row['d_rising'] and
            row['stoch_overbought'] and
            row['atr'] > 0):
            return 'short'

        return None

    def check_exit_signals(self, row, position, entry_price, stop_loss, take_profit):
        """Check for exit signals"""
        current_price = row['close']

        if position > 0:  # Long position
            # Hard stop loss
            hard_stop = entry_price - (RISK_PARAMS['HARD_STOP_DOLLARS'] / (abs(position) * 20))
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
            hard_stop = entry_price + (RISK_PARAMS['HARD_STOP_DOLLARS'] / (abs(position) * 20))
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
                    quantity = RISK_PARAMS['CONTRACTS']
                    atr_value = row['atr']

                    if signal == 'long':
                        entry_price = row['close']
                        stop_loss = entry_price - (atr_value * STRATEGY_PARAMS['SL_ATR_MULT'])
                        take_profit = entry_price + (atr_value * STRATEGY_PARAMS['SL_ATR_MULT'] * STRATEGY_PARAMS['TP_RR'])
                        position = quantity
                    else:  # short
                        entry_price = row['close']
                        stop_loss = entry_price + (atr_value * STRATEGY_PARAMS['SL_ATR_MULT'])
                        take_profit = entry_price - (atr_value * STRATEGY_PARAMS['SL_ATR_MULT'] * STRATEGY_PARAMS['TP_RR'])
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
        print("📊 MNQ STRATEGY BACKTEST RESULTS (6 Months - Daily Data)")
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
            print(f"   Annualized Return: {((r['final_equity']/10000)**(365/(self.months*30)) - 1):.2%}")
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
    """Run the backtest"""
    try:
        # Initialize backtest
        backtest = MNQBacktest(symbol='MNQ=F', months=6)

        # Download data
        backtest.download_data()

        # Run backtest
        results = backtest.run_backtest()

        # Print results
        backtest.print_results()

        print("\n✅ Backtest completed successfully!")
        print("📈 Strategy uses EMA 21/55 crossover with Stochastic 25/75 extremes")
        print("⚡ Risk management: 3 contracts, ATR-based stops, $75 hard stop")
        print("⏰ Backtest run on 5-minute MNQ data (Yahoo Finance limitation)")

    except Exception as e:
        print(f"❌ Backtest failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()