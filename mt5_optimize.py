#!/usr/bin/env python3
"""
MT5 Strategy Optimizer — Fix strategy for prop firm challenge
=============================================================
Problem: Win rate too low, drawdowns too high.
Solution: Tighter filters, adaptive lot sizing, strict daily limits.

Focus on top 2 performers:
  1. US100/NAS100 2min (best: +$113K, 40.9% WR)
  2. XAUUSD 2min (best: +$78K, 29.2% WR)
"""

import os
import sys
import pandas as pd
import numpy as np
from itertools import product

# ── Prop Firm Rules ──────────────────────────────────────────────────
ACCOUNT_BALANCE = 500_000
MAX_DAILY_DD_PCT = 1.5
MAX_DAILY_LOSS = ACCOUNT_BALANCE * MAX_DAILY_DD_PCT / 100  # $7,500
DAILY_PROFIT_TARGET = 10_100

# ── Instrument Configs ───────────────────────────────────────────────
INSTRUMENTS = {
    'xauusd': {
        'name': 'XAUUSD (Gold)',
        'point_value_per_lot': 100.0,
        'tick_size': 0.01,
        'spread': 0.30,
        'commission': 7.0,
        'slippage': 0.10,
    },
    'btcusd': {
        'name': 'BTCUSD (Bitcoin)',
        'point_value_per_lot': 1.0,
        'tick_size': 0.01,
        'spread': 50.0,
        'commission': 10.0,
        'slippage': 25.0,
    },
    'nq_60d': {
        'name': 'US100/NAS100 (NASDAQ)',
        'point_value_per_lot': 20.0,
        'tick_size': 0.25,
        'spread': 1.0,
        'commission': 4.0,
        'slippage': 0.50,
    },
    'es_60d': {
        'name': 'US500/SP500',
        'point_value_per_lot': 50.0,
        'tick_size': 0.25,
        'spread': 0.50,
        'commission': 4.0,
        'slippage': 0.25,
    },
}


# ── Indicators ───────────────────────────────────────────────────────
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

def calc_rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0).astype(float)
    loss = np.where(delta < 0, -delta, 0).astype(float)
    avg_gain = np.empty_like(close)
    avg_loss = np.empty_like(close)
    avg_gain[:period] = np.nan
    avg_loss[:period] = np.nan
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100)
    rsi = 100 - (100 / (1 + rs))
    rsi[:period] = np.nan
    return rsi


def compute_indicators(df, params):
    """Compute all indicators with given parameters."""
    c = df['close'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)

    df = df.copy()
    df['ema_fast'] = calc_ema(c, params['ema_fast'])
    df['ema_slow'] = calc_ema(c, params['ema_slow'])
    df['atr'] = calc_atr(h, l, c, params['atr_len'])
    _, df['stoch_d'] = calc_stoch(h, l, c, params['stoch_k'],
                                   params['stoch_d_period'], params['stoch_smooth'])
    df['rsi'] = calc_rsi(c, 14)
    df['uptrend'] = df['ema_fast'] > df['ema_slow']
    df['downtrend'] = df['ema_fast'] < df['ema_slow']
    df['prev_stoch_d'] = df['stoch_d'].shift(1)
    df['d_rising'] = df['stoch_d'] > df['prev_stoch_d']
    df['d_falling'] = df['stoch_d'] < df['prev_stoch_d']

    # EMA gap strength
    df['ema_gap'] = abs(df['ema_fast'] - df['ema_slow'])
    df['ema_gap_atr'] = df['ema_gap'] / df['atr']  # gap relative to ATR

    return df


