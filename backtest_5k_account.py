#!/usr/bin/env python3
"""
Backtest for $5K Live Account - MNQ & MES
==========================================
1 contract, last 3 months, multiple stop levels
Find the safest stop loss that doesn't blow the account.
Realistic Tastytrade commissions: $2.50 RT per contract for futures.
"""

import pandas as pd
import numpy as np
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# STRATEGY PARAMETERS (EMA 21/55 + Stochastic + ATR)
# =============================================================================
STRATEGY = {
    "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 2.0,
    "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
}

# Instrument specs
INSTRUMENTS = {
    'MNQ': {'file': 'data/mnq_1min.csv', 'point_value': 2.0, 'tick': 0.25, 'commission_rt': 2.52},
    'MES': {'file': 'data/mes_1min.csv', 'point_value': 5.0, 'tick': 0.25, 'commission_rt': 2.52},
}

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
    denom = highest_high - lowest_low
    denom[denom == 0] = 1
    k = 100 * (close[k_period-1:] - lowest_low) / denom
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    valid = ~np.isnan(k)
    d = calc_ema(k[valid], d_period)
    d_full = np.full(len(k), np.nan)
    d_full[valid] = d
    valid2 = ~np.isnan(d_full)
    d_smooth = calc_ema(d_full[valid2], smooth)
    d_smooth_full = np.full(len(d_full), np.nan)
    d_smooth_full[valid2] = d_smooth
    return k, d_smooth_full

