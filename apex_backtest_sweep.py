#!/usr/bin/env python3
"""
Apex Trader Funding 150K - Optimized Strategy Backtester
=========================================================
Sweeps strategy parameters to find the best configuration for Apex prop firm:
- Fewer trades (2-5/day) to avoid HFT appearance
- Higher win rate (40%+)
- Max trailing drawdown < $5,000 (Apex 150K limit)
- 30% consistency rule (no single day > 30% of total profit)
- All trades closed by 4:59 PM ET
- 3 MNQ contracts

Tests across: timeframes, EMA periods, stochastic thresholds, ATR settings,
max trades per day, and stop/target levels.
"""

import sys
import pandas as pd
import numpy as np
from itertools import product
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Point value: using $2/point (real MNQ) for Apex-accurate P&L
MNQ_POINT_VALUE = 2.0
CONTRACTS = 3
APEX_TRAILING_DRAWDOWN = 5000.0

# ── Indicators ────────────────────────────────────────────────────────
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
    lowest = np.array([np.min(low[max(0, i-k_period+1):i+1]) for i in range(k_period-1, n)])
    highest = np.array([np.max(high[max(0, i-k_period+1):i+1]) for i in range(k_period-1, n)])
    denom = highest - lowest
    denom[denom == 0] = 1
    k = 100 * (close[k_period-1:] - lowest) / denom
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    valid = k[~np.isnan(k)]
    d = calc_ema(valid, d_period) if len(valid) > 0 else np.array([])
    d_full = np.full(n, np.nan)
    mask = ~np.isnan(k)
    if len(d) == np.sum(mask):
        d_full[mask] = d
    valid_d = d_full[~np.isnan(d_full)]
    d_smooth = calc_ema(valid_d, smooth) if len(valid_d) > 0 else np.array([])
    d_smooth_full = np.full(n, np.nan)
    mask_d = ~np.isnan(d_full)
    if len(d_smooth) == np.sum(mask_d):
        d_smooth_full[mask_d] = d_smooth
    return k, d_smooth_full


