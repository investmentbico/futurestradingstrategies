#!/usr/bin/env python3
"""
MT5 Multi-Instrument Backtester — XAUUSD, BTCUSD, US100/NAS100
================================================================
Mirrors the exact strategy from traderspost_live.py:
  EMA 21/55 + Stoch 25/75 + ATR 9 + Multi-phase exit

Account: 500K | Max DD: 1.5%/day ($7,500) | Target: $10,100/day | 5 days to pass

Usage:
  python mt5_backtest.py                          # Run all instruments, all timeframes
  python mt5_backtest.py --symbol XAUUSD --tf 1   # XAUUSD 1-min only
  python mt5_backtest.py --symbol BTCUSD --tf 5   # BTCUSD 5-min only
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# ── Strategy Parameters (same as traderspost_live.py) ────────────────
STRATEGY = {
    "EMA_FAST": 21, "EMA_SLOW": 55,
    "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75,
    "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
}

# ── Prop Firm Rules ──────────────────────────────────────────────────
ACCOUNT_BALANCE = 500_000
MAX_DAILY_DD_PCT = 1.5
MAX_DAILY_LOSS = ACCOUNT_BALANCE * MAX_DAILY_DD_PCT / 100  # $7,500
DAILY_PROFIT_TARGET = 10_100

# ── Instrument Configs ───────────────────────────────────────────────
# MT5 lot sizing: profit = lots * lot_size * price_move
# For XAUUSD: 1 lot = 100 oz, so 1 lot * $1 move = $100
# For BTCUSD: 1 lot = 1 BTC, so 1 lot * $1 move = $1
# For US100/NAS100: 1 lot = $1/point typically (broker dependent)
INSTRUMENTS = {
    'xauusd': {
        'name': 'XAUUSD (Gold)',
        'point_value_per_lot': 100.0,  # $100 per $1 move per lot
        'tick_size': 0.01,
        'spread': 0.30,      # typical spread in price units
        'commission': 7.0,    # per lot round trip
        'default_lots': 5.0,  # lots per trade
        'max_lots': 20.0,
        'slippage': 0.10,
    },
    'btcusd': {
        'name': 'BTCUSD (Bitcoin)',
        'point_value_per_lot': 1.0,   # $1 per $1 move per lot
        'tick_size': 0.01,
        'spread': 50.0,
        'commission': 10.0,
        'default_lots': 2.0,
        'max_lots': 10.0,
        'slippage': 25.0,
    },
    'nq_60d': {
        'name': 'US100/NAS100 (NASDAQ)',
        'point_value_per_lot': 20.0,  # like MNQ: $20/point per lot
        'tick_size': 0.25,
        'spread': 1.0,
        'commission': 4.0,
        'default_lots': 10.0,
        'max_lots': 50.0,
        'slippage': 0.50,
    },
    'es_60d': {
        'name': 'US500/SP500',
        'point_value_per_lot': 50.0,  # like ES: $50/point per lot
        'tick_size': 0.25,
        'spread': 0.50,
        'commission': 4.0,
        'default_lots': 5.0,
        'max_lots': 30.0,
        'slippage': 0.25,
    },
}


# ── Indicators (exact copy from traderspost_live.py) ─────────────────
def calc_ema(prices, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high, low, close, period):
    tr = np.maximum(high - low, np.maximum(
        np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out

def calc_stoch(high, low, close, k_period, d_period, smooth):
    n = len(close)
    if n < k_period:
        return np.full(n, 50.0), np.full(n, 50.0)
    lowest = np.array([np.min(low[i - k_period + 1:i + 1]) for i in range(k_period - 1, n)])
    highest = np.array([np.max(high[i - k_period + 1:i + 1]) for i in range(k_period - 1, n)])
    denom = highest - lowest
    denom[denom == 0] = 1
    k = 100 * (close[k_period - 1:] - lowest) / denom
    k = np.concatenate([np.full(k_period - 1, np.nan), k])
    valid = k[~np.isnan(k)]
    d = calc_ema(valid, d_period) if len(valid) > 0 else np.array([])
    d_full = np.full(n, np.nan)
    mask_k = ~np.isnan(k)
    if len(d) == np.sum(mask_k):
        d_full[mask_k] = d
    valid_d = d_full[~np.isnan(d_full)]
    d_smooth = calc_ema(valid_d, smooth) if len(valid_d) > 0 else np.array([])
    d_smooth_full = np.full(n, np.nan)
    mask_d = ~np.isnan(d_full)
    if len(d_smooth) == np.sum(mask_d):
        d_smooth_full[mask_d] = d_smooth
    return k, d_smooth_full


def compute_indicators(df):
    """Compute all indicators on dataframe."""
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)

    ema_f = calc_ema(c, STRATEGY["EMA_FAST"])
    ema_s = calc_ema(c, STRATEGY["EMA_SLOW"])
    atr = calc_atr(h, l, c, STRATEGY["ATR_LEN"])
    _, stoch_d = calc_stoch(h, l, c, STRATEGY["STOCH_K"],
                            STRATEGY["STOCH_D"], STRATEGY["STOCH_SMT"])

    df['ema_fast'] = ema_f
    df['ema_slow'] = ema_s
    df['atr'] = atr
    df['stoch_d'] = stoch_d
    df['uptrend'] = ema_f > ema_s
    df['downtrend'] = ema_f < ema_s

    # Previous stoch_d for rising/falling
    df['prev_stoch_d'] = df['stoch_d'].shift(1)
    df['d_rising'] = df['stoch_d'] > df['prev_stoch_d']
    df['d_falling'] = df['stoch_d'] < df['prev_stoch_d']

    return df


# ── Backtest Engine ──────────────────────────────────────────────────
def run_backtest(df, instrument_key, lots=None, verbose=True):
    """
    Bar-by-bar backtest mirroring traderspost_live.py strategy exactly.
    Returns dict with results.
    """
    cfg = INSTRUMENTS[instrument_key]
    pv = cfg['point_value_per_lot']
    spread = cfg['spread']
    commission = cfg['commission']
    slippage = cfg['slippage']
    if lots is None:
        lots = cfg['default_lots']

    # Compute indicators
    df = compute_indicators(df.copy())

    # State
    equity = ACCOUNT_BALANCE
    daily_start = equity
    position = 0       # +1=long, -1=short, 0=flat
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    trailing_stop = 0.0
    peak_pnl = 0.0
    hard_stop_dollars = MAX_DAILY_LOSS * 0.5  # per-trade hard stop = 50% of daily limit

    # Tracking
    trades = []
    equity_curve = []
    daily_pnl = 0.0
    daily_trades = 0
    emergency_stop = False
    prev_date = None
    prev_uptrend = None
    last_exit_pnl = 0
    last_exit_bar = -999
    daily_results = []

    stoch_lo = STRATEGY["STOCH_LO"]
    stoch_hi = STRATEGY["STOCH_HI"]

    for i in range(len(df)):
        row = df.iloc[i]
        price = row['close']
        atr_val = row['atr']
        stoch = row['stoch_d']

        # Skip if indicators not ready
        if np.isnan(atr_val) or np.isnan(stoch):
            equity_curve.append(equity)
            continue

        # Daily reset
        current_date = pd.Timestamp(row['time'], unit='s').date() if 'time' in df.columns else None
        if current_date and current_date != prev_date:
            if prev_date is not None:
                daily_results.append({
                    'date': prev_date,
                    'pnl': daily_pnl,
                    'trades': daily_trades,
                    'start_equity': daily_start,
                    'end_equity': equity,
                })
            daily_start = equity
            daily_pnl = 0.0
            daily_trades = 0
            emergency_stop = False
            prev_date = current_date

        # ── Check Exits ──────────────────────────────────────────
        if position != 0:
            c_lots = lots
            if position > 0:
                pnl = (price - entry_price) * c_lots * pv
            else:
                pnl = (entry_price - price) * c_lots * pv

            # Update peak
            if pnl > peak_pnl:
                peak_pnl = pnl

            r_unit = hard_stop_dollars
            exit_trade = False
            exit_reason = ""

            # Hard stop
            if pnl <= -hard_stop_dollars:
                exit_trade = True
                exit_reason = "HARD_STOP"

            # Smart trailing (mirror live bot phases)
            if not exit_trade and position > 0:  # LONG
                if peak_pnl >= r_unit * 3:
                    floor_price = entry_price + (peak_pnl * 0.60) / (c_lots * pv)
                    if floor_price > trailing_stop:
                        trailing_stop = floor_price
                elif peak_pnl >= r_unit * 2:
                    floor_price = entry_price + (peak_pnl * 0.40) / (c_lots * pv)
                    if floor_price > trailing_stop:
                        trailing_stop = floor_price
                elif peak_pnl >= r_unit:
                    be_price = entry_price + cfg['tick_size']
                    if be_price > trailing_stop:
                        trailing_stop = be_price

                # ATR trail
                atr_trail = price - atr_val * 1.5
                if atr_trail > trailing_stop:
                    trailing_stop = atr_trail

                if trailing_stop > 0 and price <= trailing_stop:
                    exit_trade = True
                    exit_reason = "TRAIL"
                elif price <= stop_loss:
                    exit_trade = True
                    exit_reason = "ATR_STOP"

            elif not exit_trade and position < 0:  # SHORT
                if peak_pnl >= r_unit * 3:
                    floor_price = entry_price - (peak_pnl * 0.60) / (c_lots * pv)
                    if trailing_stop == 0 or floor_price < trailing_stop:
                        trailing_stop = floor_price
                elif peak_pnl >= r_unit * 2:
                    floor_price = entry_price - (peak_pnl * 0.40) / (c_lots * pv)
                    if trailing_stop == 0 or floor_price < trailing_stop:
                        trailing_stop = floor_price
                elif peak_pnl >= r_unit:
                    be_price = entry_price - cfg['tick_size']
                    if trailing_stop == 0 or be_price < trailing_stop:
                        trailing_stop = be_price

                atr_trail = price + atr_val * 1.5
                if trailing_stop == 0 or atr_trail < trailing_stop:
                    trailing_stop = atr_trail

                if trailing_stop > 0 and price >= trailing_stop:
                    exit_trade = True
                    exit_reason = "TRAIL"
                elif price >= stop_loss:
                    exit_trade = True
                    exit_reason = "ATR_STOP"

            if exit_trade:
                # Apply slippage on exit
                if position > 0:
                    exit_price = price - slippage
                    final_pnl = (exit_price - entry_price) * c_lots * pv
                else:
                    exit_price = price + slippage
                    final_pnl = (entry_price - exit_price) * c_lots * pv

                final_pnl -= commission  # round-trip commission

                equity += final_pnl
                daily_pnl += final_pnl
                last_exit_pnl = final_pnl
                last_exit_bar = i

                trades.append({
                    'bar': i,
                    'dir': 'LONG' if position > 0 else 'SHORT',
                    'entry': entry_price,
                    'exit': exit_price,
                    'pnl': final_pnl,
                    'reason': exit_reason,
                    'peak_pnl': peak_pnl,
                    'lots': c_lots,
                    'date': current_date,
                })

                position = 0
                entry_price = 0.0
                stop_loss = 0.0
                take_profit = 0.0
                trailing_stop = 0.0
                peak_pnl = 0.0

        # ── Check Entries ─────────────────────────────────────────
        if position == 0 and not emergency_stop:
            # Daily loss limit check
            if daily_pnl <= -MAX_DAILY_LOSS:
                emergency_stop = True

            # Daily profit target — stop if hit
            if daily_pnl >= DAILY_PROFIT_TARGET:
                emergency_stop = True

            # Cooldown after loss
            if last_exit_pnl < 0 and (i - last_exit_bar) < 5:
                equity_curve.append(equity)
                continue

            uptrend = row['uptrend']
            downtrend = row['downtrend']
            d_rising = row['d_rising']
            d_falling = row['d_falling']
            atr_ok = atr_val > 0

            signal = None

            # PRIMARY: Mean reversion
            if uptrend and atr_ok and stoch <= stoch_lo + 10 and d_rising:
                signal = 'long'
            elif downtrend and atr_ok and stoch >= stoch_hi - 10 and d_falling:
                signal = 'short'

            # SECONDARY: Momentum
            if signal is None and atr_ok:
                if uptrend and stoch <= stoch_lo + 20 and d_rising:
                    signal = 'long'
                elif downtrend and stoch >= stoch_hi - 20 and d_falling:
                    signal = 'short'

            # TERTIARY: EMA crossover
            if signal is None and prev_uptrend is not None and atr_ok:
                if uptrend and not prev_uptrend and stoch <= stoch_hi:
                    signal = 'long'
                elif downtrend and prev_uptrend and stoch >= stoch_lo:
                    signal = 'short'

            # COUNTER-TREND: Deep extremes
            if signal is None and atr_ok:
                if stoch <= 15 and d_rising:
                    signal = 'long'
                elif stoch >= 85 and d_falling:
                    signal = 'short'

            # Quick re-entry after win
            if signal is None and last_exit_pnl > 0 and (i - last_exit_bar) < 30:
                if uptrend and stoch <= 55 and atr_ok:
                    signal = 'long'
                elif downtrend and stoch >= 45 and atr_ok:
                    signal = 'short'

            prev_uptrend = uptrend

            if signal and not emergency_stop:
                mult = STRATEGY["SL_ATR_MULT"]
                rr = STRATEGY["TP_RR"]
                atr_sl = atr_val * mult
                atr_tp = atr_val * mult * rr

                # Cap SL at hard stop distance
                hard_stop_pts = hard_stop_dollars / (lots * pv)
                sl_amount = min(atr_sl, hard_stop_pts)
                tp_amount = atr_tp

                if signal == 'long':
                    entry_price = price + spread / 2 + slippage  # slippage on entry
                    stop_loss = entry_price - sl_amount
                    take_profit = entry_price + tp_amount
                    position = 1
                else:
                    entry_price = price - spread / 2 - slippage
                    stop_loss = entry_price + sl_amount
                    take_profit = entry_price - tp_amount
                    position = -1

                trailing_stop = 0.0
                peak_pnl = 0.0
                daily_trades += 1

        equity_curve.append(equity)

    # Final daily result
    if prev_date:
        daily_results.append({
            'date': prev_date,
            'pnl': daily_pnl,
            'trades': daily_trades,
            'start_equity': daily_start,
            'end_equity': equity,
        })

    # ── Results Analysis ─────────────────────────────────────────
    if not trades:
        return {'total_pnl': 0, 'trades': 0, 'error': 'No trades generated'}

    trade_pnls = [t['pnl'] for t in trades]
    wins = [p for p in trade_pnls if p > 0]
    losses = [p for p in trade_pnls if p <= 0]

    total_pnl = sum(trade_pnls)
    win_rate = len(wins) / len(trade_pnls) * 100 if trade_pnls else 0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float('inf')

    # Max drawdown
    peak_eq = ACCOUNT_BALANCE
    max_dd = 0
    for eq in equity_curve:
        if eq > peak_eq:
            peak_eq = eq
        dd = peak_eq - eq
        if dd > max_dd:
            max_dd = dd

    # Daily analysis
    daily_pnls = [d['pnl'] for d in daily_results]
    days_profitable = sum(1 for p in daily_pnls if p > 0)
    days_losing = sum(1 for p in daily_pnls if p <= 0)
    days_hit_target = sum(1 for p in daily_pnls if p >= DAILY_PROFIT_TARGET)
    max_daily_loss_actual = min(daily_pnls) if daily_pnls else 0
    max_daily_profit = max(daily_pnls) if daily_pnls else 0
    avg_daily_pnl = np.mean(daily_pnls) if daily_pnls else 0

    # Days to pass challenge (need $50,500 total)
    cumulative = 0
    days_to_pass = None
    for i, d in enumerate(daily_results):
        cumulative += d['pnl']
        if cumulative >= DAILY_PROFIT_TARGET * 5:
            days_to_pass = i + 1
            break

    results = {
        'instrument': cfg['name'],
        'lots': lots,
        'total_trades': len(trades),
        'total_pnl': total_pnl,
        'win_rate': win_rate,
        'wins': len(wins),
        'losses': len(losses),
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'max_drawdown': max_dd,
        'max_dd_pct': max_dd / ACCOUNT_BALANCE * 100,
        'final_equity': equity,
        'return_pct': (equity - ACCOUNT_BALANCE) / ACCOUNT_BALANCE * 100,
        'total_days': len(daily_results),
        'days_profitable': days_profitable,
        'days_losing': days_losing,
        'days_hit_target': days_hit_target,
        'max_daily_loss': max_daily_loss_actual,
        'max_daily_profit': max_daily_profit,
        'avg_daily_pnl': avg_daily_pnl,
        'days_to_pass': days_to_pass,
        'trades': trades,
        'daily_results': daily_results,
        'equity_curve': equity_curve,
    }

    if verbose:
        print_results(results)

    return results


def print_results(r):
    """Pretty print backtest results."""
    print("\n" + "=" * 70)
    print(f"  BACKTEST RESULTS: {r['instrument']}")
    print(f"  Lots: {r['lots']} | Account: ${ACCOUNT_BALANCE:,.0f}")
    print("=" * 70)
    print(f"  Total Trades:     {r['total_trades']}")
    print(f"  Win Rate:         {r['win_rate']:.1f}% ({r['wins']}W / {r['losses']}L)")
    print(f"  Total P&L:        ${r['total_pnl']:,.2f}")
    print(f"  Return:           {r['return_pct']:.2f}%")
    print(f"  Profit Factor:    {r['profit_factor']:.2f}")
    print(f"  Avg Win:          ${r['avg_win']:,.2f}")
    print(f"  Avg Loss:         ${r['avg_loss']:,.2f}")
    print(f"  Max Drawdown:     ${r['max_drawdown']:,.2f} ({r['max_dd_pct']:.2f}%)")
    print("-" * 70)
    print(f"  DAILY ANALYSIS ({r['total_days']} trading days):")
    print(f"  Days Profitable:  {r['days_profitable']}/{r['total_days']}")
    print(f"  Days Hit Target:  {r['days_hit_target']}/{r['total_days']} (${DAILY_PROFIT_TARGET:,}/day)")
    print(f"  Avg Daily P&L:    ${r['avg_daily_pnl']:,.2f}")
    print(f"  Max Daily Profit: ${r['max_daily_profit']:,.2f}")
    print(f"  Max Daily Loss:   ${r['max_daily_loss']:,.2f}")
    print("-" * 70)

    # Pass/fail assessment
    passes = r['max_drawdown'] <= MAX_DAILY_LOSS * 3
    daily_ok = abs(r['max_daily_loss']) <= MAX_DAILY_LOSS
    target_ok = r['avg_daily_pnl'] >= DAILY_PROFIT_TARGET * 0.5

    print(f"  PROP FIRM CHALLENGE ASSESSMENT:")
    print(f"  Max DD < ${MAX_DAILY_LOSS * 3:,}:     {'PASS' if passes else 'FAIL'} (${r['max_drawdown']:,.0f})")
    print(f"  Daily Loss < ${MAX_DAILY_LOSS:,}:  {'PASS' if daily_ok else 'FAIL'} (${abs(r['max_daily_loss']):,.0f})")
    print(f"  Avg Daily >= ${DAILY_PROFIT_TARGET * 0.5:,.0f}:  {'PASS' if target_ok else 'NEEDS WORK'} (${r['avg_daily_pnl']:,.0f})")
    if r['days_to_pass']:
        print(f"  Days to Pass:     {r['days_to_pass']} days")
    else:
        print(f"  Days to Pass:     NOT REACHED in {r['total_days']} days")
    print("=" * 70)

    # Show daily breakdown
    print("\n  DAILY BREAKDOWN:")
    print(f"  {'Date':12s} | {'P&L':>12s} | {'Trades':>6s} | {'Cumulative':>12s} | Status")
    print("  " + "-" * 65)
    cumulative = 0
    for d in r['daily_results']:
        cumulative += d['pnl']
        status = "TARGET" if d['pnl'] >= DAILY_PROFIT_TARGET else ("LOSS" if d['pnl'] < 0 else "ok")
        dd_warning = " DD!" if d['pnl'] < -MAX_DAILY_LOSS else ""
        print(f"  {str(d['date']):12s} | ${d['pnl']:>10,.2f} | {d['trades']:>6d} | ${cumulative:>10,.2f} | {status}{dd_warning}")


def optimize_lots(df, instrument_key, lot_range=None):
    """Find optimal lot size for the daily target."""
    cfg = INSTRUMENTS[instrument_key]
    if lot_range is None:
        lot_range = np.arange(1, cfg['max_lots'] + 1, 1)

    print(f"\n{'=' * 70}")
    print(f"  LOT SIZE OPTIMIZATION: {cfg['name']}")
    print(f"{'=' * 70}")
    print(f"  {'Lots':>6s} | {'Total P&L':>12s} | {'Win%':>6s} | {'Avg Daily':>12s} | {'Max DD':>12s} | {'DD%':>6s} | Pass?")
    print("  " + "-" * 80)

    best = None
    for lot in lot_range:
        r = run_backtest(df, instrument_key, lots=lot, verbose=False)
        if 'error' in r:
            continue

        passes = (r['max_drawdown'] <= MAX_DAILY_LOSS * 3 and
                  abs(r['max_daily_loss']) <= MAX_DAILY_LOSS and
                  r['avg_daily_pnl'] > 0)
        status = "YES" if passes else "no"

        print(f"  {lot:>6.1f} | ${r['total_pnl']:>10,.0f} | {r['win_rate']:>5.1f}% | "
              f"${r['avg_daily_pnl']:>10,.0f} | ${r['max_drawdown']:>10,.0f} | "
              f"{r['max_dd_pct']:>5.2f}% | {status}")

        if passes and (best is None or r['avg_daily_pnl'] > best['avg_daily_pnl']):
            best = r
            best['lots_used'] = lot

    if best:
        print(f"\n  OPTIMAL: {best['lots_used']:.1f} lots | Avg Daily: ${best['avg_daily_pnl']:,.0f} | DD: ${best['max_drawdown']:,.0f}")
    else:
        print("\n  No safe lot size found — strategy needs tuning for this instrument")

    return best


def main():
    parser = argparse.ArgumentParser(description='MT5 Multi-Instrument Backtester')
    parser.add_argument('--symbol', type=str, help='Symbol: xauusd, btcusd, nq_60d, es_60d')
    parser.add_argument('--tf', type=int, help='Timeframe in minutes: 1, 2, 3, 5')
    parser.add_argument('--lots', type=float, help='Override lot size')
    parser.add_argument('--optimize', action='store_true', help='Optimize lot sizes')
    parser.add_argument('--all', action='store_true', help='Run all instruments and timeframes')
    args = parser.parse_args()

    timeframes = [1, 2, 3, 5]
    symbols = list(INSTRUMENTS.keys())

    if args.symbol:
        symbols = [args.symbol.lower()]
    if args.tf:
        timeframes = [args.tf]
    if args.all or (not args.symbol and not args.tf):
        pass  # run all

    all_results = []

    for sym in symbols:
        for tf in timeframes:
            tf_str = f"{tf}min"
            csv_path = f"data/{sym}_{tf_str}.csv"

            if not os.path.exists(csv_path):
                print(f"\nSkipping {sym} {tf_str} — no data file: {csv_path}")
                continue

            df = pd.read_csv(csv_path)
            if len(df) < 100:
                print(f"\nSkipping {sym} {tf_str} — only {len(df)} bars (need 100+)")
                continue

            print(f"\n{'#' * 70}")
            print(f"  {INSTRUMENTS[sym]['name']} — {tf_str} — {len(df)} bars")
            print(f"{'#' * 70}")

            if args.optimize:
                result = optimize_lots(df, sym)
            else:
                lots = args.lots if args.lots else INSTRUMENTS[sym]['default_lots']
                result = run_backtest(df, sym, lots=lots)

            if result and 'error' not in result:
                result['timeframe'] = tf_str
                result['symbol'] = sym
                all_results.append(result)

    # Summary table
    if len(all_results) > 1:
        print("\n" + "=" * 90)
        print("  SUMMARY — ALL INSTRUMENTS & TIMEFRAMES")
        print("=" * 90)
        print(f"  {'Instrument':25s} | {'TF':>5s} | {'Trades':>6s} | {'P&L':>12s} | {'Win%':>6s} | "
              f"{'Avg Daily':>10s} | {'Max DD':>10s} | Pass")
        print("  " + "-" * 85)

        for r in sorted(all_results, key=lambda x: x['avg_daily_pnl'], reverse=True):
            passes = (r['max_drawdown'] <= MAX_DAILY_LOSS * 3 and
                      abs(r['max_daily_loss']) <= MAX_DAILY_LOSS)
            status = "YES" if passes else "no"
            print(f"  {r['instrument']:25s} | {r['timeframe']:>5s} | {r['total_trades']:>6d} | "
                  f"${r['total_pnl']:>10,.0f} | {r['win_rate']:>5.1f}% | "
                  f"${r['avg_daily_pnl']:>8,.0f} | ${r['max_drawdown']:>8,.0f} | {status}")

        # Best pick
        passing = [r for r in all_results
                   if r['max_drawdown'] <= MAX_DAILY_LOSS * 3
                   and abs(r['max_daily_loss']) <= MAX_DAILY_LOSS
                   and r['avg_daily_pnl'] > 0]
        if passing:
            best = max(passing, key=lambda x: x['avg_daily_pnl'])
            print(f"\n  RECOMMENDED: {best['instrument']} {best['timeframe']} — "
                  f"${best['avg_daily_pnl']:,.0f}/day avg, {best['win_rate']:.0f}% WR")
        else:
            print("\n  No instrument/timeframe passes all prop firm rules. Strategy needs tuning.")


if __name__ == "__main__":
    main()
