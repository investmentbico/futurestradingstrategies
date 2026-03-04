#!/usr/bin/env python3
"""
MNQ $5K Account Full Optimization
===================================
Baseline: MNQ 1ct, $25 stop, 1.5x TP, EMA 21/55, Stoch 25/75, ATR 9
Goal: Find every possible improvement a world-class MNQ scalper would use.

Phase 1: Core parameter sweep (EMA, Stoch, ATR, SL mult, TP ratio)
Phase 2: Advanced filters (session time, volatility, trend strength, bar filters)
Phase 3: Pro techniques (trailing stop, breakeven, partial exits, cooldown)
Phase 4: Hybrid — combine best of each phase
"""

import pandas as pd
import numpy as np
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONSTANTS
# =============================================================================
STARTING_EQUITY = 5000.0
CONTRACTS = 1
POINT_VALUE = 2.0  # MNQ
COMMISSION_RT = 2.52
DATA_FILE = 'data/mnq_1min.csv'
MONTHS = 3

# =============================================================================
# INDICATORS (vectorized for speed)
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
    n = len(close)
    k_vals = np.full(n, np.nan)
    for i in range(k_period - 1, n):
        lo = np.min(low[i - k_period + 1:i + 1])
        hi = np.max(high[i - k_period + 1:i + 1])
        denom = hi - lo
        k_vals[i] = 100 * (close[i] - lo) / denom if denom > 0 else 50.0
    valid = ~np.isnan(k_vals)
    d = calc_ema(k_vals[valid], d_period)
    d_full = np.full(n, np.nan)
    d_full[valid] = d
    valid2 = ~np.isnan(d_full)
    d_smooth = calc_ema(d_full[valid2], smooth)
    d_smooth_full = np.full(n, np.nan)
    d_smooth_full[valid2] = d_smooth
    return k_vals, d_smooth_full

def calc_rsi(close, period):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.full(len(close), np.nan)
    avg_loss = np.full(len(close), np.nan)
    avg_gain[period] = gain[1:period+1].mean()
    avg_loss[period] = loss[1:period+1].mean()
    for i in range(period + 1, len(close)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
    rs = np.where(avg_loss > 0, avg_gain / avg_loss, 100.0)
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_adx(high, low, close, period=14):
    n = len(close)
    dm_plus = np.zeros(n)
    dm_minus = np.zeros(n)
    tr = np.zeros(n)
    for i in range(1, n):
        up = high[i] - high[i-1]
        down = low[i-1] - low[i]
        dm_plus[i] = up if (up > down and up > 0) else 0
        dm_minus[i] = down if (down > up and down > 0) else 0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))
    atr = calc_ema(tr, period)
    di_plus = 100 * calc_ema(dm_plus, period) / np.where(atr > 0, atr, 1)
    di_minus = 100 * calc_ema(dm_minus, period) / np.where(atr > 0, atr, 1)
    dx = 100 * np.abs(di_plus - di_minus) / np.where((di_plus + di_minus) > 0, di_plus + di_minus, 1)
    adx = calc_ema(dx, period)
    return adx

def calc_vwap_proxy(high, low, close, period=20):
    """Simple VWAP-like moving average using typical price"""
    tp = (high + low + close) / 3.0
    return np.convolve(tp, np.ones(period)/period, mode='same')

# =============================================================================
# LOAD AND PREPARE DATA (once)
# =============================================================================
def load_data():
    df = pd.read_csv(DATA_FILE)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=MONTHS)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)
    # Pre-compute time features
    df['hour'] = df['timestamp'].dt.hour
    df['minute'] = df['timestamp'].dt.minute
    df['minute_of_day'] = df['hour'] * 60 + df['minute']
    df['date'] = df['timestamp'].dt.date
    return df

