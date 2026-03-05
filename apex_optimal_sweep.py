#!/usr/bin/env python3
"""
Apex 150K - Full Parameter Sweep for Optimal Strategy
======================================================
Timeframes: 1min, 2min
Contracts: 2, 3, 5, 6
Trades/day: 10-30
Last 3 months
Finds best ATR, stop loss, trailing stop, and entry parameters.
"""

import sys
import pandas as pd
import numpy as np
from itertools import product
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

MNQ_POINT_VALUE = 2.0
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


def run_backtest(df, params):
    """Run a single backtest with given parameters."""
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
    trail_mult = params['trail_mult']
    max_trades_day = params['max_trades_day']
    contracts = params['contracts']
    pv = MNQ_POINT_VALUE

    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    hours = df['hour'].values
    minutes = df['minute'].values
    dates = df['date'].values

    # Pre-compute indicators
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
    commission = 14.78 + contracts * 0.52

    warmup = max(ema_slow_p, stoch_k_p + stoch_d_p + stoch_smt) + 10

    for i in range(warmup, len(df)):
        price = closes[i]
        trade_date = dates[i]
        h = hours[i]
        m = minutes[i]

        # Daily reset
        if trade_date != current_date:
            if current_date is not None:
                daily_pnls[current_date] = daily_pnl
            daily_pnl = 0.0
            daily_trades_count = 0
            current_date = trade_date

        # Force close by 4:55 PM ET
        if position != 0 and h == 16 and m >= 55:
            if position > 0:
                pnl = (price - entry_price) * contracts * pv - commission
            else:
                pnl = (entry_price - price) * contracts * pv - commission
            trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'eod_close'})
            total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
            if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
            dd = peak_pnl - cumulative_pnl
            if dd > max_drawdown: max_drawdown = dd
            position = 0
            continue

        # Skip if indicators not ready
        if np.isnan(atr[i]) or np.isnan(stoch_d[i]):
            continue

        prev_d = stoch_d[i-1] if i > 0 and not np.isnan(stoch_d[i-1]) else stoch_d[i]

        # ── Check exits ──
        if position != 0:
            if position > 0:
                pnl_now = (price - entry_price) * contracts * pv
                # Hard stop
                if pnl_now <= -hard_stop:
                    pnl = -hard_stop - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'hard_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue
                # ATR stop
                if price <= stop_loss:
                    pnl = (stop_loss - entry_price) * contracts * pv - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'atr_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue
                # Take profit
                if price >= take_profit:
                    pnl = (take_profit - entry_price) * contracts * pv - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'take_profit'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue
                # Trailing stop
                new_t = price - atr[i] * trail_mult
                if new_t > trailing_stop:
                    trailing_stop = new_t
                if trailing_stop > stop_loss and price <= trailing_stop:
                    pnl = (trailing_stop - entry_price) * contracts * pv - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'trail_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue

            else:  # Short
                pnl_now = (entry_price - price) * contracts * pv
                if pnl_now <= -hard_stop:
                    pnl = -hard_stop - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'hard_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue
                if price >= stop_loss:
                    pnl = (entry_price - stop_loss) * contracts * pv - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'atr_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue
                if price <= take_profit:
                    pnl = (entry_price - take_profit) * contracts * pv - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'take_profit'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue
                new_t = price + atr[i] * trail_mult
                if trailing_stop == 0 or new_t < trailing_stop:
                    trailing_stop = new_t
                if trailing_stop < stop_loss and price >= trailing_stop:
                    pnl = (entry_price - trailing_stop) * contracts * pv - commission
                    trades.append({'date': trade_date, 'pnl': pnl, 'reason': 'trail_stop'})
                    total_pnl += pnl; daily_pnl += pnl; cumulative_pnl += pnl
                    if cumulative_pnl > peak_pnl: peak_pnl = cumulative_pnl
                    dd = peak_pnl - cumulative_pnl; max_drawdown = max(max_drawdown, dd)
                    position = 0; continue

        # ── Check entries ──
        if position == 0 and daily_trades_count < max_trades_day:
            # Only trade during NY session (9:30-16:00 ET)
            if h < 9 or (h == 9 and m < 30) or h >= 16:
                continue

            uptrend = ema_f[i] > ema_s[i]
            downtrend = ema_f[i] < ema_s[i]
            d_falling = stoch_d[i] < prev_d
            d_rising = stoch_d[i] > prev_d

            if uptrend and d_falling and stoch_d[i] <= stoch_lo:
                position = contracts
                entry_price = price
                a = atr[i]
                stop_loss = price - a * sl_atr_mult
                take_profit = price + a * sl_atr_mult * tp_rr
                trailing_stop = stop_loss
                daily_trades_count += 1

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

    # Close open position
    if position != 0:
        price = closes[-1]
        if position > 0:
            pnl = (price - entry_price) * contracts * pv - commission
        else:
            pnl = (entry_price - price) * contracts * pv - commission
        trades.append({'date': current_date, 'pnl': pnl, 'reason': 'end_of_data'})
        total_pnl += pnl; cumulative_pnl += pnl

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

    # 30% consistency
    if total_pnl > 0 and daily_pnls:
        max_day_pnl = max(daily_pnls.values())
        consistency_pct = (max_day_pnl / total_pnl * 100) if total_pnl > 0 else 0
    else:
        consistency_pct = 100

    profit_days = sum(1 for v in daily_pnls.values() if v > 50)

    return {
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
        'passes_consistency': consistency_pct <= 30,
        'profit_days': profit_days,
        'passes_apex_dd': max_drawdown < APEX_TRAILING_DRAWDOWN,
    }