# =============================================================================
# BACKTEST ENGINE
# =============================================================================
def run_backtest(symbol, contracts, hard_stop, tp_rr, starting_equity, months=3):
    spec = INSTRUMENTS[symbol]
    pv = spec['point_value']
    commission = spec['commission_rt']

    df = pd.read_csv(spec['file'])
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=months)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)

    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values

    df['ema_fast'] = calc_ema(closes, STRATEGY["EMA_FAST"])
    df['ema_slow'] = calc_ema(closes, STRATEGY["EMA_SLOW"])
    df['atr'] = calc_atr(highs, lows, closes, STRATEGY["ATR_LEN"])
    k_sm, d_sm = calc_stoch(highs, lows, closes, STRATEGY["STOCH_K"], STRATEGY["STOCH_D"], STRATEGY["STOCH_SMT"])
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
    equity = starting_equity
    peak_equity = starting_equity

    trades = []
    daily_pnls = {}
    daily_pnl = 0
    daily_reset_date = df['timestamp'].iloc[0].date()
    max_drawdown_dollars = 0
    max_drawdown_pct = 0
    equity_curve = [starting_equity]

    for idx in range(55, len(df)):
        row = df.iloc[idx]
        price = row['close']
        current_date = row['timestamp'].date()

        if current_date != daily_reset_date:
            daily_pnls[daily_reset_date] = daily_pnl
            daily_pnl = 0
            daily_reset_date = current_date

        # Entry
        if position == 0 and not np.isnan(row['atr']):
            atr_val = row['atr']
            # Long
            if row['uptrend'] and row['d_falling'] and row['stoch_d'] <= STRATEGY["STOCH_LO"]:
                position = contracts
                entry_price = price
                stop_loss = price - (atr_val * STRATEGY["SL_ATR_MULT"])
                take_profit = price + (atr_val * STRATEGY["SL_ATR_MULT"] * tp_rr)
                trades.append({'entry_time': row['timestamp'], 'entry_price': price, 'direction': 'long', 'contracts': contracts})
            # Short
            elif row['downtrend'] and row['d_rising'] and row['stoch_d'] >= STRATEGY["STOCH_HI"]:
                position = -contracts
                entry_price = price
                stop_loss = price + (atr_val * STRATEGY["SL_ATR_MULT"])
                take_profit = price - (atr_val * STRATEGY["SL_ATR_MULT"] * tp_rr)
                trades.append({'entry_time': row['timestamp'], 'entry_price': price, 'direction': 'short', 'contracts': contracts})

        # Exit
        elif position != 0:
            exit_reason = None
            exit_price = price

            if position > 0:
                hard_stop_price = entry_price - (hard_stop / (contracts * pv))
                if price <= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif price <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif price >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
            else:
                hard_stop_price = entry_price + (hard_stop / (abs(position) * pv))
                if price >= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif price >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif price <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            if exit_reason:
                if position > 0:
                    pnl = (exit_price - entry_price) * abs(position) * pv
                else:
                    pnl = (entry_price - exit_price) * abs(position) * pv
                pnl -= commission  # RT commission per contract

                trades[-1].update({'exit_time': row['timestamp'], 'exit_price': exit_price, 'pnl': pnl, 'exit_reason': exit_reason})

                equity += pnl
                daily_pnl += pnl

                if equity > peak_equity:
                    peak_equity = equity
                dd = peak_equity - equity
                if dd > max_drawdown_dollars:
                    max_drawdown_dollars = dd
                dd_pct = dd / peak_equity * 100 if peak_equity > 0 else 0
                if dd_pct > max_drawdown_pct:
                    max_drawdown_pct = dd_pct

                equity_curve.append(equity)
                position = 0

    daily_pnls[daily_reset_date] = daily_pnl

    completed = [t for t in trades if 'pnl' in t]
    total_trades = len(completed)
    wins = [t for t in completed if t['pnl'] > 0]
    losses = [t for t in completed if t['pnl'] <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    total_pnl = sum(t['pnl'] for t in completed)
    total_wins = sum(t['pnl'] for t in wins)
    total_losses = abs(sum(t['pnl'] for t in losses))
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')
    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0
    trading_days = len(df['timestamp'].dt.date.unique())
    max_daily_dd = min(daily_pnls.values()) if daily_pnls else 0

    # Exit reasons breakdown
    hard_stops = len([t for t in completed if t['exit_reason'] == 'hard_stop'])
    atr_stops = len([t for t in completed if t['exit_reason'] == 'atr_stop'])
    tp_exits = len([t for t in completed if t['exit_reason'] == 'take_profit'])

    return {
        'symbol': symbol, 'contracts': contracts, 'hard_stop': hard_stop, 'tp_rr': tp_rr,
        'starting_equity': starting_equity, 'final_equity': equity,
        'total_pnl': total_pnl, 'return_pct': (equity - starting_equity) / starting_equity * 100,
        'total_trades': total_trades, 'winning_trades': len(wins), 'losing_trades': len(losses),
        'win_rate': win_rate, 'avg_win': avg_win, 'avg_loss': avg_loss,
        'profit_factor': profit_factor, 'trading_days': trading_days,
        'max_drawdown': max_drawdown_dollars, 'max_drawdown_pct': max_drawdown_pct,
        'max_daily_drawdown': max_daily_dd,
        'hard_stop_exits': hard_stops, 'atr_stop_exits': atr_stops, 'tp_exits': tp_exits,
        'start_date': df['timestamp'].min(), 'end_date': df['timestamp'].max(),
        'equity_curve': equity_curve
    }


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    STARTING_EQUITY = 5000.0
    CONTRACTS = 1
    MONTHS = 3

    # Test multiple hard stops and TP ratios
    hard_stops = [25, 50, 75, 100, 150, 200]
    tp_rrs = [1.5, 2.0, 2.5, 3.0]

    print("=" * 90)
    print(f"$5K LIVE ACCOUNT BACKTEST - Last {MONTHS} Months - 1 Contract")
    print(f"Strategy: EMA 21/55 + Stoch 25/75 + ATR 9")
    print("=" * 90)

    all_results = []

    for symbol in ['MNQ', 'MES']:
        spec = INSTRUMENTS[symbol]
        print(f"\n{'='*90}")
        print(f"  {symbol} (Point Value: ${spec['point_value']}, Commission: ${spec['commission_rt']} RT)")
        print(f"{'='*90}")

        for hs in hard_stops:
            for tp in tp_rrs:
                result = run_backtest(symbol, CONTRACTS, hs, tp, STARTING_EQUITY, MONTHS)
                all_results.append(result)

        # Print results for this symbol sorted by profit factor
        symbol_results = [r for r in all_results if r['symbol'] == symbol]
        # Filter to only profitable combos
        profitable = sorted([r for r in symbol_results if r['total_pnl'] > 0], key=lambda x: x['profit_factor'], reverse=True)

        if profitable:
            print(f"\n{'#':>2} {'Stop':>5} {'TP':>4} {'Trades':>6} {'WinRate':>7} {'TotalPnL':>10} {'Return':>8} {'PF':>5} {'MaxDD$':>8} {'MaxDD%':>7} {'AvgWin':>8} {'AvgLoss':>8} {'HS/ATR/TP':>10}")
            print("-" * 110)
            for i, r in enumerate(profitable[:15]):
                print(f"{i+1:>2} ${r['hard_stop']:>4} {r['tp_rr']:>4.1f} {r['total_trades']:>6} {r['win_rate']:>6.1f}% ${r['total_pnl']:>8,.0f} {r['return_pct']:>7.1f}% {r['profit_factor']:>5.2f} ${r['max_drawdown']:>7,.0f} {r['max_drawdown_pct']:>6.1f}% ${r['avg_win']:>7,.0f} ${r['avg_loss']:>7,.0f} {r['hard_stop_exits']}/{r['atr_stop_exits']}/{r['tp_exits']}")
        else:
            print("  No profitable combinations found!")

        # Show worst-case drawdown for all combos
        print(f"\n  All combos max drawdown vs $5K equity:")
        for r in sorted(symbol_results, key=lambda x: x['max_drawdown_pct']):
            acct_blown = "BLOWN" if r['max_drawdown'] >= STARTING_EQUITY * 0.8 else "OK" if r['max_drawdown_pct'] < 30 else "RISKY"
            print(f"    ${r['hard_stop']:>4} stop, {r['tp_rr']:.1f}x TP: DD=${r['max_drawdown']:>7,.0f} ({r['max_drawdown_pct']:.1f}%) PnL=${r['total_pnl']:>8,.0f} [{acct_blown}]")

    # Final summary - best combos across both instruments
    print(f"\n{'='*90}")
    print("BEST COMBINATIONS FOR $5K ACCOUNT (Safe: MaxDD < 30%)")
    print(f"{'='*90}")

    safe = [r for r in all_results if r['max_drawdown_pct'] < 30 and r['total_pnl'] > 0]
    safe_sorted = sorted(safe, key=lambda x: x['profit_factor'], reverse=True)

    if safe_sorted:
        print(f"\n{'#':>2} {'Sym':>4} {'Stop':>5} {'TP':>4} {'Trades':>6} {'Days':>5} {'WinRate':>7} {'TotalPnL':>10} {'Return':>8} {'PF':>5} {'MaxDD$':>8} {'MaxDD%':>7} {'FinalEq':>9}")
        print("-" * 110)
        for i, r in enumerate(safe_sorted[:20]):
            print(f"{i+1:>2} {r['symbol']:>4} ${r['hard_stop']:>4} {r['tp_rr']:>4.1f} {r['total_trades']:>6} {r['trading_days']:>5} {r['win_rate']:>6.1f}% ${r['total_pnl']:>8,.0f} {r['return_pct']:>7.1f}% {r['profit_factor']:>5.2f} ${r['max_drawdown']:>7,.0f} {r['max_drawdown_pct']:>6.1f}% ${r['final_equity']:>8,.0f}")
    else:
        print("  No safe combinations found with DD < 30%!")
        # Show least risky
        least_risky = sorted(all_results, key=lambda x: x['max_drawdown_pct'])[:10]
        print("\n  Least risky combos:")
        for r in least_risky:
            print(f"    {r['symbol']} ${r['hard_stop']} stop {r['tp_rr']}x TP: DD={r['max_drawdown_pct']:.1f}% PnL=${r['total_pnl']:,.0f}")

    print(f"\n{'='*90}")
    print("DONE")
    print(f"{'='*90}")