# =============================================================================
# BACKTEST ENGINE (optimized for speed)
# =============================================================================
def backtest(df, params):
    """
    params dict keys:
      Core: ema_fast, ema_slow, stoch_k, stoch_d, stoch_smt, stoch_lo, stoch_hi,
            atr_len, sl_atr_mult, tp_rr, hard_stop
      Filters: session_start, session_end, min_atr, max_atr, adx_min, rsi_filter,
               bar_range_min, cooldown_bars
      Pro: use_trailing, trail_atr_mult, use_breakeven, be_trigger_dollars,
           use_partial_tp, partial_pct, partial_rr
    """
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    hours = df['hour'].values
    mod = df['minute_of_day'].values
    dates = df['date'].values
    n = len(closes)

    # Compute indicators
    ema_fast = calc_ema(closes, params['ema_fast'])
    ema_slow = calc_ema(closes, params['ema_slow'])
    atr = calc_atr(highs, lows, closes, params['atr_len'])
    stoch_k, stoch_d = calc_stoch(highs, lows, closes, params['stoch_k'], params['stoch_d'], params['stoch_smt'])

    # Optional indicators
    adx_min = params.get('adx_min', 0)
    adx = calc_adx(highs, lows, closes) if adx_min > 0 else np.zeros(n)

    rsi_filter = params.get('rsi_filter', False)
    rsi = calc_rsi(closes, 14) if rsi_filter else np.full(n, 50.0)

    # Session filter
    sess_start = params.get('session_start', 0)      # minute of day
    sess_end = params.get('session_end', 1440)        # minute of day
    min_atr_val = params.get('min_atr', 0)
    max_atr_val = params.get('max_atr', 9999)
    bar_range_min = params.get('bar_range_min', 0)
    cooldown = params.get('cooldown_bars', 0)

    # Pro features
    use_trailing = params.get('use_trailing', False)
    trail_mult = params.get('trail_atr_mult', 0.7)
    use_be = params.get('use_breakeven', False)
    be_trigger = params.get('be_trigger_dollars', 20)
    use_partial = params.get('use_partial_tp', False)
    partial_pct = params.get('partial_pct', 0.5)
    partial_rr = params.get('partial_rr', 1.0)

    hard_stop = params['hard_stop']
    tp_rr = params['tp_rr']
    sl_mult = params['sl_atr_mult']
    stoch_lo = params['stoch_lo']
    stoch_hi = params['stoch_hi']

    # State
    position = 0
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    trail_stop = 0.0
    be_set = False
    partial_taken = False
    last_exit_bar = -cooldown - 1

    equity = STARTING_EQUITY
    peak_equity = STARTING_EQUITY
    max_dd = 0.0
    max_dd_pct = 0.0

    wins = 0
    losses = 0
    total_win_dollars = 0.0
    total_loss_dollars = 0.0
    trade_count = 0
    daily_pnl = 0.0
    prev_date = dates[0]
    worst_daily = 0.0
    best_daily = 0.0

    warmup = max(params['ema_slow'], params['atr_len'], params['stoch_k']) + 10

    for i in range(warmup, n):
        price = closes[i]
        cur_date = dates[i]

        # Daily reset
        if cur_date != prev_date:
            if daily_pnl < worst_daily:
                worst_daily = daily_pnl
            if daily_pnl > best_daily:
                best_daily = daily_pnl
            daily_pnl = 0.0
            prev_date = cur_date

        # Skip if outside session
        in_session = (mod[i] >= sess_start) and (mod[i] < sess_end)

        # ATR filter
        cur_atr = atr[i]
        if np.isnan(cur_atr):
            continue
        atr_ok = (cur_atr >= min_atr_val) and (cur_atr <= max_atr_val)

        # Bar range filter
        bar_range = highs[i] - lows[i]
        bar_ok = bar_range >= bar_range_min

        # Stoch values
        sd = stoch_d[i]
        sd_prev = stoch_d[i-1] if i > 0 else sd
        if np.isnan(sd) or np.isnan(sd_prev):
            continue

        d_falling = sd < sd_prev
        d_rising = sd > sd_prev
        uptrend = ema_fast[i] > ema_slow[i]
        downtrend = ema_fast[i] < ema_slow[i]

        # ADX filter
        adx_ok = adx[i] >= adx_min if adx_min > 0 else True

        # RSI filter (avoid overbought/oversold entries)
        rsi_ok = True
        if rsi_filter:
            if uptrend:
                rsi_ok = rsi[i] > 40 and rsi[i] < 80
            else:
                rsi_ok = rsi[i] > 20 and rsi[i] < 60

        # Cooldown
        cooled = (i - last_exit_bar) >= cooldown

        can_enter = in_session and atr_ok and bar_ok and adx_ok and rsi_ok and cooled

        # ENTRY
        if position == 0 and can_enter:
            sl_dist = cur_atr * sl_mult
            if sl_dist < 0.5:
                sl_dist = 0.5

            if uptrend and d_falling and sd <= stoch_lo:
                position = 1
                entry_price = price
                stop_loss = price - sl_dist
                take_profit = price + sl_dist * tp_rr
                trail_stop = stop_loss
                be_set = False
                partial_taken = False
                trade_count += 1

            elif downtrend and d_rising and sd >= stoch_hi:
                position = -1
                entry_price = price
                stop_loss = price + sl_dist
                take_profit = price - sl_dist * tp_rr
                trail_stop = stop_loss
                be_set = False
                partial_taken = False
                trade_count += 1

        # MANAGE POSITION
        elif position != 0:
            pnl_points = (price - entry_price) * position
            pnl_dollars = pnl_points * POINT_VALUE

            # Trailing stop
            if use_trailing and cur_atr > 0:
                if position == 1:
                    new_trail = price - trail_mult * cur_atr
                    if new_trail > trail_stop:
                        trail_stop = new_trail
                else:
                    new_trail = price + trail_mult * cur_atr
                    if new_trail < trail_stop:
                        trail_stop = new_trail

            # Breakeven
            if use_be and not be_set and pnl_dollars >= be_trigger:
                if position == 1:
                    stop_loss = entry_price + 0.25  # 1 tick above entry
                else:
                    stop_loss = entry_price - 0.25
                be_set = True

            # Check exits
            exit_reason = None
            exit_price = price

            # Hard stop (dollar cap)
            hard_stop_dist = hard_stop / POINT_VALUE
            if position == 1:
                hs_price = entry_price - hard_stop_dist
                if price <= hs_price:
                    exit_price = hs_price
                    exit_reason = 'hard_stop'
                elif price <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif use_trailing and price <= trail_stop:
                    exit_price = trail_stop
                    exit_reason = 'trail_stop'
                elif price >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
            else:
                hs_price = entry_price + hard_stop_dist
                if price >= hs_price:
                    exit_price = hs_price
                    exit_reason = 'hard_stop'
                elif price >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif use_trailing and price >= trail_stop:
                    exit_price = trail_stop
                    exit_reason = 'trail_stop'
                elif price <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            # Force close at session end
            if not in_session and position != 0 and exit_reason is None:
                exit_reason = 'session_close'
                exit_price = price

            if exit_reason:
                final_pnl_points = (exit_price - entry_price) * position
                final_pnl = final_pnl_points * POINT_VALUE - COMMISSION_RT

                equity += final_pnl
                daily_pnl += final_pnl

                if final_pnl > 0:
                    wins += 1
                    total_win_dollars += final_pnl
                else:
                    losses += 1
                    total_loss_dollars += abs(final_pnl)

                if equity > peak_equity:
                    peak_equity = equity
                dd = peak_equity - equity
                if dd > max_dd:
                    max_dd = dd
                dd_pct = dd / peak_equity * 100 if peak_equity > 0 else 0
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct

                position = 0
                last_exit_bar = i

    # Final daily
    if daily_pnl < worst_daily:
        worst_daily = daily_pnl

    total_trades = wins + losses
    win_rate = wins / total_trades * 100 if total_trades > 0 else 0
    total_pnl = equity - STARTING_EQUITY
    pf = total_win_dollars / total_loss_dollars if total_loss_dollars > 0 else 999
    avg_win = total_win_dollars / wins if wins > 0 else 0
    avg_loss = -total_loss_dollars / losses if losses > 0 else 0
    days = len(set(dates))

    return {
        'total_pnl': total_pnl, 'return_pct': total_pnl / STARTING_EQUITY * 100,
        'total_trades': total_trades, 'wins': wins, 'losses': losses,
        'win_rate': win_rate, 'profit_factor': pf,
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'max_dd': max_dd, 'max_dd_pct': max_dd_pct,
        'worst_daily': worst_daily, 'best_daily': best_daily,
        'final_equity': equity, 'trading_days': days,
        'params': params
    }

