#!/usr/bin/env python3
"""
Hard Stop Sweep Backtest - Last 3 Months
Tests: $30, $50, $60, $75, $100, $130, $200 hard stops with 1 contract MNQ
"""

import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Strategy Parameters (same as live bot)
STRATEGY_PARAMS = {
    "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
    "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
}

POINT_VALUE = 2.0  # MNQ $2 per 0.25 tick, $20 per full point... but let's use 20
MNQ_POINT_VALUE = 20  # $20 per point for MNQ

# =============================================================================
# INDICATORS
# =============================================================================

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

# =============================================================================
# BACKTEST ENGINE
# =============================================================================

def run_backtest(hard_stop, contracts=1, data_file='data/mnq_1min.csv', months=3):
    """Run backtest with specific hard stop, 1 contract, last N months"""

    df = pd.read_csv(data_file)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter last N months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=months)
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

    df['uptrend'] = df['ema_fast'] > df['ema_slow']
    df['downtrend'] = df['ema_fast'] < df['ema_slow']
    df['d_falling'] = df['stoch_d'] < df['stoch_d'].shift(1)
    df['d_rising'] = df['stoch_d'] > df['stoch_d'].shift(1)

    # Trading state
    position = 0
    entry_price = 0
    stop_loss = 0
    take_profit = 0
    trades = []
    total_pnl = 0
    cumulative_pnl = 0
    cumulative_pnl_history = []
    daily_pnls = {}
    daily_pnl = 0
    daily_reset_date = df['timestamp'].iloc[0].date()
    hard_stop_hits = 0
    atr_stop_hits = 0
    tp_hits = 0

    for idx in range(50, len(df)):
        row = df.iloc[idx]
        current_price = row['close']

        current_date = row['timestamp'].date()
        if current_date != daily_reset_date:
            daily_pnls[daily_reset_date] = daily_pnl
            daily_pnl = 0
            daily_reset_date = current_date

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
                    'entry_time': row['timestamp'], 'entry_price': entry_price,
                    'direction': 'long', 'contracts': contracts, 'hard_stop': hard_stop
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
                    'entry_time': row['timestamp'], 'entry_price': entry_price,
                    'direction': 'short', 'contracts': contracts, 'hard_stop': hard_stop
                })

        elif position != 0:
            exit_reason = None
            exit_price = current_price

            if position > 0:  # Long
                hard_stop_price = entry_price - (hard_stop / (contracts * MNQ_POINT_VALUE))
                if current_price <= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif current_price <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif current_price >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
            else:  # Short
                hard_stop_price = entry_price + (hard_stop / (contracts * MNQ_POINT_VALUE))
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
                if position > 0:
                    pnl = (exit_price - entry_price) * abs(position) * MNQ_POINT_VALUE
                else:
                    pnl = (entry_price - exit_price) * abs(position) * MNQ_POINT_VALUE

                # Commissions + spread
                commissions = 14.78 + (abs(position) * 0.02)
                spread_cost = abs(position) * 0.50
                pnl -= (commissions + spread_cost)

                trades[-1].update({
                    'exit_time': row['timestamp'], 'exit_price': exit_price,
                    'pnl': pnl, 'exit_reason': exit_reason
                })

                if exit_reason == 'hard_stop':
                    hard_stop_hits += 1
                elif exit_reason == 'atr_stop':
                    atr_stop_hits += 1
                elif exit_reason == 'take_profit':
                    tp_hits += 1

                total_pnl += pnl
                daily_pnl += pnl
                cumulative_pnl += pnl
                cumulative_pnl_history.append(cumulative_pnl)

                position = 0
                entry_price = 0
                stop_loss = 0
                take_profit = 0

    # Record last day
    daily_pnls[daily_reset_date] = daily_pnl

    # Statistics
    completed = [t for t in trades if 'pnl' in t]
    total_trades = len(completed)
    wins = [t for t in completed if t['pnl'] > 0]
    losses = [t for t in completed if t['pnl'] <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0
    total_wins = sum(t['pnl'] for t in wins)
    total_losses = abs(sum(t['pnl'] for t in losses))
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

    # Max drawdown
    max_dd = 0
    if cumulative_pnl_history:
        peak = cumulative_pnl_history[0]
        for p in cumulative_pnl_history:
            if p > peak:
                peak = p
            dd = peak - p
            if dd > max_dd:
                max_dd = dd

    max_daily_dd = min(daily_pnls.values()) if daily_pnls else 0
    trading_days = len(df['timestamp'].dt.date.unique())

    # Best/worst trade
    best_trade = max(completed, key=lambda t: t['pnl'])['pnl'] if completed else 0
    worst_trade = min(completed, key=lambda t: t['pnl'])['pnl'] if completed else 0

    return {
        'hard_stop': hard_stop,
        'contracts': contracts,
        'total_trades': total_trades,
        'winning_trades': len(wins),
        'losing_trades': len(losses),
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'max_drawdown': max_dd,
        'max_daily_drawdown': max_daily_dd,
        'trading_days': trading_days,
        'hard_stop_hits': hard_stop_hits,
        'atr_stop_hits': atr_stop_hits,
        'tp_hits': tp_hits,
        'best_trade': best_trade,
        'worst_trade': worst_trade,
        'start_date': start_date,
        'end_date': end_date,
        'pnl_per_day': total_pnl / trading_days if trading_days > 0 else 0
    }

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 90)
    print("  HARD STOP SWEEP BACKTEST - 1 CONTRACT MNQ - LAST 3 MONTHS")
    print("  Strategy: EMA 21/55 | Stoch 25/75 | ATR 9 | SL 2.0 ATR | TP 1.8 RR")
    print("=" * 90)

    hard_stops = [100, 150, 200, 300, 400, 500]
    results = []

    for hs in hard_stops:
        print(f"\n  Testing ${hs} hard stop...")
        result = run_backtest(hard_stop=hs, contracts=1, months=3)
        results.append(result)

    # Display period info
    r0 = results[0]
    print(f"\n  Period: {r0['start_date'].date()} to {r0['end_date'].date()} ({r0['trading_days']} trading days)")

    # Results table
    print("\n" + "=" * 90)
    print(f"  {'Hard':>6} | {'Trades':>6} | {'Win%':>6} | {'Total P&L':>10} | {'Avg Win':>8} | {'Avg Loss':>9} | {'PF':>5} | {'Max DD':>8} | {'$/Day':>7}")
    print(f"  {'Stop':>6} | {'':>6} | {'':>6} | {'':>10} | {'':>8} | {'':>9} | {'':>5} | {'':>8} | {'':>7}")
    print("  " + "-" * 86)

    for r in results:
        print(f"  ${r['hard_stop']:>5} | {r['total_trades']:>6} | {r['win_rate']:>5.1f}% | ${r['total_pnl']:>9,.2f} | ${r['avg_win']:>7,.2f} | ${r['avg_loss']:>8,.2f} | {r['profit_factor']:>5.2f} | ${r['max_drawdown']:>7,.2f} | ${r['pnl_per_day']:>6,.2f}")

    # Exit reason breakdown
    print("\n" + "=" * 90)
    print("  EXIT REASON BREAKDOWN")
    print("  " + "-" * 86)
    print(f"  {'Hard Stop':>9} | {'Hard Stop Exits':>15} | {'ATR Stop Exits':>14} | {'Take Profit':>11} | {'HS % of Exits':>13}")
    print("  " + "-" * 86)

    for r in results:
        total = r['hard_stop_hits'] + r['atr_stop_hits'] + r['tp_hits']
        hs_pct = (r['hard_stop_hits'] / total * 100) if total > 0 else 0
        print(f"  ${r['hard_stop']:>8} | {r['hard_stop_hits']:>15} | {r['atr_stop_hits']:>14} | {r['tp_hits']:>11} | {hs_pct:>12.1f}%")

    # Rankings
    print("\n" + "=" * 90)
    print("  RANKINGS")
    print("  " + "-" * 86)

    best_pnl = max(results, key=lambda x: x['total_pnl'])
    best_wr = max(results, key=lambda x: x['win_rate'])
    best_pf = max(results, key=lambda x: x['profit_factor'])
    best_dd = min(results, key=lambda x: x['max_drawdown'])
    best_daily = max(results, key=lambda x: x['pnl_per_day'])

    print(f"  Best Total P&L:      ${best_pnl['hard_stop']} stop -> ${best_pnl['total_pnl']:,.2f}")
    print(f"  Best Win Rate:       ${best_wr['hard_stop']} stop -> {best_wr['win_rate']:.1f}%")
    print(f"  Best Profit Factor:  ${best_pf['hard_stop']} stop -> {best_pf['profit_factor']:.2f}")
    print(f"  Lowest Max Drawdown: ${best_dd['hard_stop']} stop -> ${best_dd['max_drawdown']:,.2f}")
    print(f"  Best P&L Per Day:    ${best_daily['hard_stop']} stop -> ${best_daily['pnl_per_day']:,.2f}/day")

    print(f"\n  Best Trade:  ${best_pnl['best_trade']:,.2f}")
    print(f"  Worst Trade: ${best_pnl['worst_trade']:,.2f}")

    # Save to CSV
    rows = []
    for r in results:
        rows.append({k: v for k, v in r.items() if k not in ('start_date', 'end_date')})
    df_out = pd.DataFrame(rows)
    df_out.to_csv('results/hard_stop_sweep_3mo.csv', index=False)
    print(f"\n  Results saved to: results/hard_stop_sweep_3mo.csv")
    print("=" * 90)

if __name__ == "__main__":
    main()