# ── IMPROVED Backtest Engine ─────────────────────────────────────────
def run_optimized_backtest(df, instrument_key, params, lots=None, verbose=False):
    """
    Improved backtest with:
    - RSI confirmation filter (reduces bad entries)
    - Minimum ATR filter (skip low-vol bars)
    - Stricter Stoch thresholds (only true extremes)
    - Dynamic lot sizing based on ATR
    - Hard daily loss cap (absolute)
    - Remove counter-trend and quick re-entry (too risky)
    - Only PRIMARY signals (no secondary/tertiary)
    """
    cfg = INSTRUMENTS[instrument_key]
    pv = cfg['point_value_per_lot']
    spread = cfg['spread']
    commission = cfg['commission']
    slippage_val = cfg['slippage']
    if lots is None:
        lots = params.get('lots', 5.0)

    df = compute_indicators(df.copy(), params)

    stoch_lo = params['stoch_lo']
    stoch_hi = params['stoch_hi']
    sl_mult = params['sl_mult']
    tp_rr = params['tp_rr']
    min_atr_mult = params.get('min_atr_mult', 0.5)
    rsi_filter = params.get('rsi_filter', True)
    max_trades_per_day = params.get('max_trades_day', 6)
    use_secondary = params.get('use_secondary', False)
    use_counter = params.get('use_counter', False)

    # State
    equity = ACCOUNT_BALANCE
    daily_start = equity
    position = 0
    entry_price = 0.0
    stop_loss = 0.0
    trailing_stop = 0.0
    peak_pnl = 0.0
    hard_stop_dollars = params.get('hard_stop', MAX_DAILY_LOSS * 0.4)

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
    consecutive_losses = 0

    for i in range(len(df)):
        row = df.iloc[i]
        price = row['close']
        atr_val = row['atr']
        stoch = row['stoch_d']
        rsi = row.get('rsi', 50)

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
                })
            daily_start = equity
            daily_pnl = 0.0
            daily_trades = 0
            emergency_stop = False
            consecutive_losses = 0
            prev_date = current_date

        # ── Check Exits ──────────────────────────────────────
        if position != 0:
            c_lots = lots
            if position > 0:
                pnl = (price - entry_price) * c_lots * pv
            else:
                pnl = (entry_price - price) * c_lots * pv

            if pnl > peak_pnl:
                peak_pnl = pnl

            r_unit = hard_stop_dollars
            exit_trade = False
            exit_reason = ""

            # Hard stop
            if pnl <= -hard_stop_dollars:
                exit_trade = True
                exit_reason = "HARD_STOP"

            # Smart trailing
            if not exit_trade:
                if position > 0:
                    if peak_pnl >= r_unit * 3:
                        floor_price = entry_price + (peak_pnl * 0.65) / (c_lots * pv)
                        trailing_stop = max(trailing_stop, floor_price)
                    elif peak_pnl >= r_unit * 2:
                        floor_price = entry_price + (peak_pnl * 0.45) / (c_lots * pv)
                        trailing_stop = max(trailing_stop, floor_price)
                    elif peak_pnl >= r_unit:
                        be_price = entry_price + cfg['tick_size']
                        trailing_stop = max(trailing_stop, be_price)

                    atr_trail = price - atr_val * 1.5
                    trailing_stop = max(trailing_stop, atr_trail)

                    if trailing_stop > 0 and price <= trailing_stop:
                        exit_trade = True
                        exit_reason = "TRAIL"
                    elif price <= stop_loss:
                        exit_trade = True
                        exit_reason = "ATR_STOP"
                else:
                    if peak_pnl >= r_unit * 3:
                        floor_price = entry_price - (peak_pnl * 0.65) / (c_lots * pv)
                        if trailing_stop == 0 or floor_price < trailing_stop:
                            trailing_stop = floor_price
                    elif peak_pnl >= r_unit * 2:
                        floor_price = entry_price - (peak_pnl * 0.45) / (c_lots * pv)
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
                if position > 0:
                    exit_price = price - slippage_val
                    final_pnl = (exit_price - entry_price) * c_lots * pv
                else:
                    exit_price = price + slippage_val
                    final_pnl = (entry_price - exit_price) * c_lots * pv

                final_pnl -= commission

                equity += final_pnl
                daily_pnl += final_pnl
                last_exit_pnl = final_pnl
                last_exit_bar = i

                if final_pnl <= 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0

                trades.append({
                    'bar': i, 'dir': 'LONG' if position > 0 else 'SHORT',
                    'entry': entry_price, 'exit': exit_price,
                    'pnl': final_pnl, 'reason': exit_reason,
                    'date': current_date,
                })

                position = 0
                entry_price = 0.0
                stop_loss = 0.0
                trailing_stop = 0.0
                peak_pnl = 0.0

        # ── Check Entries ────────────────────────────────────
        if position == 0 and not emergency_stop:
            # STRICT daily loss limit
            if daily_pnl <= -MAX_DAILY_LOSS:
                emergency_stop = True
            if daily_pnl >= DAILY_PROFIT_TARGET:
                emergency_stop = True
            if daily_trades >= max_trades_per_day:
                equity_curve.append(equity)
                continue
            # Scale back after consecutive losses
            if consecutive_losses >= 3:
                equity_curve.append(equity)
                continue

            # Cooldown after loss
            if last_exit_pnl < 0 and (i - last_exit_bar) < params.get('cooldown_bars', 10):
                equity_curve.append(equity)
                continue

            uptrend = row['uptrend']
            downtrend = row['downtrend']
            d_rising = row['d_rising']
            d_falling = row['d_falling']
            atr_ok = atr_val > 0

            # Minimum ATR filter (skip dead markets)
            if not np.isnan(row.get('ema_gap_atr', np.nan)):
                if row['ema_gap_atr'] < min_atr_mult:
                    equity_curve.append(equity)
                    continue

            signal = None

            # PRIMARY: Mean reversion from extreme stoch
            if uptrend and atr_ok and stoch <= stoch_lo and d_rising:
                if not rsi_filter or (not np.isnan(rsi) and rsi > 40):
                    signal = 'long'
            elif downtrend and atr_ok and stoch >= stoch_hi and d_falling:
                if not rsi_filter or (not np.isnan(rsi) and rsi < 60):
                    signal = 'short'

            # SECONDARY: Moderate pullback (only if enabled)
            if signal is None and use_secondary and atr_ok:
                if uptrend and stoch <= stoch_lo + 10 and d_rising:
                    if not rsi_filter or (not np.isnan(rsi) and rsi > 50):
                        signal = 'long'
                elif downtrend and stoch >= stoch_hi - 10 and d_falling:
                    if not rsi_filter or (not np.isnan(rsi) and rsi < 50):
                        signal = 'short'

            # EMA crossover
            if signal is None and prev_uptrend is not None and atr_ok:
                if uptrend and not prev_uptrend and stoch <= stoch_hi - 10:
                    signal = 'long'
                elif downtrend and prev_uptrend and stoch >= stoch_lo + 10:
                    signal = 'short'

            # COUNTER-TREND deep extreme (only if enabled)
            if signal is None and use_counter and atr_ok:
                if stoch <= 10 and d_rising:
                    signal = 'long'
                elif stoch >= 90 and d_falling:
                    signal = 'short'

            prev_uptrend = uptrend

            if signal and not emergency_stop:
                atr_sl = atr_val * sl_mult
                atr_tp = atr_val * sl_mult * tp_rr
                hard_stop_pts = hard_stop_dollars / (lots * pv)
                sl_amount = min(atr_sl, hard_stop_pts)
                tp_amount = atr_tp

                if signal == 'long':
                    entry_price = price + spread / 2 + slippage_val
                    stop_loss = entry_price - sl_amount
                    position = 1
                else:
                    entry_price = price - spread / 2 - slippage_val
                    stop_loss = entry_price + sl_amount
                    position = -1

                trailing_stop = 0.0
                peak_pnl = 0.0
                daily_trades += 1

        equity_curve.append(equity)

    # Final daily
    if prev_date:
        daily_results.append({
            'date': prev_date,
            'pnl': daily_pnl,
            'trades': daily_trades,
        })

    # Results
    if not trades:
        return None

    trade_pnls = [t['pnl'] for t in trades]
    wins = [p for p in trade_pnls if p > 0]
    losses_list = [p for p in trade_pnls if p <= 0]
    total_pnl = sum(trade_pnls)
    win_rate = len(wins) / len(trade_pnls) * 100
    profit_factor = abs(sum(wins) / sum(losses_list)) if losses_list and sum(losses_list) != 0 else 999

    peak_eq = ACCOUNT_BALANCE
    max_dd = 0
    for eq in equity_curve:
        if eq > peak_eq:
            peak_eq = eq
        dd = peak_eq - eq
        if dd > max_dd:
            max_dd = dd

    daily_pnls = [d['pnl'] for d in daily_results]
    max_daily_loss_actual = min(daily_pnls) if daily_pnls else 0
    avg_daily = np.mean(daily_pnls) if daily_pnls else 0
    days_hit = sum(1 for p in daily_pnls if p >= DAILY_PROFIT_TARGET)
    days_dd = sum(1 for p in daily_pnls if p < -MAX_DAILY_LOSS)

    return {
        'total_pnl': total_pnl,
        'trades': len(trades),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'max_dd': max_dd,
        'max_dd_pct': max_dd / ACCOUNT_BALANCE * 100,
        'avg_daily': avg_daily,
        'max_daily_loss': max_daily_loss_actual,
        'days_hit_target': days_hit,
        'days_dd_breach': days_dd,
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses_list) if losses_list else 0,
        'daily_results': daily_results,
        'total_days': len(daily_results),
        'params': params,
        'lots': lots,
    }