def load_data(timeframe, months=3):
    """Load and prepare data for a given timeframe."""
    path = f'data/mnq_{timeframe}.csv'
    df = pd.read_csv(path)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    ts = pd.to_datetime(df['time'].astype(float), unit='s', utc=True)
    # Convert to ET using offset (EST = UTC-5)
    ts_et = ts - pd.Timedelta(hours=5)
    df['timestamp'] = ts_et
    df['date'] = ts_et.dt.date
    df['hour'] = ts_et.dt.hour
    df['minute'] = ts_et.dt.minute
    df = df[['timestamp', 'date', 'hour', 'minute', 'open', 'high', 'low', 'close']].dropna()
    df = df.sort_values('timestamp').reset_index(drop=True)

    # Last N months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=months)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)
    return df


def main():
    print("=" * 80)
    print("APEX 150K - OPTIMAL PARAMETER SWEEP")
    print("=" * 80)
    print("Timeframes: 1min, 2min | Last 3 months")
    print("Contracts: 2, 3, 5, 6 | Trades/day: 10-30")
    print()

    # Parameter grid
    timeframes = ['1min', '2min']
    contracts_list = [2, 3, 5, 6]
    ema_fast_list = [21, 34]
    ema_slow_list = [55, 89]
    stoch_k_list = [9, 14]
    stoch_lo_list = [15, 20, 25, 30]
    stoch_hi_list = [70, 75, 80, 85]
    atr_len_list = [9, 14]
    sl_atr_mult_list = [1.0, 1.5, 2.0, 2.5, 3.0]
    tp_rr_list = [1.2, 1.5, 1.8, 2.0, 2.5]
    trail_mult_list = [0.5, 0.7, 1.0, 1.5]
    hard_stop_list = [100, 200, 400]
    max_trades_list = [10, 15, 20, 30]

    # Build combos
    all_combos = []
    for tf in timeframes:
        for c in contracts_list:
            for ef in ema_fast_list:
                for es in ema_slow_list:
                    if ef >= es:
                        continue
                    for sk in stoch_k_list:
                        for slo in stoch_lo_list:
                            for shi in stoch_hi_list:
                                if slo >= shi:
                                    continue
                                for al in atr_len_list:
                                    for slam in sl_atr_mult_list:
                                        for tprr in tp_rr_list:
                                            for tm in trail_mult_list:
                                                for hs in hard_stop_list:
                                                    for mtd in max_trades_list:
                                                        all_combos.append({
                                                            'timeframe': tf,
                                                            'contracts': c,
                                                            'ema_fast': ef, 'ema_slow': es,
                                                            'stoch_k': sk, 'stoch_d': 3, 'stoch_smt': 2,
                                                            'stoch_lo': slo, 'stoch_hi': shi,
                                                            'atr_len': al,
                                                            'sl_atr_mult': slam,
                                                            'tp_rr': tprr,
                                                            'trail_mult': tm,
                                                            'hard_stop': hs,
                                                            'max_trades_day': mtd,
                                                        })

    print(f"Total combinations: {len(all_combos)}")

    # Too many? Sample strategically
    if len(all_combos) > 50000:
        # First do a coarse sweep with fewer params
        print("Large grid detected - running focused 2-phase sweep...")

        # Phase 1: Coarse sweep with reduced grid
        coarse_combos = []
        for tf in timeframes:
            for c in [3, 5]:  # Focus on 3 and 5 first
                for ef in ema_fast_list:
                    for es in ema_slow_list:
                        if ef >= es: continue
                        for sk in stoch_k_list:
                            for slo in [15, 25]:
                                for shi in [75, 85]:
                                    for al in atr_len_list:
                                        for slam in [1.5, 2.0, 2.5]:
                                            for tprr in [1.5, 2.0, 2.5]:
                                                for tm in [0.7, 1.0]:
                                                    for hs in [200, 400]:
                                                        for mtd in [10, 20]:
                                                            coarse_combos.append({
                                                                'timeframe': tf,
                                                                'contracts': c,
                                                                'ema_fast': ef, 'ema_slow': es,
                                                                'stoch_k': sk, 'stoch_d': 3, 'stoch_smt': 2,
                                                                'stoch_lo': slo, 'stoch_hi': shi,
                                                                'atr_len': al,
                                                                'sl_atr_mult': slam,
                                                                'tp_rr': tprr,
                                                                'trail_mult': tm,
                                                                'hard_stop': hs,
                                                                'max_trades_day': mtd,
                                                            })

        print(f"Phase 1 (coarse): {len(coarse_combos)} combinations")
        sweep_combos = coarse_combos
    else:
        sweep_combos = all_combos

    # Preload data
    data_cache = {}
    for tf in timeframes:
        print(f"Loading {tf} data...")
        data_cache[tf] = load_data(tf, months=3)
        print(f"  {len(data_cache[tf])} bars")

    results = []
    tested = 0

    for params in sweep_combos:
        tf = params['timeframe']
        result = run_backtest(data_cache[tf], params)
        tested += 1

        if result and result['total_trades'] >= 10:
            result['params'] = params
            results.append(result)

        if tested % 1000 == 0:
            print(f"  [{tested}/{len(sweep_combos)}] {len(results)} valid results so far...")

    print(f"\nPhase 1 done: {len(results)} valid results from {tested} tests")

    if not results:
        print("No valid results found!")
        return

    # ── Find top Phase 1 winners, then do Phase 2 fine-tuning ──
    # Filter profitable + Apex compliant
    profitable = [r for r in results if r['total_pnl'] > 0 and r['passes_apex_dd']]
    print(f"Profitable + Apex DD compliant: {len(profitable)}")

    if not profitable:
        profitable = [r for r in results if r['total_pnl'] > 0]
        print(f"Profitable (ignoring DD): {len(profitable)}")

    # Score them
    for r in profitable:
        wr_bonus = r['win_rate'] / 50.0  # normalize around 50%
        pf_bonus = min(r['profit_factor'], 5.0)  # cap PF
        pnl_bonus = r['total_pnl'] / 1000.0
        dd_penalty = 1.0 if r['passes_apex_dd'] else 0.3
        consistency_bonus = 1.2 if r['passes_consistency'] else 0.8
        r['score'] = wr_bonus * pf_bonus * pnl_bonus * dd_penalty * consistency_bonus

    ranked = sorted(profitable, key=lambda x: x['score'], reverse=True)

    # ── Phase 2: Fine-tune around top 5 winners ──
    if len(ranked) >= 5:
        print(f"\nPhase 2: Fine-tuning around top 5 winners...")
        top5 = ranked[:5]

        fine_combos = []
        for r in top5:
            p = r['params']
            # Test all contract sizes around this winner
            for c in contracts_list:
                for slo in [p['stoch_lo'] - 5, p['stoch_lo'], p['stoch_lo'] + 5]:
                    for shi in [p['stoch_hi'] - 5, p['stoch_hi'], p['stoch_hi'] + 5]:
                        if slo < 5 or slo > 40 or shi < 60 or shi > 95 or slo >= shi:
                            continue
                        for slam in [p['sl_atr_mult'] - 0.5, p['sl_atr_mult'], p['sl_atr_mult'] + 0.5]:
                            if slam < 0.5 or slam > 4.0: continue
                            for tprr in [p['tp_rr'] - 0.3, p['tp_rr'], p['tp_rr'] + 0.3]:
                                if tprr < 1.0 or tprr > 3.5: continue
                                for tm in [p['trail_mult'] - 0.3, p['trail_mult'], p['trail_mult'] + 0.3]:
                                    if tm < 0.3 or tm > 2.5: continue
                                    for hs in [p['hard_stop'] - 100, p['hard_stop'], p['hard_stop'] + 100]:
                                        if hs < 50 or hs > 600: continue
                                        for mtd in [10, 15, 20, 25, 30]:
                                            fine_combos.append({
                                                'timeframe': p['timeframe'],
                                                'contracts': c,
                                                'ema_fast': p['ema_fast'], 'ema_slow': p['ema_slow'],
                                                'stoch_k': p['stoch_k'], 'stoch_d': 3, 'stoch_smt': 2,
                                                'stoch_lo': slo, 'stoch_hi': shi,
                                                'atr_len': p['atr_len'],
                                                'sl_atr_mult': slam,
                                                'tp_rr': tprr,
                                                'trail_mult': tm,
                                                'hard_stop': hs,
                                                'max_trades_day': mtd,
                                            })

        # Deduplicate
        seen = set()
        unique_fine = []
        for fc in fine_combos:
            key = tuple(sorted(fc.items()))
            if key not in seen:
                seen.add(key)
                unique_fine.append(fc)

        print(f"  Phase 2 combinations: {len(unique_fine)}")

        tested2 = 0
        for params in unique_fine:
            tf = params['timeframe']
            result = run_backtest(data_cache[tf], params)
            tested2 += 1
            if result and result['total_trades'] >= 10:
                result['params'] = params
                results.append(result)
            if tested2 % 1000 == 0:
                print(f"  [{tested2}/{len(unique_fine)}] Phase 2...")

        print(f"Phase 2 done: {tested2} additional tests")

    # ── Final ranking ──
    profitable = [r for r in results if r['total_pnl'] > 0]
    for r in profitable:
        wr_bonus = r['win_rate'] / 50.0
        pf_bonus = min(r['profit_factor'], 5.0)
        pnl_bonus = r['total_pnl'] / 1000.0
        dd_penalty = 1.0 if r['passes_apex_dd'] else 0.3
        consistency_bonus = 1.2 if r['passes_consistency'] else 0.8
        r['score'] = wr_bonus * pf_bonus * pnl_bonus * dd_penalty * consistency_bonus

    final_ranked = sorted(profitable, key=lambda x: x['score'], reverse=True)

    # ── Display Results ──
    print("\n" + "=" * 80)
    print("TOP 25 APEX-OPTIMIZED STRATEGIES")
    print("=" * 80)

    for i, r in enumerate(final_ranked[:25]):
        p = r['params']
        print(f"\n{'─'*60}")
        print(f"#{i+1} | Score: {r['score']:.1f} | {p['timeframe']} | {p['contracts']} contracts")
        print(f"  EMA: {p['ema_fast']}/{p['ema_slow']} | Stoch K={p['stoch_k']} ({p['stoch_lo']}/{p['stoch_hi']})")
        print(f"  ATR: {p['atr_len']} | SL: {p['sl_atr_mult']}x | TP: {p['tp_rr']}R | Trail: {p['trail_mult']}x")
        print(f"  Hard Stop: ${p['hard_stop']} | Max Trades/Day: {p['max_trades_day']}")
        print(f"  Trades: {r['total_trades']} ({r['trades_per_day']:.1f}/day) | "
              f"WR: {r['win_rate']:.1f}% | PF: {r['profit_factor']:.2f}")
        print(f"  P&L: ${r['total_pnl']:,.2f} | Avg Win: ${r['avg_win']:,.2f} | Avg Loss: ${r['avg_loss']:,.2f}")
        print(f"  Max DD: ${r['max_drawdown']:,.2f} | Apex DD: {'PASS' if r['passes_apex_dd'] else 'FAIL'}")
        print(f"  Profit Days: {r['profit_days']}/{r['trading_days']} | "
              f"30% Rule: {'PASS' if r['passes_consistency'] else 'FAIL'} ({r['consistency_pct']:.1f}%)")

    # ── Best per contract size ──
    for c in contracts_list:
        c_results = [r for r in final_ranked if r['params']['contracts'] == c]
        if c_results:
            best = c_results[0]
            p = best['params']
            print(f"\n{'='*60}")
            print(f"BEST FOR {c} CONTRACTS:")
            print(f"  {p['timeframe']} | EMA {p['ema_fast']}/{p['ema_slow']} | "
                  f"Stoch {p['stoch_lo']}/{p['stoch_hi']} K={p['stoch_k']}")
            print(f"  ATR {p['atr_len']} | SL {p['sl_atr_mult']}x | TP {p['tp_rr']}R | "
                  f"Trail {p['trail_mult']}x | HS ${p['hard_stop']}")
            print(f"  WR: {best['win_rate']:.1f}% | PF: {best['profit_factor']:.2f} | "
                  f"P&L: ${best['total_pnl']:,.2f}")
            print(f"  Trades: {best['total_trades']} ({best['trades_per_day']:.1f}/day) | "
                  f"DD: ${best['max_drawdown']:,.2f}")

    # ── Save to CSV ──
    rows = []
    for r in final_ranked[:200]:
        p = r['params']
        rows.append({
            'rank': len(rows) + 1, 'score': r['score'],
            'timeframe': p['timeframe'], 'contracts': p['contracts'],
            'ema_fast': p['ema_fast'], 'ema_slow': p['ema_slow'],
            'stoch_k': p['stoch_k'], 'stoch_lo': p['stoch_lo'], 'stoch_hi': p['stoch_hi'],
            'atr_len': p['atr_len'],
            'sl_atr_mult': p['sl_atr_mult'], 'tp_rr': p['tp_rr'],
            'trail_mult': p['trail_mult'], 'hard_stop': p['hard_stop'],
            'max_trades_day': p['max_trades_day'],
            'total_trades': r['total_trades'], 'trades_per_day': round(r['trades_per_day'], 1),
            'win_rate': round(r['win_rate'], 1),
            'profit_factor': round(r['profit_factor'], 2),
            'total_pnl': round(r['total_pnl'], 2),
            'avg_win': round(r['avg_win'], 2), 'avg_loss': round(r['avg_loss'], 2),
            'max_drawdown': round(r['max_drawdown'], 2),
            'profit_days': r['profit_days'], 'trading_days': r['trading_days'],
            'consistency_pct': round(r['consistency_pct'], 1),
            'apex_dd_pass': r['passes_apex_dd'],
            'consistency_pass': r['passes_consistency'],
        })

    df_out = pd.DataFrame(rows)
    df_out.to_csv('results/apex_optimal_sweep_results.csv', index=False)
    print(f"\nSaved top 200 results to results/apex_optimal_sweep_results.csv")

    # ── THE WINNER ──
    if final_ranked:
        w = final_ranked[0]
        p = w['params']
        print("\n" + "=" * 80)
        print("WINNER - APEX 150K OPTIMAL STRATEGY")
        print("=" * 80)
        print(f"  Timeframe:      {p['timeframe']}")
        print(f"  Contracts:      {p['contracts']}")
        print(f"  EMA Fast/Slow:  {p['ema_fast']}/{p['ema_slow']}")
        print(f"  Stochastic:     K={p['stoch_k']} D=3 Smooth=2 ({p['stoch_lo']}/{p['stoch_hi']})")
        print(f"  ATR Period:     {p['atr_len']}")
        print(f"  SL ATR Mult:    {p['sl_atr_mult']}x")
        print(f"  TP R:R:         {p['tp_rr']}")
        print(f"  Trail Mult:     {p['trail_mult']}x")
        print(f"  Hard Stop:      ${p['hard_stop']}")
        print(f"  Max Trades/Day: {p['max_trades_day']}")
        print(f"  ──────────────────────────")
        print(f"  Total Trades:   {w['total_trades']} ({w['trades_per_day']:.1f}/day)")
        print(f"  Win Rate:       {w['win_rate']:.1f}%")
        print(f"  Profit Factor:  {w['profit_factor']:.2f}")
        print(f"  Total P&L:      ${w['total_pnl']:,.2f}")
        print(f"  Max Drawdown:   ${w['max_drawdown']:,.2f}")
        print(f"  Apex DD:        {'PASS' if w['passes_apex_dd'] else 'FAIL'}")
        print(f"  30% Consistency: {'PASS' if w['passes_consistency'] else 'FAIL'}")
        print("=" * 80)


if __name__ == "__main__":
    main()
