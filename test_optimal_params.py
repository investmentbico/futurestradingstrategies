#!/usr/bin/env python3
"""
Test Top Optimal Parameter Combinations
"""

import numpy as np
import pandas as pd
import sys
import os

# Add current directory to path to import backtest functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import backtest functions (assuming they exist in comprehensive_backtest.py)
# For now, I'll copy the necessary functions

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

def run_full_backtest(params, data_file='data/es_2min.csv'):
    """Run complete backtest with given parameters"""
    try:
        # Load data
        df = pd.read_csv(data_file)
        df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
        df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
        df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

        # Extract data
        close = df['close'].values
        high = df['high'].values
        low = df['low'].values
        open_price = df['open'].values

        # Calculate indicators
        ema_fast = calc_ema(close, params["EMA_FAST"])
        ema_slow = calc_ema(close, params["EMA_SLOW"])
        atr = calc_atr(high, low, close, params["ATR_LEN"])
        k_sm, d_sm = calc_stoch(high, low, close, params["STOCH_K"], params["STOCH_D"], params["STOCH_SMT"])

        # Trading parameters
        CONTRACTS = 2
        MAX_LOSS_TRADE = 1200.0
        COMMISSION_PER_CONTRACT = 0.02
        COMMISSION_PER_TRADE = 14.78

        # Initialize tracking
        position = 0
        entry_price = 0
        stop_loss = 0
        take_profit = 0
        trailing_stop = 0
        breakeven_triggered = False

        trades = []
        equity = 100000.0
        peak_equity = equity
        max_drawdown = 0

        for idx in range(50, len(df)):
            current_price = close[idx]
            current_high = high[idx]
            current_low = low[idx]

            if position == 0:  # No position
                if not np.isnan(atr[idx]) and not np.isnan(d_sm[idx]):
                    uptrend = ema_fast[idx] > ema_slow[idx]
                    downtrend = ema_fast[idx] < ema_slow[idx]
                    d_falling = d_sm[idx] < d_sm[idx-1] if idx >= 1 else False
                    d_rising = d_sm[idx] > d_sm[idx-1] if idx >= 1 else False

                    # Long entry
                    if uptrend and d_falling and d_sm[idx] <= params["STOCH_LO"]:
                        position = CONTRACTS
                        entry_price = current_price
                        atr_value = atr[idx]
                        stop_loss = entry_price - (atr_value * params["SL_ATR_MULT"])
                        take_profit = entry_price + (atr_value * params["SL_ATR_MULT"] * params["TP_RR"])
                        trailing_stop = stop_loss
                        breakeven_triggered = False

                        trades.append({
                            'entry_time': df['timestamp'][idx],
                            'entry_price': entry_price,
                            'direction': 'long',
                            'contracts': CONTRACTS
                        })

                    # Short entry
                    elif downtrend and d_rising and d_sm[idx] >= params["STOCH_HI"]:
                        position = -CONTRACTS
                        entry_price = current_price
                        atr_value = atr[idx]
                        stop_loss = entry_price + (atr_value * params["SL_ATR_MULT"])
                        take_profit = entry_price - (atr_value * params["SL_ATR_MULT"] * params["TP_RR"])
                        trailing_stop = stop_loss
                        breakeven_triggered = False

                        trades.append({
                            'entry_time': df['timestamp'][idx],
                            'entry_price': entry_price,
                            'direction': 'short',
                            'contracts': CONTRACTS
                        })

            else:  # In position
                # Update trailing stop
                if position > 0:  # Long position
                    # Breakeven logic
                    if not breakeven_triggered and current_price >= entry_price + params["BE_POINTS"]:
                        stop_loss = entry_price
                        breakeven_triggered = True

                    # Trailing stop
                    new_trailing = current_high - (atr[idx] * params["TRAIL_ATR_MULT"])
                    if new_trailing > trailing_stop:
                        trailing_stop = new_trailing
                        stop_loss = max(stop_loss, trailing_stop)

                    # Exit conditions
                    if current_low <= stop_loss or current_high >= take_profit:
                        exit_price = stop_loss if current_low <= stop_loss else take_profit
                        pnl = (exit_price - entry_price) * position * 50  # ES point value
                        pnl -= COMMISSION_PER_TRADE + (abs(position) * COMMISSION_PER_CONTRACT)

                        trades[-1].update({
                            'exit_time': df['timestamp'][idx],
                            'exit_price': exit_price,
                            'pnl': pnl,
                            'exit_reason': 'stop_loss' if exit_price == stop_loss else 'take_profit'
                        })

                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)

                        position = 0

                else:  # Short position
                    # Breakeven logic
                    if not breakeven_triggered and current_price <= entry_price - params["BE_POINTS"]:
                        stop_loss = entry_price
                        breakeven_triggered = True

                    # Trailing stop
                    new_trailing = current_low + (atr[idx] * params["TRAIL_ATR_MULT"])
                    if new_trailing < trailing_stop:
                        trailing_stop = new_trailing
                        stop_loss = min(stop_loss, trailing_stop)

                    # Exit conditions
                    if current_high >= stop_loss or current_low <= take_profit:
                        exit_price = stop_loss if current_high >= stop_loss else take_profit
                        pnl = (entry_price - exit_price) * abs(position) * 50  # ES point value
                        pnl -= COMMISSION_PER_TRADE + (abs(position) * COMMISSION_PER_CONTRACT)

                        trades[-1].update({
                            'exit_time': df['timestamp'][idx],
                            'exit_price': exit_price,
                            'pnl': pnl,
                            'exit_reason': 'stop_loss' if exit_price == stop_loss else 'take_profit'
                        })

                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)

                        position = 0

        # Calculate statistics
        total_trades = len([t for t in trades if 'pnl' in t])
        winning_trades = len([t for t in trades if 'pnl' in t and t['pnl'] > 0])
        losing_trades = len([t for t in trades if 'pnl' in t and t['pnl'] < 0])
        win_rate = winning_trades / total_trades * 100 if total_trades > 0 else 0

        total_pnl = sum([t['pnl'] for t in trades if 'pnl' in t])
        avg_win = np.mean([t['pnl'] for t in trades if 'pnl' in t and t['pnl'] > 0]) if winning_trades > 0 else 0
        avg_loss = np.mean([t['pnl'] for t in trades if 'pnl' in t and t['pnl'] < 0]) if losing_trades > 0 else 0

        # Estimate monthly performance
        trading_days = len(df['timestamp'].dt.date.unique())
        months = trading_days / 20.83  # Average trading days per month
        monthly_pnl = total_pnl / months if months > 0 else 0
        monthly_trades = total_trades / months if months > 0 else 0

        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'monthly_pnl': monthly_pnl,
            'monthly_trades': monthly_trades,
            'max_drawdown': max_drawdown,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'params': params
        }

    except Exception as e:
        print(f"Error in backtest: {e}")
        return None