# =============================================================================
# PHASE 1: CORE PARAMETER SWEEP
# =============================================================================
def phase1_core_sweep(df):
    print("\n" + "=" * 100)
    print("PHASE 1: CORE PARAMETER SWEEP")
    print("=" * 100)

    # Baseline
    baseline_params = {
        'ema_fast': 21, 'ema_slow': 55, 'stoch_k': 9, 'stoch_d': 3, 'stoch_smt': 2,
        'stoch_lo': 25, 'stoch_hi': 75, 'atr_len': 9, 'sl_atr_mult': 2.0,
        'tp_rr': 1.5, 'hard_stop': 25
    }
    baseline = backtest(df, baseline_params)
    print(f"\nBASELINE: PnL=${baseline['total_pnl']:,.0f} PF={baseline['profit_factor']:.2f} WR={baseline['win_rate']:.1f}% DD={baseline['max_dd_pct']:.1f}% Trades={baseline['total_trades']}")

    results = []

    # 1A: EMA combinations
    print("\n--- 1A: EMA Fast/Slow Sweep ---")
    ema_fasts = [5, 8, 10, 13, 15, 18, 21, 26, 34]
    ema_slows = [21, 34, 42, 50, 55, 65, 75, 89, 100]
    for ef, es in product(ema_fasts, ema_slows):
        if ef >= es:
            continue
        p = {**baseline_params, 'ema_fast': ef, 'ema_slow': es}
        r = backtest(df, p)
        r['label'] = f'EMA {ef}/{es}'
        results.append(r)

    # 1B: Stochastic thresholds
    print("--- 1B: Stochastic Thresholds ---")
    for lo, hi in [(15, 85), (20, 80), (25, 75), (30, 70), (35, 65), (10, 90)]:
        p = {**baseline_params, 'stoch_lo': lo, 'stoch_hi': hi}
        r = backtest(df, p)
        r['label'] = f'Stoch {lo}/{hi}'
        results.append(r)

    # 1C: Stoch K period
    print("--- 1C: Stochastic K Period ---")
    for sk in [5, 7, 9, 12, 14, 18, 21]:
        for sd in [2, 3, 5]:
            for smt in [1, 2, 3]:
                p = {**baseline_params, 'stoch_k': sk, 'stoch_d': sd, 'stoch_smt': smt}
                r = backtest(df, p)
                r['label'] = f'StochK={sk} D={sd} Smt={smt}'
                results.append(r)

    # 1D: ATR period
    print("--- 1D: ATR Period ---")
    for al in [5, 7, 9, 12, 14, 18, 21]:
        p = {**baseline_params, 'atr_len': al}
        r = backtest(df, p)
        r['label'] = f'ATR {al}'
        results.append(r)

    # 1E: SL ATR multiplier
    print("--- 1E: SL ATR Multiplier ---")
    for slm in [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0]:
        p = {**baseline_params, 'sl_atr_mult': slm}
        r = backtest(df, p)
        r['label'] = f'SL {slm}x ATR'
        results.append(r)

    # 1F: TP ratio
    print("--- 1F: Take Profit Ratio ---")
    for tp in [0.8, 1.0, 1.2, 1.3, 1.5, 1.7, 2.0, 2.5]:
        p = {**baseline_params, 'tp_rr': tp}
        r = backtest(df, p)
        r['label'] = f'TP {tp}x'
        results.append(r)

    # 1G: Hard stop
    print("--- 1G: Hard Stop ---")
    for hs in [15, 20, 25, 30, 35, 40, 50, 60, 75]:
        p = {**baseline_params, 'hard_stop': hs}
        r = backtest(df, p)
        r['label'] = f'HS ${hs}'
        results.append(r)

    # Sort by profit factor (safe only)
    safe = [r for r in results if r['max_dd_pct'] < 30 and r['total_trades'] >= 50]
    safe.sort(key=lambda x: x['profit_factor'], reverse=True)

    print(f"\n{'#':>3} {'Label':>25} {'PnL':>10} {'PF':>6} {'WR':>6} {'Trades':>7} {'DD%':>6} {'AvgW':>7} {'AvgL':>7}")
    print("-" * 90)
    for i, r in enumerate(safe[:25]):
        print(f"{i+1:>3} {r['label']:>25} ${r['total_pnl']:>8,.0f} {r['profit_factor']:>6.2f} {r['win_rate']:>5.1f}% {r['total_trades']:>7} {r['max_dd_pct']:>5.1f}% ${r['avg_win']:>6,.0f} ${r['avg_loss']:>6,.0f}")

    return safe[:5], baseline