def grid_search(df, instrument_key):
    """Grid search over key parameters."""
    print(f"\nOptimizing {INSTRUMENTS[instrument_key]['name']}...")
    print("=" * 100)

    param_grid = {
        'ema_fast': [13, 21, 34],
        'ema_slow': [55, 89],
        'stoch_lo': [20, 25, 30],
        'stoch_hi': [70, 75, 80],
        'sl_mult': [1.5, 2.0, 2.5],
        'tp_rr': [1.5, 1.8, 2.0, 2.5],
        'lots': [2, 3, 5, 8, 10],
        'hard_stop': [2000, 3000, 3750],
    }

    # Fixed params
    base_params = {
        'stoch_k': 9, 'stoch_d_period': 3, 'stoch_smooth': 2,
        'atr_len': 9,
        'rsi_filter': True,
        'min_atr_mult': 0.3,
        'max_trades_day': 6,
        'cooldown_bars': 10,
        'use_secondary': True,
        'use_counter': False,
    }

    # Generate all combinations
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    total = 1
    for v in values:
        total *= len(v)
    print(f"Testing {total} parameter combinations...\n")

    results = []
    tested = 0

    for combo in product(*values):
        params = dict(zip(keys, combo))
        params.update(base_params)
        lots = params.pop('lots')

        r = run_optimized_backtest(df, instrument_key, params, lots=lots)
        tested += 1

        if r is None:
            continue

        # Filter: must pass prop firm rules
        passes = (
            r['max_dd'] <= MAX_DAILY_LOSS * 2.5 and
            abs(r['max_daily_loss']) <= MAX_DAILY_LOSS and
            r['days_dd_breach'] == 0 and
            r['avg_daily'] > 0 and
            r['profit_factor'] > 1.0
        )

        if passes:
            params['lots'] = lots
            r['params_full'] = params
            results.append(r)

        if tested % 500 == 0:
            print(f"  Tested {tested}/{total} | Passing: {len(results)}")

    print(f"\nTested {tested} combinations | Passing: {len(results)}")

    if not results:
        print("No combinations pass all prop firm rules.")
        # Show best failing ones
        print("\nRelaxing criteria...")
        for combo in product(*values):
            params = dict(zip(keys, combo))
            params.update(base_params)
            lots = params.pop('lots')
            r = run_optimized_backtest(df, instrument_key, params, lots=lots)
            if r and r['avg_daily'] > 0 and r['profit_factor'] > 1.0:
                params['lots'] = lots
                r['params_full'] = params
                results.append(r)
        if results:
            results.sort(key=lambda x: x['avg_daily'], reverse=True)
            print(f"\nTop 5 profitable (may have DD issues):")
            for i, r in enumerate(results[:5]):
                print(f"  #{i+1}: Avg/day=${r['avg_daily']:,.0f} | WR={r['win_rate']:.0f}% | "
                      f"PF={r['profit_factor']:.2f} | DD=${r['max_dd']:,.0f} | "
                      f"MaxDayLoss=${abs(r['max_daily_loss']):,.0f} | "
                      f"Params: EMA {r['params_full']['ema_fast']}/{r['params_full']['ema_slow']} "
                      f"Stoch {r['params_full']['stoch_lo']}/{r['params_full']['stoch_hi']} "
                      f"SL {r['params_full']['sl_mult']}x TP {r['params_full']['tp_rr']}R "
                      f"Lots={r['params_full']['lots']} HardStop=${r['params_full']['hard_stop']}")
        return results[:5] if results else []

    # Sort by avg daily P&L
    results.sort(key=lambda x: x['avg_daily'], reverse=True)

    print(f"\n{'=' * 110}")
    print(f"TOP 10 PASSING PARAMETER SETS:")
    print(f"{'=' * 110}")
    print(f"{'#':>3s} | {'Avg/Day':>10s} | {'Total':>12s} | {'WR%':>5s} | {'PF':>5s} | "
          f"{'MaxDD':>10s} | {'DD%':>6s} | {'MaxDayL':>10s} | {'Trades':>6s} | Parameters")
    print("-" * 110)

    for i, r in enumerate(results[:10]):
        p = r['params_full']
        print(f"{i+1:>3d} | ${r['avg_daily']:>8,.0f} | ${r['total_pnl']:>10,.0f} | "
              f"{r['win_rate']:>4.0f}% | {r['profit_factor']:>4.1f} | "
              f"${r['max_dd']:>8,.0f} | {r['max_dd_pct']:>5.2f}% | "
              f"${abs(r['max_daily_loss']):>8,.0f} | {r['trades']:>6d} | "
              f"EMA {p['ema_fast']}/{p['ema_slow']} St {p['stoch_lo']}/{p['stoch_hi']} "
              f"SL {p['sl_mult']}x TP {p['tp_rr']}R L={p['lots']} HS=${p['hard_stop']}")

    # Show daily breakdown for #1
    if results:
        best = results[0]
        print(f"\nBEST RESULT — Daily Breakdown:")
        print(f"  {'Date':12s} | {'P&L':>10s} | {'Trades':>6s} | {'Cum':>12s}")
        print("  " + "-" * 50)
        cum = 0
        for d in best['daily_results']:
            cum += d['pnl']
            print(f"  {str(d['date']):12s} | ${d['pnl']:>8,.0f} | {d['trades']:>6d} | ${cum:>10,.0f}")

    return results[:10]


