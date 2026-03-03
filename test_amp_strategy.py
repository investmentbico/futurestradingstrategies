#!/usr/bin/env python3
"""
AMP MNQ Strategy Test Script
Validates strategy logic against historical data
"""

import pandas as pd
import numpy as np
import sys
import os

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_strategy_logic():
    """Test the strategy logic with historical data"""
    print("🧪 Testing AMP MNQ Strategy Logic")
    print("=" * 50)

    try:
        # Load historical data
        data_file = "data/mnq_1min.csv"
        if not os.path.exists(data_file):
            print("❌ Historical data file not found")
            return False

        df = pd.read_csv(data_file)
        df['timestamp'] = pd.to_datetime(df['time'], unit='s')
        df = df.sort_values('timestamp').reset_index(drop=True)

        print(f"✅ Loaded {len(df)} historical bars")

        # Strategy parameters (same as winning stochd_backtest)
        EMA_FAST = 34
        EMA_SLOW = 89
        STOCH_K = 14
        STOCH_D = 3
        STOCH_SMT = 3
        ATR_LEN = 14
        STOCH_LO = 30
        STOCH_HI = 70
        CONTRACTS = 15
        HARD_STOP_DOLLARS = 80.0

        # Calculate indicators
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values

        # EMAs
        ema_fast = calc_ema(closes, EMA_FAST)
        ema_slow = calc_ema(closes, EMA_SLOW)

        # ATR
        atr = calc_atr(highs, lows, closes, ATR_LEN)

        # Stochastic
        k, d_smooth = calc_stoch(highs, lows, closes, STOCH_K, STOCH_D, STOCH_SMT)

        # Trading simulation
        position = 0
        entry_price = 0
        trades = []
        equity = 100000.0

        for i in range(max(EMA_SLOW, 50), len(df)):
            current_price = closes[i]

            # Skip if indicators not ready
            if np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]) or np.isnan(d_smooth[i]) or np.isnan(atr[i]):
                continue

            # Entry signals
            uptrend = ema_fast[i] > ema_slow[i]
            downtrend = ema_fast[i] < ema_slow[i]
            d_falling = d_smooth[i] < d_smooth[i-1] if i > 0 else False
            d_rising = d_smooth[i] > d_smooth[i-1] if i > 0 else False

            # Long entry
            if (position == 0 and uptrend and d_falling and d_smooth[i] <= STOCH_LO):
                position = CONTRACTS
                entry_price = current_price
                hard_stop = current_price - (HARD_STOP_DOLLARS / (CONTRACTS * 20))
                atr_stop = current_price - (atr[i] * 2.0)
                take_profit = current_price + (atr[i] * 2.0 * 1.8)

                trades.append({
                    'type': 'long',
                    'entry_time': df['timestamp'][i],
                    'entry_price': entry_price,
                    'hard_stop': hard_stop,
                    'atr_stop': atr_stop,
                    'take_profit': take_profit,
                    'contracts': CONTRACTS
                })

            # Short entry
            elif (position == 0 and downtrend and d_rising and d_smooth[i] >= STOCH_HI):
                position = -CONTRACTS
                entry_price = current_price
                hard_stop = current_price + (HARD_STOP_DOLLARS / (CONTRACTS * 20))
                atr_stop = current_price + (atr[i] * 2.0)
                take_profit = current_price - (atr[i] * 2.0 * 1.8)

                trades.append({
                    'type': 'short',
                    'entry_time': df['timestamp'][i],
                    'entry_price': entry_price,
                    'hard_stop': hard_stop,
                    'atr_stop': atr_stop,
                    'take_profit': take_profit,
                    'contracts': CONTRACTS
                })

            # Exit logic
            elif position != 0:
                exit_trade = False
                exit_price = 0
                exit_reason = ""

                if position > 0:  # Long position
                    if current_price <= hard_stop:
                        exit_price = hard_stop
                        exit_reason = "hard_stop"
                        exit_trade = True
                    elif current_price <= atr_stop:
                        exit_price = atr_stop
                        exit_reason = "atr_stop"
                        exit_trade = True
                    elif current_price >= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"
                        exit_trade = True
                else:  # Short position
                    if current_price >= hard_stop:
                        exit_price = hard_stop
                        exit_reason = "hard_stop"
                        exit_trade = True
                    elif current_price >= atr_stop:
                        exit_price = atr_stop
                        exit_reason = "atr_stop"
                        exit_trade = True
                    elif current_price <= take_profit:
                        exit_price = take_profit
                        exit_reason = "take_profit"
                        exit_trade = True

                if exit_trade:
                    # Calculate P&L
                    if position > 0:
                        pnl = (exit_price - entry_price) * CONTRACTS * 20
                    else:
                        pnl = (entry_price - exit_price) * CONTRACTS * 20

                    equity += pnl

                    # Update trade record
                    last_trade = trades[-1]
                    last_trade.update({
                        'exit_time': df['timestamp'][i],
                        'exit_price': exit_price,
                        'exit_reason': exit_reason,
                        'pnl': pnl,
                        'equity': equity
                    })

                    position = 0
                    entry_price = 0

        # Calculate performance metrics
        if trades:
            trade_df = pd.DataFrame(trades)
            winning_trades = trade_df[trade_df['pnl'] > 0]
            losing_trades = trade_df[trade_df['pnl'] < 0]

            total_trades = len(trade_df)
            win_rate = len(winning_trades) / total_trades * 100
            total_pnl = trade_df['pnl'].sum()
            avg_win = winning_trades['pnl'].mean() if len(winning_trades) > 0 else 0
            avg_loss = abs(losing_trades['pnl'].mean()) if len(losing_trades) > 0 else 0
            profit_factor = abs(winning_trades['pnl'].sum() / losing_trades['pnl'].sum()) if len(losing_trades) > 0 else float('inf')
            max_drawdown = (trade_df['equity'] - trade_df['equity'].cummax()).min()

            print("\n📊 Strategy Test Results:")
            print(f"Total Trades: {total_trades}")
            print(f"Win Rate: {win_rate:.1f}%")
            print(f"Total P&L: ${total_pnl:.2f}")
            print(f"Average Win: ${avg_win:.2f}")
            print(f"Average Loss: ${avg_loss:.2f}")
            print(f"Profit Factor: {profit_factor:.2f}")
            print(f"Max Drawdown: ${max_drawdown:.2f}")
            print(f"Final Equity: ${equity:.2f}")
            # Validate against expected performance
            expected_pf = 64.22
            expected_win_rate = 7.6
            expected_total_pnl = 12064511  # $12M+ from stochd_backtest

            if abs(profit_factor - expected_pf) < 10 and abs(win_rate - expected_win_rate) < 2 and abs(total_pnl - expected_total_pnl) < 1000000:
                print("✅ Strategy logic validation PASSED")
                return True
            else:
                print("❌ Strategy logic validation FAILED")
                print(f"Expected PF: {expected_pf}, Got: {profit_factor}")
                print(f"Expected Win Rate: {expected_win_rate}%, Got: {win_rate}%")
                return False
        else:
            print("❌ No trades generated")
            return False

    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        return False

# Helper functions (same as in strategy)
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
    success = test_strategy_logic()
    sys.exit(0 if success else 1)