#!/usr/bin/env python3
"""
Aggressive ES 2min Strategy Test
Testing more aggressive parameters with higher contract sizes
"""

import numpy as np
import pandas as pd
import sys

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

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

def run_aggressive_backtest(params, contracts, data_file='data/es_2min.csv'):
    """Run backtest with aggressive parameters and higher contracts"""
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

        # Risk parameters (scaled with contracts)
        MAX_LOSS_TRADE = 1200.0 * contracts  # Scale risk with contracts
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

                    # Long entry (more aggressive)
                    if uptrend and d_falling and d_sm[idx] <= params["STOCH_LO"]:
                        position = contracts
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
                            'contracts': contracts
                        })

                    # Short entry (more aggressive)
                    elif downtrend and d_rising and d_sm[idx] >= params["STOCH_HI"]:
                        position = -contracts
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
                            'contracts': contracts
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
            'contracts': contracts,
            'params': params
        }

    except Exception as e:
        print(f"Error in backtest: {e}")
        return None

def main():
    print("🚀 Aggressive ES 2min Strategy Test")
    print("=" * 60)
    print("Testing more aggressive parameters with higher contract sizes")
    print()

    # More aggressive parameter combinations
    aggressive_params = [
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 15, "STOCH_HI": 80, "SL_ATR_MULT": 1.1, "TP_RR": 1.8,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Very Aggressive (15/80)"
        },
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 18, "STOCH_HI": 78, "SL_ATR_MULT": 1.1, "TP_RR": 1.8,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Aggressive (18/78)"
        },
        {
            "EMA_FAST": 26, "EMA_SLOW": 68, "STOCH_K": 11, "STOCH_D": 3, "STOCH_SMT": 2,
            "ATR_LEN": 11, "STOCH_LO": 20, "STOCH_HI": 75, "SL_ATR_MULT": 1.1, "TP_RR": 1.6,
            "TRAIL_ATR_MULT": 0.6, "BE_POINTS": 2.8, "name": "Current Optimized (20/75)"
        }
    ]

    # Test with different contract sizes
    contract_sizes = [2, 3, 4]

    results = []

    for params in aggressive_params:
        for contracts in contract_sizes:
            print(f"🔬 Testing: {params['name']} with {contracts} contracts")
            print(f"   Stoch: {params['STOCH_LO']}/{params['STOCH_HI']}, TP_RR: {params['TP_RR']}")

            result = run_aggressive_backtest(params, contracts)
            if result:
                results.append(result)
                print(f"   ✅ {result['total_trades']} trades | ${result['total_pnl']:,.0f} P&L | {result['win_rate']:.1f}% win rate")
                print(f"   📈 ${result['monthly_pnl']:,.0f}/month | {result['monthly_trades']:.0f} trades/month | {result['max_drawdown']:.1f}% max DD")
                print()

    # Summary
    print("\n" + "=" * 80)
    print("🏆 AGGRESSIVE STRATEGY RESULTS SUMMARY")
    print("=" * 80)

    # Sort by monthly P&L
    sorted_results = sorted(results, key=lambda x: x['monthly_pnl'], reverse=True)

    for i, result in enumerate(sorted_results[:9]):  # Top 9 results
        params = result['params']
        print(f"\n{i+1}. {params['name']} - {result['contracts']} contracts:")
        print(f"   Trades: {result['total_trades']} ({result['monthly_trades']:.0f}/month)")
        print(f"   P&L: ${result['total_pnl']:,.0f} (${result['monthly_pnl']:,.0f}/month)")
        print(f"   Win Rate: {result['win_rate']:.1f}% | Max DD: {result['max_drawdown']:.1f}%")
        print(f"   Stoch: {params['STOCH_LO']}/{params['STOCH_HI']} | TP_RR: {params['TP_RR']}")

    # Best performers by different metrics
    print("\n" + "=" * 60)
    print("🎯 BEST PERFORMERS BY CATEGORY")
    print("=" * 60)

    # Highest monthly P&L
    best_pnl = max(results, key=lambda x: x['monthly_pnl'])
    print(f"\n💰 Highest Monthly P&L:")
    print(f"   {best_pnl['params']['name']} - {best_pnl['contracts']} contracts")
    print(f"   ${best_pnl['monthly_pnl']:,.0f}/month | {best_pnl['monthly_trades']:.0f} trades/month")

    # Highest win rate
    best_winrate = max(results, key=lambda x: x['win_rate'])
    print(f"\n🎯 Highest Win Rate:")
    print(f"   {best_winrate['params']['name']} - {best_winrate['contracts']} contracts")
    print(f"   {best_winrate['win_rate']:.1f}% win rate | ${best_winrate['monthly_pnl']:,.0f}/month")

    # Lowest drawdown
    best_dd = min(results, key=lambda x: x['max_drawdown'])
    print(f"\n🛡️  Lowest Drawdown:")
    print(f"   {best_dd['params']['name']} - {best_dd['contracts']} contracts")
    print(f"   {best_dd['max_drawdown']:.1f}% max DD | ${best_dd['monthly_pnl']:,.0f}/month")

if __name__ == "__main__":
    main()