# ── Backtest Engine ───────────────────────────────────────────────────
def run_backtest(df, params):
    """Run a single backtest with given parameters. Returns results dict."""
    ema_fast_p = params['ema_fast']
    ema_slow_p = params['ema_slow']
    stoch_k_p = params['stoch_k']
    stoch_d_p = params['stoch_d']
    stoch_smt = params.get('stoch_smt', 2)
    stoch_lo = params['stoch_lo']
    stoch_hi = params['stoch_hi']
    atr_len = params['atr_len']
    sl_atr_mult = params['sl_atr_mult']
    tp_rr = params['tp_rr']
    hard_stop = params['hard_stop']
    max_trades_day = params['max_trades_day']
    contracts = params.get('contracts', CONTRACTS)

    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)

    # Indicators
    ema_f = calc_ema(closes, ema_fast_p)
    ema_s = calc_ema(closes, ema_slow_p)
    atr = calc_atr(highs, lows, closes, atr_len)
    _, stoch_d = calc_stoch(highs, lows, closes, stoch_k_p, stoch_d_p, stoch_smt)

    # Trading state
    position = 0
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    trailing_stop = 0.0

    trades = []
    total_pnl = 0.0
    cumulative_pnl = 0.0
    peak_pnl = 0.0
    max_drawdown = 0.0
    daily_pnls = {}
    daily_trades_count = 0
    daily_pnl = 0.0
    current_date = None

    warmup = max(ema_slow_p, stoch_k_p + stoch_d_p + stoch_smt) + 10

    for i in range(warmup, len(df)):
        price = closes[i]
        ts = df.iloc[i]['timestamp']
        trade_date = ts.date()
        trade_hour = ts.hour
        trade_minute = ts.minute

        # Daily reset
        if trade_date != current_date:
            if current_date is not None:
                daily_pnls[current_date] = daily_pnl
            daily_pnl = 0.0
            daily_trades_count = 0
            current_date = trade_date

        # Force close by 4:59 PM ET (Apex rule)
        if position != 0 and trade_hour == 16 and trade_minute >= 55:
            if position > 0:
                pnl = (price - entry_price) * contracts * MNQ_POINT_VALUE
            else:
                pnl = (entry_price - price) * contracts * MNQ_POINT_VALUE
            pnl -= 14.78 + contracts * 0.52  # commissions + fees
            trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'eod_close'})
            total_pnl += pnl
            daily_pnl += pnl
            cumulative_pnl += pnl
            if cumulative_pnl > peak_pnl:
                peak_pnl = cumulative_pnl
            dd = peak_pnl - cumulative_pnl
            if dd > max_drawdown:
                max_drawdown = dd
            position = 0
            continue

        # Skip if indicators not ready
        if np.isnan(atr[i]) or np.isnan(stoch_d[i]) or np.isnan(ema_f[i]) or np.isnan(ema_s[i]):
            continue

        prev_d = stoch_d[i-1] if i > 0 and not np.isnan(stoch_d[i-1]) else stoch_d[i]

        # ── Check exits ──
        if position != 0:
            if position > 0:
                pnl_now = (price - entry_price) * contracts * MNQ_POINT_VALUE
                # Hard stop
                if pnl_now <= -hard_stop:
                    pnl = -hard_stop - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'hard_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue
                # ATR stop
                if price <= stop_loss:
                    pnl = (stop_loss - entry_price) * contracts * MNQ_POINT_VALUE - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'atr_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue
                # Take profit
                if price >= take_profit:
                    pnl = (take_profit - entry_price) * contracts * MNQ_POINT_VALUE - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'take_profit'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue
                # Update trailing stop
                new_t = price - atr[i] * sl_atr_mult
                if new_t > trailing_stop:
                    trailing_stop = new_t
                if trailing_stop > stop_loss and price <= trailing_stop:
                    pnl = (trailing_stop - entry_price) * contracts * MNQ_POINT_VALUE - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'trailing_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue

            else:  # Short
                pnl_now = (entry_price - price) * contracts * MNQ_POINT_VALUE
                if pnl_now <= -hard_stop:
                    pnl = -hard_stop - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'hard_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue
                if price >= stop_loss:
                    pnl = (entry_price - stop_loss) * contracts * MNQ_POINT_VALUE - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'atr_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue
                if price <= take_profit:
                    pnl = (entry_price - take_profit) * contracts * MNQ_POINT_VALUE - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'take_profit'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue
                new_t = price + atr[i] * sl_atr_mult
                if trailing_stop == 0 or new_t < trailing_stop:
                    trailing_stop = new_t
                if trailing_stop < stop_loss and price >= trailing_stop:
                    pnl = (entry_price - trailing_stop) * contracts * MNQ_POINT_VALUE - 14.78 - contracts * 0.52
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'trailing_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl
                    if dd > max_drawdown: max_drawdown = dd
                    position = 0; continue

        # ── Check entries ──
        if position == 0 and daily_trades_count < max_trades_day:
            # Only trade during NY session (9:30-16:00 ET)
            if trade_hour < 9 or (trade_hour == 9 and trade_minute < 30) or trade_hour >= 16:
                continue

            uptrend = ema_f[i] > ema_s[i]
            downtrend = ema_f[i] < ema_s[i]
            d_falling = stoch_d[i] < prev_d
            d_rising = stoch_d[i] > prev_d

            # Long entry
            if uptrend and d_falling and stoch_d[i] <= stoch_lo:
                position = contracts
                entry_price = price
                a = atr[i]
                stop_loss = price - a * sl_atr_mult
                take_profit = price + a * sl_atr_mult * tp_rr
                trailing_stop = stop_loss
                daily_trades_count += 1

            # Short entry
            elif downtrend and d_rising and stoch_d[i] >= stoch_hi:
                position = -contracts
                entry_price = price
                a = atr[i]
                stop_loss = price + a * sl_atr_mult
                take_profit = price - a * sl_atr_mult * tp_rr
                trailing_stop = stop_loss
                daily_trades_count += 1

    # Record last day
    if current_date is not None:
        daily_pnls[current_date] = daily_pnl

    # Close open position at end
    if position != 0:
        price = closes[-1]
        if position > 0:
            pnl = (price - entry_price) * contracts * MNQ_POINT_VALUE
        else:
            pnl = (entry_price - price) * contracts * MNQ_POINT_VALUE
        pnl -= 14.78 + contracts * 0.52
        trades.append({'date': current_date, 'pnl': pnl, 'reason': 'end_of_data'})
        total_pnl += pnl
        cumulative_pnl += pnl

    # ── Statistics ──
    if not trades:
        return None

    total_trades = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0

    avg_win = np.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = np.mean([t['pnl'] for t in losses]) if losses else 0

    total_win_pnl = sum(t['pnl'] for t in wins)
    total_loss_pnl = abs(sum(t['pnl'] for t in losses))
    profit_factor = total_win_pnl / total_loss_pnl if total_loss_pnl > 0 else float('inf')

    trading_days = len(daily_pnls)
    trades_per_day = total_trades / trading_days if trading_days > 0 else 0

    # 30% consistency check
    if total_pnl > 0 and daily_pnls:
        max_day_pnl = max(daily_pnls.values())
        consistency_pct = (max_day_pnl / total_pnl * 100) if total_pnl > 0 else 0
        passes_consistency = consistency_pct <= 30
    else:
        consistency_pct = 0
        passes_consistency = total_pnl <= 0

    # Profit days
    profit_days = sum(1 for v in daily_pnls.values() if v > 50)

    return {
        'params': params,
        'total_trades': total_trades,
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'total_pnl': total_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'trading_days': trading_days,
        'trades_per_day': trades_per_day,
        'consistency_pct': consistency_pct,
        'passes_consistency': passes_consistency,
        'profit_days': profit_days,
        'passes_apex_dd': max_drawdown < APEX_TRAILING_DRAWDOWN,
    }