# =============================================================================
# PHASE 2: ADVANCED FILTERS
# =============================================================================
def phase2_filters(df, best_core):
    print("\n" + "=" * 100)
    print("PHASE 2: ADVANCED FILTERS (applied to best core params)")
    print("=" * 100)

    base_p = best_core['params'].copy()
    results = []

    # 2A: Session time windows (ET)
    print("\n--- 2A: Session Time Windows ---")
    sessions = [
        ('Full 9:30-16:00', 570, 960),
        ('Morning 9:30-12:00', 570, 720),
        ('Power Hour 9:30-10:30', 570, 630),
        ('Mid-Morning 10:00-12:00', 600, 720),
        ('Afternoon 12:00-16:00', 720, 960),
        ('Core 9:30-14:00', 570, 840),
        ('Tight 9:45-15:45', 585, 945),
        ('Extended 8:00-16:00', 480, 960),
        ('Pre+Regular 7:00-16:00', 420, 960),
    ]
    for name, start, end in sessions:
        p = {**base_p, 'session_start': start, 'session_end': end}
        r = backtest(df, p)
        r['label'] = name
        results.append(r)

    # 2B: ATR volatility filter
    print("--- 2B: ATR Volatility Filter ---")
    for min_atr in [0, 1, 2, 3, 5, 7, 10]:
        for max_atr in [50, 100, 200, 500, 9999]:
            if min_atr >= max_atr:
                continue
            p = {**base_p, 'min_atr': min_atr, 'max_atr': max_atr}
            r = backtest(df, p)
            r['label'] = f'ATR {min_atr}-{max_atr}'
            results.append(r)

    # 2C: ADX trend strength filter
    print("--- 2C: ADX Trend Strength ---")
    for adx_min in [0, 15, 20, 25, 30, 35]:
        p = {**base_p, 'adx_min': adx_min}
        r = backtest(df, p)
        r['label'] = f'ADX>{adx_min}'
        results.append(r)

    # 2D: RSI confirmation filter
    print("--- 2D: RSI Confirmation ---")
    for use_rsi in [False, True]:
        p = {**base_p, 'rsi_filter': use_rsi}
        r = backtest(df, p)
        r['label'] = f'RSI {"ON" if use_rsi else "OFF"}'
        results.append(r)

    # 2E: Bar range filter
    print("--- 2E: Bar Range Minimum ---")
    for brm in [0, 0.5, 1, 2, 3, 5]:
        p = {**base_p, 'bar_range_min': brm}
        r = backtest(df, p)
        r['label'] = f'BarMin {brm}pts'
        results.append(r)

    # 2F: Cooldown between trades
    print("--- 2F: Trade Cooldown ---")
    for cd in [0, 1, 2, 3, 5, 10, 15, 30]:
        p = {**base_p, 'cooldown_bars': cd}
        r = backtest(df, p)
        r['label'] = f'Cooldown {cd}bars'
        results.append(r)

    safe = [r for r in results if r['max_dd_pct'] < 30 and r['total_trades'] >= 30]
    safe.sort(key=lambda x: x['profit_factor'], reverse=True)

    print(f"\n{'#':>3} {'Label':>25} {'PnL':>10} {'PF':>6} {'WR':>6} {'Trades':>7} {'DD%':>6}")
    print("-" * 80)
    for i, r in enumerate(safe[:25]):
        print(f"{i+1:>3} {r['label']:>25} ${r['total_pnl']:>8,.0f} {r['profit_factor']:>6.2f} {r['win_rate']:>5.1f}% {r['total_trades']:>7} {r['max_dd_pct']:>5.1f}%")

    return safe[:5]