def main():
    print("🧪 Testing Top Optimal Parameter Combinations")
    print("=" * 60)

    # Top 3 optimal combinations from optimization
    test_params = [
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 25, "STOCH_HI": 80, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Optimal #1 (285 monthly)"
        },
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 20, "STOCH_HI": 75, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Optimal #2 (279 monthly)"
        },
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 20, "STOCH_HI": 80, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Optimal #3 (209 monthly)"
        },
        # Original for comparison
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 27, "STOCH_HI": 73, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Original (203 trades)"
        }
    ]

    results = []

    for params in test_params:
        print(f"\n🔬 Testing: {params['name']}")
        print(f"   EMA: {params['EMA_FAST']}/{params['EMA_SLOW']}, Stoch: {params['STOCH_LO']}/{params['STOCH_HI']}")

        result = run_full_backtest(params)
        if result:
            results.append(result)
            print(f"   ✅ {result['total_trades']} trades | ${result['total_pnl']:,.0f} P&L | {result['win_rate']:.1f}% win rate")
            print(f"   📈 ${result['monthly_pnl']:,.0f}/month | {result['monthly_trades']:.0f} trades/month | {result['max_drawdown']:.1f}% max DD")

    # Summary
    print("\n" + "=" * 80)
    print("🏆 FINAL RESULTS SUMMARY")
    print("=" * 80)

    for result in results:
        params = result['params']
        print(f"\n{params['name']}:")
        print(f"   Trades: {result['total_trades']} ({result['monthly_trades']:.0f}/month)")
        print(f"   P&L: ${result['total_pnl']:,.0f} (${result['monthly_pnl']:,.0f}/month)")
        print(f"   Win Rate: {result['win_rate']:.1f}%")
        print(f"   Max DD: {result['max_drawdown']:.1f}%")
        print(f"   Avg Win/Loss: ${result['avg_win']:,.0f} / ${result['avg_loss']:,.0f}")

if __name__ == "__main__":
    main()