def load_data(timeframe):
    """Load and prepare data for a given timeframe."""
    path = f'data/mnq_{timeframe}.csv'
    df = pd.read_csv(path)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(
        pd.to_datetime(df['time'], unit='s')
    ).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Last 6 months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=6)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)
    return df


def main():
    print("=" * 80)
    print("APEX 150K - OPTIMIZED STRATEGY PARAMETER SWEEP")
    print("=" * 80)
    print(f"Account: Apex 150K | Contracts: {CONTRACTS} MNQ")
    print(f"Max Trailing Drawdown: ${APEX_TRAILING_DRAWDOWN:,.0f}")
    print(f"Target: Higher win rate, fewer trades, Apex compliant")
    print()

    # Parameter grid - focused for Apex compliance
    param_grid = {
        'timeframe':     ['5min', '15min'],
        'ema_fast':      [21, 34, 50],
        'ema_slow':      [55, 89, 200],
        'stoch_k':       [9, 14],
        'stoch_d':       [3],
        'stoch_lo':      [10, 15, 20],
        'stoch_hi':      [80, 85, 90],
        'atr_len':       [9, 14],
        'sl_atr_mult':   [1.5, 2.0, 2.5],
        'tp_rr':         [1.5, 2.0, 2.5],
        'hard_stop':     [150, 300],
        'max_trades_day': [2, 3, 5],
    }

    # Filter out invalid EMA combos (fast must be < slow)
    all_combos = list(product(
        param_grid['timeframe'],
        param_grid['ema_fast'], param_grid['ema_slow'],
        param_grid['stoch_k'], param_grid['stoch_d'],
        param_grid['stoch_lo'], param_grid['stoch_hi'],
        param_grid['atr_len'],
        param_grid['sl_atr_mult'], param_grid['tp_rr'],
        param_grid['hard_stop'], param_grid['max_trades_day'],
    ))

    valid_combos = [c for c in all_combos if c[1] < c[2] and c[5] < c[6]]
    print(f"Total parameter combinations: {len(valid_combos)}")

    # Preload data
    data_cache = {}
    for tf in param_grid['timeframe']:
        print(f"Loading {tf} data...")
        data_cache[tf] = load_data(tf)
        print(f"  {len(data_cache[tf])} bars loaded")

    results = []
    tested = 0

    for combo in valid_combos:
        tf, ef, es, sk, sd, slo, shi, al, slam, tprr, hs, mtd = combo

        params = {
            'timeframe': tf,
            'ema_fast': ef, 'ema_slow': es,
            'stoch_k': sk, 'stoch_d': sd, 'stoch_smt': 2,
            'stoch_lo': slo, 'stoch_hi': shi,
            'atr_len': al,
            'sl_atr_mult': slam, 'tp_rr': tprr,
            'hard_stop': hs,
            'max_trades_day': mtd,
            'contracts': CONTRACTS,
        }

        result = run_backtest(data_cache[tf], params)
        tested += 1

        if result and result['total_trades'] >= 20:
            results.append(result)

        if tested % 500 == 0:
            print(f"  Tested {tested}/{len(valid_combos)} combinations... "
                  f"({len(results)} valid results)")

    print(f"\nCompleted: {tested} combinations tested, {len(results)} valid results")

    if not results:
        print("No valid results found!")
        return

    # ── Filter for Apex compliance ──
    apex_ok = [r for r in results if r['passes_apex_dd'] and r['total_pnl'] > 0]
    print(f"Apex drawdown compliant (< ${APEX_TRAILING_DRAWDOWN:,.0f}): {len(apex_ok)}")

    # ── Rank by composite score ──
    # Score = win_rate * profit_factor * (1 if passes_dd) * (1 if passes_consistency)
    # Penalize too many trades per day
    for r in apex_ok:
        tpd_penalty = 1.0 if r['trades_per_day'] <= 5 else 0.5
        consistency_bonus = 1.2 if r['passes_consistency'] else 0.8
        r['score'] = (r['win_rate'] * r['profit_factor'] * tpd_penalty *
                      consistency_bonus * (r['total_pnl'] / 1000))

    ranked = sorted(apex_ok, key=lambda x: x['score'], reverse=True)

    # ── Display Top 20 ──
    print("\n" + "=" * 80)
    print("TOP 20 APEX-FRIENDLY STRATEGIES")
    print("=" * 80)

    for i, r in enumerate(ranked[:20]):
        p = r['params']
        print(f"\n{'='*60}")
        print(f"#{i+1} | Score: {r['score']:.1f}")
        print(f"  Timeframe: {p['timeframe']} | EMA: {p['ema_fast']}/{p['ema_slow']}")
        print(f"  Stoch: K={p['stoch_k']} D={p['stoch_d']} ({p['stoch_lo']}/{p['stoch_hi']})")
        print(f"  ATR: {p['atr_len']} | SL: {p['sl_atr_mult']}x | TP: {p['tp_rr']}R")
        print(f"  Hard Stop: ${p['hard_stop']} | Max Trades/Day: {p['max_trades_day']}")
        print(f"  ---")
        print(f"  Trades: {r['total_trades']} ({r['trades_per_day']:.1f}/day)")
        print(f"  Win Rate: {r['win_rate']:.1f}% | P/F: {r['profit_factor']:.2f}")
        print(f"  Total P&L: ${r['total_pnl']:,.2f}")
        print(f"  Avg Win: ${r['avg_win']:,.2f} | Avg Loss: ${r['avg_loss']:,.2f}")
        print(f"  Max Drawdown: ${r['max_drawdown']:,.2f}")
        print(f"  Profit Days: {r['profit_days']}/{r['trading_days']}")
        print(f"  30% Consistency: {'PASS' if r['passes_consistency'] else 'FAIL'} ({r['consistency_pct']:.1f}%)")
        print(f"  Apex DD Check: {'PASS' if r['passes_apex_dd'] else 'FAIL'}")

    # ── Also show best by win rate ──
    print("\n" + "=" * 80)
    print("TOP 10 BY WIN RATE (Apex compliant)")
    print("=" * 80)

    by_wr = sorted(apex_ok, key=lambda x: x['win_rate'], reverse=True)
    for i, r in enumerate(by_wr[:10]):
        p = r['params']
        print(f"#{i+1} WR={r['win_rate']:.1f}% | {p['timeframe']} EMA {p['ema_fast']}/{p['ema_slow']} "
              f"Stoch {p['stoch_lo']}/{p['stoch_hi']} | "
              f"Trades={r['total_trades']} ({r['trades_per_day']:.1f}/d) | "
              f"P&L=${r['total_pnl']:,.0f} | PF={r['profit_factor']:.2f} | "
              f"DD=${r['max_drawdown']:,.0f} | "
              f"SL={p['sl_atr_mult']}x TP={p['tp_rr']}R HS=${p['hard_stop']}")

    # ── Best by lowest drawdown with good P&L ──
    print("\n" + "=" * 80)
    print("TOP 10 LOWEST DRAWDOWN (with P&L > $500)")
    print("=" * 80)

    good_pnl = [r for r in apex_ok if r['total_pnl'] > 500]
    by_dd = sorted(good_pnl, key=lambda x: x['max_drawdown'])
    for i, r in enumerate(by_dd[:10]):
        p = r['params']
        print(f"#{i+1} DD=${r['max_drawdown']:,.0f} | {p['timeframe']} EMA {p['ema_fast']}/{p['ema_slow']} "
              f"Stoch {p['stoch_lo']}/{p['stoch_hi']} | "
              f"WR={r['win_rate']:.1f}% | Trades={r['total_trades']} ({r['trades_per_day']:.1f}/d) | "
              f"P&L=${r['total_pnl']:,.0f} | PF={r['profit_factor']:.2f} | "
              f"SL={p['sl_atr_mult']}x TP={p['tp_rr']}R HS=${p['hard_stop']}")

    # ── Save results ──
    rows = []
    for r in ranked[:100]:
        p = r['params']
        rows.append({
            'rank': len(rows) + 1,
            'score': r['score'],
            'timeframe': p['timeframe'],
            'ema_fast': p['ema_fast'], 'ema_slow': p['ema_slow'],
            'stoch_k': p['stoch_k'], 'stoch_d': p['stoch_d'],
            'stoch_lo': p['stoch_lo'], 'stoch_hi': p['stoch_hi'],
            'atr_len': p['atr_len'],
            'sl_atr_mult': p['sl_atr_mult'], 'tp_rr': p['tp_rr'],
            'hard_stop': p['hard_stop'],
            'max_trades_day': p['max_trades_day'],
            'total_trades': r['total_trades'],
            'trades_per_day': r['trades_per_day'],
            'win_rate': r['win_rate'],
            'profit_factor': r['profit_factor'],
            'total_pnl': r['total_pnl'],
            'avg_win': r['avg_win'], 'avg_loss': r['avg_loss'],
            'max_drawdown': r['max_drawdown'],
            'profit_days': r['profit_days'],
            'trading_days': r['trading_days'],
            'consistency_pct': r['consistency_pct'],
            'passes_consistency': r['passes_consistency'],
            'passes_apex_dd': r['passes_apex_dd'],
        })

    df_results = pd.DataFrame(rows)
    df_results.to_csv('results/apex_150k_sweep_results.csv', index=False)
    print(f"\nResults saved to results/apex_150k_sweep_results.csv")

    # ── Print THE WINNER ──
    if ranked:
        w = ranked[0]
        p = w['params']
        print("\n" + "=" * 80)
        print("RECOMMENDED APEX 150K STRATEGY")
        print("=" * 80)
        print(f"  Timeframe: {p['timeframe']}")
        print(f"  EMA: {p['ema_fast']}/{p['ema_slow']}")
        print(f"  Stochastic: K={p['stoch_k']} D={p['stoch_d']} ({p['stoch_lo']}/{p['stoch_hi']})")
        print(f"  ATR Period: {p['atr_len']}")
        print(f"  Stop Loss: {p['sl_atr_mult']}x ATR")
        print(f"  Take Profit: {p['tp_rr']}R")
        print(f"  Hard Stop: ${p['hard_stop']}")
        print(f"  Max Trades/Day: {p['max_trades_day']}")
        print(f"  Contracts: {CONTRACTS}")
        print(f"  ---")
        print(f"  Total Trades: {w['total_trades']} ({w['trades_per_day']:.1f}/day)")
        print(f"  Win Rate: {w['win_rate']:.1f}%")
        print(f"  Profit Factor: {w['profit_factor']:.2f}")
        print(f"  Total P&L: ${w['total_pnl']:,.2f}")
        print(f"  Max Drawdown: ${w['max_drawdown']:,.2f}")
        print(f"  Apex DD: {'PASS' if w['passes_apex_dd'] else 'FAIL'}")
        print(f"  30% Consistency: {'PASS' if w['passes_consistency'] else 'FAIL'}")
        print("=" * 80)


if __name__ == "__main__":
    main()