# =============================================================================
# PHASE 3: PRO TECHNIQUES
# =============================================================================
def phase3_pro(df, best_core):
    print("\n" + "=" * 100)
    print("PHASE 3: PRO TECHNIQUES (trailing, breakeven, session close)")
    print("=" * 100)

    base_p = best_core['params'].copy()
    results = []

    # 3A: Trailing stop
    print("\n--- 3A: Trailing Stop ---")
    for trail in [0.3, 0.5, 0.7, 1.0, 1.2, 1.5, 2.0]:
        p = {**base_p, 'use_trailing': True, 'trail_atr_mult': trail}
        r = backtest(df, p)
        r['label'] = f'Trail {trail}x ATR'
        results.append(r)

    # No trailing (baseline comparison)
    p = {**base_p, 'use_trailing': False}
    r = backtest(df, p)
    r['label'] = 'No Trailing'
    results.append(r)

    # 3B: Breakeven stop
    print("--- 3B: Breakeven Trigger ---")
    for be in [5, 10, 15, 20, 25, 30, 50]:
        p = {**base_p, 'use_breakeven': True, 'be_trigger_dollars': be}
        r = backtest(df, p)
        r['label'] = f'BE @ ${be} profit'
        results.append(r)

    # 3C: Session close (force close at end of day)
    print("--- 3C: Session Close ---")
    for sess_end in [945, 955, 960]:
        p = {**base_p, 'session_end': sess_end}
        r = backtest(df, p)
        r['label'] = f'Close @{sess_end//60}:{sess_end%60:02d}'
        results.append(r)

    # 3D: Combined trailing + breakeven
    print("--- 3D: Trailing + Breakeven Combos ---")
    for trail in [0.5, 0.7, 1.0]:
        for be in [10, 15, 20]:
            p = {**base_p, 'use_trailing': True, 'trail_atr_mult': trail, 'use_breakeven': True, 'be_trigger_dollars': be}
            r = backtest(df, p)
            r['label'] = f'Trail {trail}x + BE${be}'
            results.append(r)

    safe = [r for r in results if r['max_dd_pct'] < 30 and r['total_trades'] >= 30]
    safe.sort(key=lambda x: x['profit_factor'], reverse=True)

    print(f"\n{'#':>3} {'Label':>25} {'PnL':>10} {'PF':>6} {'WR':>6} {'Trades':>7} {'DD%':>6}")
    print("-" * 80)
    for i, r in enumerate(safe[:20]):
        print(f"{i+1:>3} {r['label']:>25} ${r['total_pnl']:>8,.0f} {r['profit_factor']:>6.2f} {r['win_rate']:>5.1f}% {r['total_trades']:>7} {r['max_dd_pct']:>5.1f}%")

    return safe[:5]