def main():
    # Focus on top performers
    targets = [
        ('nq_60d', '2min'),    # Best overall
        ('xauusd', '2min'),    # Second best
        ('es_60d', '5min'),    # Third best
        ('nq_60d', '1min'),    # Test 1min NQ too
        ('xauusd', '1min'),
        ('btcusd', '5min'),
    ]

    all_best = []

    for sym, tf in targets:
        csv_path = f"data/{sym}_{tf}.csv"
        if not os.path.exists(csv_path):
            print(f"Skipping {sym} {tf} — no data")
            continue

        df = pd.read_csv(csv_path)
        if len(df) < 100:
            continue

        print(f"\n{'#' * 80}")
        print(f"  {INSTRUMENTS[sym]['name']} — {tf} — {len(df)} bars")
        print(f"{'#' * 80}")

        best = grid_search(df, sym)
        if best:
            for r in best:
                r['symbol'] = sym
                r['timeframe'] = tf
            all_best.extend(best)

    # Final ranking
    if all_best:
        all_best.sort(key=lambda x: x['avg_daily'], reverse=True)
        print(f"\n{'=' * 120}")
        print(f"  FINAL RANKING — ALL INSTRUMENTS")
        print(f"{'=' * 120}")
        for i, r in enumerate(all_best[:20]):
            p = r['params_full']
            inst = INSTRUMENTS[r['symbol']]['name']
            print(f"  #{i+1:>2d} | {inst:25s} {r['timeframe']:>5s} | "
                  f"Avg/day=${r['avg_daily']:>8,.0f} | WR={r['win_rate']:.0f}% | "
                  f"PF={r['profit_factor']:.1f} | DD=${r['max_dd']:,.0f} ({r['max_dd_pct']:.1f}%) | "
                  f"EMA {p['ema_fast']}/{p['ema_slow']} St {p['stoch_lo']}/{p['stoch_hi']} "
                  f"SL {p['sl_mult']}x TP {p['tp_rr']}R L={p['lots']} HS=${p['hard_stop']}")


if __name__ == "__main__":
    main()
