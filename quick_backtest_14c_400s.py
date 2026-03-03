#!/usr/bin/env python3
"""
Quick backtest for 14 contracts, $400 hard stop combination
"""

import pandas as pd
import numpy as np

# Strategy parameters
STRATEGY_PARAMS = {
    "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 2.0, "TP_RR": 1.8
}

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

def run_quick_backtest(contracts=14, hard_stop=400):
    # Load data
    df = pd.read_csv('data/mnq_1min.csv')
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter last 6 months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=6)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)

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
    trades = []
    total_pnl = 0

    # Trading loop
    for idx in range(50, len(df)):
        row = df.iloc[idx]
        current_price = row['close']

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
                hard_stop_price = entry_price - (hard_stop / (contracts * 20))
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
                hard_stop_price = entry_price + (hard_stop / (contracts * 20))
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
                    pnl = (exit_price - entry_price) * abs(position) * 20
                else:
                    pnl = (entry_price - exit_price) * abs(position) * 20

                # Add commissions
                commissions = 14.78 + (abs(position) * 0.02)
                pnl -= commissions

                # Record trade
                trades[-1].update({
                    'exit_time': row['timestamp'],
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'exit_reason': exit_reason
                })

                total_pnl += pnl
                position = 0
                entry_price = 0
                stop_loss = 0
                take_profit = 0

    # Calculate statistics
    completed_trades = [t for t in trades if 'pnl' in t]
    total_trades = len(completed_trades)
    winning_trades = len([t for t in completed_trades if t['pnl'] > 0])
    losing_trades = len([t for t in completed_trades if t['pnl'] <= 0])
    win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0

    avg_win = np.mean([t['pnl'] for t in completed_trades if t['pnl'] > 0]) if winning_trades > 0 else 0
    avg_loss = np.mean([t['pnl'] for t in completed_trades if t['pnl'] <= 0]) if losing_trades > 0 else 0

    total_wins = sum([t['pnl'] for t in completed_trades if t['pnl'] > 0])
    total_losses = abs(sum([t['pnl'] for t in completed_trades if t['pnl'] <= 0]))
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

    trading_days = len(df['timestamp'].dt.date.unique())

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
        'trading_days': trading_days
    }

if __name__ == "__main__":
    print("🔬 Testing Multiple Hard Stop Levels with 15 Contracts")
    print("=" * 70)

    stops_to_test = [100, 80]

    for stop in stops_to_test:
        print(f"\n📊 Testing ${stop} hard stop:")
        print("-" * 40)

        result = run_quick_backtest(contracts=15, hard_stop=stop)
        print(f"Total Trades: {result['total_trades']}")
        print(f"Win Rate: {result['win_rate']:.1f}%")
        print(f"Total P&L: ${result['total_pnl']:,.0f}")
        print(f"Profit Factor: {result['profit_factor']:.2f}")
        print(f"Avg Win: ${result['avg_win']:,.0f}")
        print(f"Avg Loss: ${result['avg_loss']:,.0f}")
        print(f"Trading Days: {result['trading_days']}")

    print("\n" + "=" * 70)
    print("✅ All tests completed")