# =============================================================================
# PHASE 4: HYBRID — COMBINE BEST OF EVERYTHING
# =============================================================================
def phase4_hybrid(df, baseline, best_core_list, best_filter_list, best_pro_list):
    print("\n" + "=" * 100)
    print("PHASE 4: HYBRID — COMBINING BEST IMPROVEMENTS")
    print("=" * 100)

    # Extract best individual improvements
    # Start from best core, layer on best filters and pro techniques
    results = []

    # Get top core params
    top_cores = [r['params'] for r in best_core_list[:3]]
    # Get best filter settings
    top_filters = [r['params'] for r in best_filter_list[:3]]
    # Get best pro settings
    top_pros = [r['params'] for r in best_pro_list[:3]]

    # Build hybrid combos
    print("\nTesting hybrid combinations...")
    combo_count = 0

    for core_p in top_cores:
        for filt_p in top_filters:
            for pro_p in top_pros:
                # Merge: core base + filter overrides + pro overrides
                hybrid = {**core_p}
                for key in ['session_start', 'session_end', 'min_atr', 'max_atr', 'adx_min', 'rsi_filter', 'bar_range_min', 'cooldown_bars']:
                    if key in filt_p:
                        hybrid[key] = filt_p[key]
                for key in ['use_trailing', 'trail_atr_mult', 'use_breakeven', 'be_trigger_dollars']:
                    if key in pro_p:
                        hybrid[key] = pro_p[key]

                r = backtest(df, hybrid)
                r['label'] = f"Hybrid #{combo_count+1}"
                results.append(r)
                combo_count += 1

    # Also test manual "best trader" combos
    print("Testing expert combos...")
    expert_combos = [
        # Tight scalper: small EMA, tight stops, quick profits
        {'ema_fast': 8, 'ema_slow': 21, 'stoch_k': 5, 'stoch_d': 2, 'stoch_smt': 1,
         'stoch_lo': 20, 'stoch_hi': 80, 'atr_len': 7, 'sl_atr_mult': 1.0, 'tp_rr': 1.2,
         'hard_stop': 20, 'session_start': 570, 'session_end': 720,
         'use_trailing': True, 'trail_atr_mult': 0.5, 'use_breakeven': True, 'be_trigger_dollars': 10},
        # Trend follower: larger EMA, ride the trend
        {'ema_fast': 21, 'ema_slow': 89, 'stoch_k': 14, 'stoch_d': 3, 'stoch_smt': 2,
         'stoch_lo': 25, 'stoch_hi': 75, 'atr_len': 14, 'sl_atr_mult': 1.5, 'tp_rr': 2.0,
         'hard_stop': 30, 'adx_min': 25, 'session_start': 570, 'session_end': 960,
         'use_trailing': True, 'trail_atr_mult': 1.0},
        # Morning scalper with breakeven
        {'ema_fast': 13, 'ema_slow': 34, 'stoch_k': 9, 'stoch_d': 3, 'stoch_smt': 2,
         'stoch_lo': 25, 'stoch_hi': 75, 'atr_len': 9, 'sl_atr_mult': 1.5, 'tp_rr': 1.5,
         'hard_stop': 25, 'session_start': 570, 'session_end': 720,
         'use_breakeven': True, 'be_trigger_dollars': 15, 'cooldown_bars': 3},
        # Ultra-tight stop with fast exit
        {'ema_fast': 10, 'ema_slow': 34, 'stoch_k': 7, 'stoch_d': 2, 'stoch_smt': 1,
         'stoch_lo': 20, 'stoch_hi': 80, 'atr_len': 7, 'sl_atr_mult': 0.75, 'tp_rr': 1.3,
         'hard_stop': 15, 'session_start': 570, 'session_end': 840,
         'use_trailing': True, 'trail_atr_mult': 0.5, 'use_breakeven': True, 'be_trigger_dollars': 8},
        # Fibonacci EMAs with session filter
        {'ema_fast': 13, 'ema_slow': 55, 'stoch_k': 9, 'stoch_d': 3, 'stoch_smt': 2,
         'stoch_lo': 25, 'stoch_hi': 75, 'atr_len': 9, 'sl_atr_mult': 2.0, 'tp_rr': 1.5,
         'hard_stop': 25, 'session_start': 570, 'session_end': 840,
         'cooldown_bars': 2},
    ]

    for i, ep in enumerate(expert_combos):
        r = backtest(df, ep)
        r['label'] = f"Expert #{i+1}"
        results.append(r)

    # Sort all by profit factor (safe only)
    safe = [r for r in results if r['max_dd_pct'] < 30 and r['total_trades'] >= 30]
    safe.sort(key=lambda x: x['profit_factor'], reverse=True)

    print(f"\n{'#':>3} {'Label':>20} {'PnL':>10} {'Ret%':>8} {'PF':>6} {'WR':>6} {'Trades':>7} {'DD$':>8} {'DD%':>6} {'AvgW':>7} {'AvgL':>7} {'Days':>5}")
    print("-" * 115)
    for i, r in enumerate(safe[:20]):
        print(f"{i+1:>3} {r['label']:>20} ${r['total_pnl']:>8,.0f} {r['return_pct']:>7.1f}% {r['profit_factor']:>6.2f} {r['win_rate']:>5.1f}% {r['total_trades']:>7} ${r['max_dd']:>7,.0f} {r['max_dd_pct']:>5.1f}% ${r['avg_win']:>6,.0f} ${r['avg_loss']:>6,.0f} {r['trading_days']:>5}")

    return safe

# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    print("=" * 100)
    print("MNQ $5K FULL OPTIMIZATION — Finding the Best Possible Strategy")
    print(f"Starting Equity: ${STARTING_EQUITY:,.0f} | 1 Contract | Last {MONTHS} Months")
    print("=" * 100)

    df = load_data()
    print(f"Data: {len(df)} bars, {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"Trading days: {df['date'].nunique()}")

    # Phase 1
    best_core, baseline = phase1_core_sweep(df)

    # Phase 2 — use best core
    best_filter = phase2_filters(df, best_core[0])

    # Phase 3 — use best core
    best_pro = phase3_pro(df, best_core[0])

    # Phase 4 — combine everything
    final = phase4_hybrid(df, baseline, best_core, best_filter, best_pro)

    # FINAL COMPARISON
    print("\n" + "=" * 100)
    print("FINAL REPORT: BASELINE vs BEST OPTIMIZED")
    print("=" * 100)

    baseline_params = {
        'ema_fast': 21, 'ema_slow': 55, 'stoch_k': 9, 'stoch_d': 3, 'stoch_smt': 2,
        'stoch_lo': 25, 'stoch_hi': 75, 'atr_len': 9, 'sl_atr_mult': 2.0,
        'tp_rr': 1.5, 'hard_stop': 25
    }
    bl = backtest(df, baseline_params)

    if final:
        best = final[0]
        bp = best['params']

        print(f"\n{'Metric':<25} {'BASELINE':>15} {'OPTIMIZED':>15} {'Change':>12}")
        print("-" * 70)
        print(f"{'Total P&L':<25} ${bl['total_pnl']:>14,.0f} ${best['total_pnl']:>14,.0f} {(best['total_pnl']-bl['total_pnl'])/abs(bl['total_pnl'])*100 if bl['total_pnl'] != 0 else 0:>+10.1f}%")
        print(f"{'Return %':<25} {bl['return_pct']:>14.1f}% {best['return_pct']:>14.1f}%")
        print(f"{'Profit Factor':<25} {bl['profit_factor']:>15.2f} {best['profit_factor']:>15.2f}")
        print(f"{'Win Rate':<25} {bl['win_rate']:>14.1f}% {best['win_rate']:>14.1f}%")
        print(f"{'Total Trades':<25} {bl['total_trades']:>15} {best['total_trades']:>15}")
        print(f"{'Avg Win':<25} ${bl['avg_win']:>14,.0f} ${best['avg_win']:>14,.0f}")
        print(f"{'Avg Loss':<25} ${bl['avg_loss']:>14,.0f} ${best['avg_loss']:>14,.0f}")
        print(f"{'Max Drawdown $':<25} ${bl['max_dd']:>14,.0f} ${best['max_dd']:>14,.0f}")
        print(f"{'Max Drawdown %':<25} {bl['max_dd_pct']:>14.1f}% {best['max_dd_pct']:>14.1f}%")
        print(f"{'Worst Daily P&L':<25} ${bl['worst_daily']:>14,.0f} ${best['worst_daily']:>14,.0f}")
        print(f"{'Final Equity':<25} ${bl['final_equity']:>14,.0f} ${best['final_equity']:>14,.0f}")
        print(f"{'Trading Days':<25} {bl['trading_days']:>15} {best['trading_days']:>15}")

        print(f"\nOPTIMIZED PARAMETERS:")
        for k, v in sorted(bp.items()):
            bv = baseline_params.get(k, '-')
            marker = " <-- CHANGED" if v != bv else ""
            print(f"  {k:>25}: {v}{marker}")

    print("\n" + "=" * 100)
    print("OPTIMIZATION COMPLETE")
    print("=" * 100)
