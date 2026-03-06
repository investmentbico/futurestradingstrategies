#!/usr/bin/env python3
"""
MGC Ultimate Gold Scalper — Multi-Strategy Backtest Engine
============================================================
8 strategy types tested across every viable parameter combination.
Designed by thinking like the best gold scalper in the world.

Strategy Types:
  1. RSI Mean Reversion — buy oversold, sell overbought
  2. Bollinger Band Bounce — trade band touches with confirmation
  3. EMA Pullback — pullbacks to fast EMA in trend direction
  4. Momentum Breakout — trade explosive moves with ATR filter
  5. Stochastic Crossover — %K/%D cross in OB/OS zones
  6. Range Scalp — detect consolidation, scalp the range
  7. VWAP Reversion — revert to session VWAP
  8. Multi-Signal Confluence — combine 2+ signals for high-probability

All strategies enforce:
  - $2K max daily drawdown
  - 30-second minimum hold (unless stop hit)
  - Apex prop firm costs ($3.24 RT/contract + 1 tick slippage)
  - $150K starting equity

Target: 5% per week ($7,500/week)
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# MGC SPECS
# =============================================================================
MGC_PV = 10.0       # $10/point
MGC_TICK = 0.10
APEX_RT = 3.24       # per contract round trip
SLIP = 0.10          # 1 tick per side
START_EQ = 150000.0
MAX_DAILY_DD = 2000.0
MIN_HOLD = 30        # seconds

# =============================================================================
# INDICATORS
# =============================================================================

def ema(prices, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(prices)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def sma(prices, period):
    out = np.full_like(prices, np.nan)
    cs = np.cumsum(prices)
    out[period-1:] = (cs[period-1:] - np.concatenate([[0], cs[:-period]])) / period
    return out

def rsi(closes, period):
    n = len(closes)
    out = np.full(n, 50.0)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    if n < period + 1:
        return out
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            out[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return out

def bollinger_bands(closes, period, num_std):
    mid = sma(closes, period)
    n = len(closes)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    for i in range(period - 1, n):
        std = np.std(closes[i - period + 1:i + 1])
        upper[i] = mid[i] + num_std * std
        lower[i] = mid[i] - num_std * std
    return upper, mid, lower

def stochastic(highs, lows, closes, k_period, d_period):
    n = len(closes)
    k_vals = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = np.max(highs[i - k_period + 1:i + 1])
        ll = np.min(lows[i - k_period + 1:i + 1])
        if hh != ll:
            k_vals[i] = 100.0 * (closes[i] - ll) / (hh - ll)
    d_vals = sma(k_vals, d_period)
    d_vals = np.where(np.isnan(d_vals), 50.0, d_vals)
    return k_vals, d_vals

def atr(highs, lows, closes, period):
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    out = np.full(n, np.nan)
    if n < period:
        return out
    out[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out

def calc_vwap(highs, lows, closes, timestamps):
    """Session VWAP (resets each day). Uses typical price."""
    n = len(closes)
    vwap = np.full(n, np.nan)
    typical = (highs + lows + closes) / 3.0
    cum_tp = 0.0
    cum_vol = 0.0
    current_day = -1
    for i in range(n):
        day = int(timestamps[i]) // 86400
        if day != current_day:
            cum_tp = 0.0
            cum_vol = 0.0
            current_day = day
        cum_vol += 1.0  # proxy volume = 1 per bar
        cum_tp += typical[i]
        vwap[i] = cum_tp / cum_vol
    return vwap

# =============================================================================
# SIGNAL GENERATORS
# =============================================================================

def signals_rsi_reversion(closes, highs, lows, timestamps, rsi_period, rsi_buy, rsi_sell, atr_period):
    """RSI Mean Reversion: buy oversold, sell overbought."""
    r = rsi(closes, rsi_period)
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if r[i] < rsi_buy and r[i-1] >= rsi_buy:  # cross below into oversold
            buy[i] = True
        if r[i] > rsi_sell and r[i-1] <= rsi_sell:  # cross above into overbought
            sell[i] = True
    return buy, sell, a

def signals_bollinger(closes, highs, lows, timestamps, bb_period, bb_std, atr_period):
    """Bollinger Band Bounce: buy at lower band, sell at upper band."""
    upper, mid, lower = bollinger_bands(closes, bb_period, bb_std)
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(lower[i]):
            continue
        # Price touches lower band and bounces
        if lows[i] <= lower[i] and closes[i] > lower[i] and closes[i] > closes[i-1]:
            buy[i] = True
        # Price touches upper band and rejects
        if highs[i] >= upper[i] and closes[i] < upper[i] and closes[i] < closes[i-1]:
            sell[i] = True
    return buy, sell, a

def signals_ema_pullback(closes, highs, lows, timestamps, fast_ema, slow_ema, atr_period):
    """EMA Pullback: pullbacks to fast EMA in direction of slow EMA trend."""
    fast = ema(closes, fast_ema)
    slow = ema(closes, slow_ema)
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(2, n):
        if np.isnan(a[i]):
            continue
        # Uptrend: fast > slow, price pulled back to fast EMA and bouncing
        if fast[i] > slow[i] and lows[i] <= fast[i] * 1.001 and closes[i] > fast[i] and closes[i] > closes[i-1]:
            buy[i] = True
        # Downtrend: fast < slow, price pulled back to fast EMA and rejecting
        if fast[i] < slow[i] and highs[i] >= fast[i] * 0.999 and closes[i] < fast[i] and closes[i] < closes[i-1]:
            sell[i] = True
    return buy, sell, a

def signals_momentum_breakout(closes, highs, lows, timestamps, lookback, atr_mult, atr_period):
    """Momentum Breakout: trade explosive moves beyond ATR channel."""
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(lookback, n):
        if np.isnan(a[i]) or a[i] == 0:
            continue
        move = closes[i] - closes[i - lookback]
        threshold = atr_mult * a[i]
        # Strong upward breakout
        if move > threshold and closes[i] > highs[i-1]:
            buy[i] = True
        # Strong downward breakout
        if move < -threshold and closes[i] < lows[i-1]:
            sell[i] = True
    return buy, sell, a

def signals_stochastic(closes, highs, lows, timestamps, k_per, d_per, ob, os_level, atr_period):
    """Stochastic Crossover in OB/OS zones."""
    k, d = stochastic(highs, lows, closes, k_per, d_per)
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        # %K crosses above %D in oversold zone
        if k[i-1] < d[i-1] and k[i] > d[i] and k[i] < os_level:
            buy[i] = True
        # %K crosses below %D in overbought zone
        if k[i-1] > d[i-1] and k[i] < d[i] and k[i] > ob:
            sell[i] = True
    return buy, sell, a

def signals_range_scalp(closes, highs, lows, timestamps, range_period, atr_period):
    """Range Scalp: detect consolidation, trade the range boundaries."""
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(range_period, n):
        if np.isnan(a[i]) or a[i] == 0:
            continue
        window_high = np.max(highs[i - range_period:i])
        window_low = np.min(lows[i - range_period:i])
        range_size = window_high - window_low
        # Only trade if range is tight (< 2x ATR = consolidation)
        if range_size < 2.0 * a[i]:
            # Buy at bottom of range
            if closes[i] <= window_low + 0.25 * range_size and closes[i] > closes[i-1]:
                buy[i] = True
            # Sell at top of range
            if closes[i] >= window_high - 0.25 * range_size and closes[i] < closes[i-1]:
                sell[i] = True
    return buy, sell, a

def signals_vwap_reversion(closes, highs, lows, timestamps, atr_period, vwap_dist):
    """VWAP Reversion: trade when price deviates from VWAP and reverts."""
    vwap = calc_vwap(highs, lows, closes, timestamps)
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(vwap[i]) or np.isnan(a[i]) or a[i] == 0:
            continue
        dist = (closes[i] - vwap[i]) / a[i]
        prev_dist = (closes[i-1] - vwap[i-1]) / a[i] if not np.isnan(vwap[i-1]) else 0
        # Price far below VWAP and starting to revert up
        if dist < -vwap_dist and closes[i] > closes[i-1]:
            buy[i] = True
        # Price far above VWAP and starting to revert down
        if dist > vwap_dist and closes[i] < closes[i-1]:
            sell[i] = True
    return buy, sell, a

def signals_confluence(closes, highs, lows, timestamps, rsi_period, bb_period, fast_ema, slow_ema, atr_period):
    """Multi-signal confluence: need 2+ signals agreeing."""
    r = rsi(closes, rsi_period)
    upper, mid, lower = bollinger_bands(closes, bb_period, 2.0)
    f_ema = ema(closes, fast_ema)
    s_ema = ema(closes, slow_ema)
    a = atr(highs, lows, closes, atr_period)
    n = len(closes)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(a[i]) or np.isnan(lower[i]):
            continue
        buy_score = 0
        sell_score = 0
        # RSI
        if r[i] < 35:
            buy_score += 1
        if r[i] > 65:
            sell_score += 1
        # Bollinger
        if closes[i] <= lower[i] * 1.002:
            buy_score += 1
        if closes[i] >= upper[i] * 0.998:
            sell_score += 1
        # EMA trend
        if f_ema[i] > s_ema[i] and closes[i] > f_ema[i]:
            buy_score += 1
        if f_ema[i] < s_ema[i] and closes[i] < f_ema[i]:
            sell_score += 1
        # Candle direction
        if closes[i] > closes[i-1]:
            buy_score += 1
        if closes[i] < closes[i-1]:
            sell_score += 1

        if buy_score >= 3:
            buy[i] = True
        if sell_score >= 3:
            sell[i] = True
    return buy, sell, a


# =============================================================================
# STRATEGY CONFIGS — every strategy type with multiple parameter sets
# =============================================================================

def build_strategy_configs():
    """Build all strategy configurations to test."""
    configs = []

    # RSI Mean Reversion
    for rsi_p in [5, 7, 9, 14]:
        for buy_lvl, sell_lvl in [(20, 80), (25, 75), (30, 70), (15, 85)]:
            for atr_p in [5, 10, 14]:
                configs.append(('RSI_Reversion', {
                    'rsi_period': rsi_p, 'rsi_buy': buy_lvl, 'rsi_sell': sell_lvl, 'atr_period': atr_p
                }))

    # Bollinger Band Bounce
    for bb_p in [10, 15, 20]:
        for bb_s in [1.5, 2.0, 2.5]:
            for atr_p in [5, 10, 14]:
                configs.append(('Bollinger', {
                    'bb_period': bb_p, 'bb_std': bb_s, 'atr_period': atr_p
                }))

    # EMA Pullback
    for fast, slow in [(5, 13), (8, 21), (13, 34), (5, 21), (8, 34)]:
        for atr_p in [5, 10, 14]:
            configs.append(('EMA_Pullback', {
                'fast_ema': fast, 'slow_ema': slow, 'atr_period': atr_p
            }))

    # Momentum Breakout
    for lookback in [3, 5, 8, 13]:
        for atr_mult in [1.0, 1.5, 2.0, 2.5]:
            for atr_p in [5, 10, 14]:
                configs.append(('Momentum', {
                    'lookback': lookback, 'atr_mult': atr_mult, 'atr_period': atr_p
                }))

    # Stochastic Crossover
    for k_p in [5, 9, 14]:
        for d_p in [3, 5]:
            for ob, os_l in [(80, 20), (75, 25), (70, 30)]:
                for atr_p in [5, 10, 14]:
                    configs.append(('Stochastic', {
                        'k_per': k_p, 'd_per': d_p, 'ob': ob, 'os_level': os_l, 'atr_period': atr_p
                    }))

    # Range Scalp
    for rp in [10, 15, 20, 30]:
        for atr_p in [5, 10, 14]:
            configs.append(('Range_Scalp', {
                'range_period': rp, 'atr_period': atr_p
            }))

    # VWAP Reversion
    for vd in [1.0, 1.5, 2.0, 2.5, 3.0]:
        for atr_p in [5, 10, 14]:
            configs.append(('VWAP_Reversion', {
                'vwap_dist': vd, 'atr_period': atr_p
            }))

    # Confluence
    for rsi_p in [7, 14]:
        for bb_p in [15, 20]:
            for fast, slow in [(5, 21), (8, 34)]:
                for atr_p in [5, 10, 14]:
                    configs.append(('Confluence', {
                        'rsi_period': rsi_p, 'bb_period': bb_p,
                        'fast_ema': fast, 'slow_ema': slow, 'atr_period': atr_p
                    }))

    return configs


# =============================================================================
# EXIT STRATEGIES — focused on what works for scalping
# =============================================================================

EXIT_CONFIGS = [
    # (name, sl_dollar, tp_dollar, trail_atr_mult)
    # Tight scalp exits
    ('SL30_TP60',        30,  60,  0),
    ('SL40_TP80',        40,  80,  0),
    ('SL50_TP100',       50,  100, 0),
    ('SL50_TP150',       50,  150, 0),
    ('SL75_TP150',       75,  150, 0),
    ('SL100_TP200',      100, 200, 0),
    ('SL100_TP300',      100, 300, 0),
    # With trailing
    ('SL50_Trail1x',     50,  0,   1.0),
    ('SL75_Trail1.5x',   75,  0,   1.5),
    ('SL100_Trail2x',    100, 0,   2.0),
    # Tight + trailing
    ('SL50_TP150_Tr1x',  50,  150, 1.0),
    ('SL75_TP200_Tr1.5', 75,  200, 1.5),
]


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

def run_backtest(closes, highs, lows, opens, timestamps, buy_sig, sell_sig, atr_vals,
                 contracts, sl_dollar, tp_dollar, trail_atr_mult):
    n = len(closes)
    position = 0
    entry_price = 0.0
    entry_time = 0
    qty = contracts
    sl_price = 0.0
    tp_price = 0.0
    trail_price = 0.0
    best_price = 0.0

    equity = START_EQ
    peak_eq = START_EQ
    max_dd = 0.0
    daily_pnl = 0.0
    max_daily_dd = 0.0
    current_day = -1

    trades = []
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    total_costs = 0.0

    def do_close(exit_px, exit_ts, reason):
        nonlocal position, equity, daily_pnl, peak_eq, max_dd, max_daily_dd
        nonlocal wins, losses, gross_profit, gross_loss, total_costs
        if position > 0:
            pnl = (exit_px - entry_price) * qty * MGC_PV
        else:
            pnl = (entry_price - exit_px) * qty * MGC_PV
        cost = APEX_RT * qty
        pnl -= cost
        total_costs += cost
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += abs(pnl)
        equity += pnl
        daily_pnl += pnl
        if equity > peak_eq:
            peak_eq = equity
        dd = peak_eq - equity
        if dd > max_dd:
            max_dd = dd
        trades.append({
            'entry_time': entry_time, 'exit_time': exit_ts,
            'entry_price': entry_price, 'exit_price': exit_px,
            'dir': 'L' if position > 0 else 'S',
            'pnl': pnl, 'reason': reason,
            'hold': exit_ts - entry_time,
        })
        position = 0

    def do_enter(direction, price, ts):
        nonlocal position, entry_price, entry_time, sl_price, tp_price, trail_price, best_price
        entry_price = price + SLIP if direction > 0 else price - SLIP
        entry_time = ts
        position = direction
        best_price = entry_price

        if sl_dollar > 0:
            sl_pts = sl_dollar / MGC_PV
            sl_price = entry_price - sl_pts if direction > 0 else entry_price + sl_pts
        else:
            sl_price = 0

        if tp_dollar > 0:
            tp_pts = tp_dollar / MGC_PV
            tp_price = entry_price + tp_pts if direction > 0 else entry_price - tp_pts
        else:
            tp_price = 0

        if trail_atr_mult > 0 and not np.isnan(atr_vals[min(ts_idx, n-1)]):
            trail_dist = trail_atr_mult * atr_vals[min(ts_idx, n-1)]
            trail_price = entry_price - trail_dist if direction > 0 else entry_price + trail_dist
        else:
            trail_price = 0

    for ts_idx in range(max(20, 1), n):
        ts = int(timestamps[ts_idx])
        day = ts // 86400

        if day != current_day:
            if current_day >= 0 and daily_pnl < 0:
                max_daily_dd = min(max_daily_dd, daily_pnl)
            daily_pnl = 0.0
            current_day = day

        # Force close on daily limit
        if daily_pnl <= -MAX_DAILY_DD and position != 0:
            ex = closes[ts_idx] - SLIP if position > 0 else closes[ts_idx] + SLIP
            do_close(ex, ts, 'daily_lim')
            continue
        if daily_pnl <= -MAX_DAILY_DD:
            continue

        if position != 0:
            hold = ts - entry_time
            can_exit = hold >= MIN_HOLD

            # Update trailing
            if position > 0 and highs[ts_idx] > best_price:
                best_price = highs[ts_idx]
                if trail_atr_mult > 0 and not np.isnan(atr_vals[ts_idx]):
                    trail_price = best_price - trail_atr_mult * atr_vals[ts_idx]
            elif position < 0 and lows[ts_idx] < best_price:
                best_price = lows[ts_idx]
                if trail_atr_mult > 0 and not np.isnan(atr_vals[ts_idx]):
                    trail_price = best_price + trail_atr_mult * atr_vals[ts_idx]

            exited = False

            # Stop loss (always fires)
            if sl_price > 0:
                if position > 0 and lows[ts_idx] <= sl_price:
                    do_close(sl_price - SLIP, ts, 'sl')
                    exited = True
                elif position < 0 and highs[ts_idx] >= sl_price:
                    do_close(sl_price + SLIP, ts, 'sl')
                    exited = True

            # Trailing stop (always fires)
            if not exited and trail_price > 0:
                if position > 0 and lows[ts_idx] <= trail_price:
                    do_close(trail_price - SLIP, ts, 'trail')
                    exited = True
                elif position < 0 and highs[ts_idx] >= trail_price:
                    do_close(trail_price + SLIP, ts, 'trail')
                    exited = True

            # Take profit (respects min hold)
            if not exited and tp_price > 0 and can_exit:
                if position > 0 and highs[ts_idx] >= tp_price:
                    do_close(tp_price - SLIP, ts, 'tp')
                    exited = True
                elif position < 0 and lows[ts_idx] <= tp_price:
                    do_close(tp_price + SLIP, ts, 'tp')
                    exited = True

            # Opposite signal (respects min hold)
            if not exited and can_exit:
                if position > 0 and sell_sig[ts_idx]:
                    do_close(closes[ts_idx] - SLIP, ts, 'sig')
                    exited = True
                elif position < 0 and buy_sig[ts_idx]:
                    do_close(closes[ts_idx] + SLIP, ts, 'sig')
                    exited = True

            if exited:
                # Check for reversal entry on same bar
                if position == 0 and daily_pnl > -MAX_DAILY_DD:
                    if sell_sig[ts_idx] and trades[-1]['reason'] == 'sig':
                        do_enter(-1, closes[ts_idx], ts)
                    elif buy_sig[ts_idx] and trades[-1]['reason'] == 'sig':
                        do_enter(1, closes[ts_idx], ts)
                continue

        # New entry
        if position == 0:
            if buy_sig[ts_idx]:
                do_enter(1, closes[ts_idx], ts)
            elif sell_sig[ts_idx]:
                do_enter(-1, closes[ts_idx], ts)

    # Close open position
    if position != 0:
        ex = closes[-1] - SLIP if position > 0 else closes[-1] + SLIP
        do_close(ex, int(timestamps[-1]), 'eod')

    if daily_pnl < 0:
        max_daily_dd = min(max_daily_dd, daily_pnl)

    total = wins + losses
    if total < 3:
        return None

    return {
        'trades': total, 'wins': wins, 'losses': losses,
        'wr': 100.0 * wins / total,
        'pnl': gross_profit - gross_loss - total_costs + (total_costs),  # already subtracted in do_close
        'pnl_val': equity - START_EQ,
        'gp': gross_profit, 'gl': gross_loss,
        'pf': gross_profit / gross_loss if gross_loss > 0 else (999 if gross_profit > 0 else 0),
        'avg_w': gross_profit / wins if wins else 0,
        'avg_l': gross_loss / losses if losses else 0,
        'max_dd': max_dd, 'max_ddd': abs(max_daily_dd),
        'costs': total_costs,
        'final_eq': equity,
        'trade_list': trades,
    }


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 90)
    print("  MGC ULTIMATE GOLD SCALPER — 8 STRATEGY TYPES × FULL PARAMETER SWEEP")
    print("  Target: 5% per week ($7,500/week on $150K)")
    print("=" * 90)
    print(f"  MGC: ${MGC_PV}/pt | Apex RT: ${APEX_RT} | Slippage: {SLIP}/side")
    print(f"  Start: ${START_EQ:,.0f} | Max Daily DD: ${MAX_DAILY_DD:,.0f} | Min Hold: {MIN_HOLD}s")
    print()

    # Load data
    tfs = ['1min', '2min', '3min', '5min']
    data = {}
    for tf in tfs:
        fp = f"data/mgc_{tf}.csv"
        if os.path.exists(fp):
            df = pd.read_csv(fp)
            if len(df) > 50:
                data[tf] = df
                s = pd.to_datetime(df['time'].iloc[0], unit='s')
                e = pd.to_datetime(df['time'].iloc[-1], unit='s')
                print(f"  {tf}: {len(df)} bars ({s.date()} to {e.date()})")

    if not data:
        print("  No data. Run mgc_data_downloader.py first.")
        sys.exit(1)

    strategy_configs = build_strategy_configs()
    print(f"\n  Strategy configs: {len(strategy_configs)}")
    print(f"  Exit configs: {len(EXIT_CONFIGS)}")
    print(f"  Contract sizes: {[1, 3, 5]}")
    print(f"  Timeframes: {list(data.keys())}")

    total_combos = len(strategy_configs) * len(EXIT_CONFIGS) * 3 * len(data)
    print(f"  Total combinations: {total_combos}")
    print()

    all_results = []
    tested = 0

    for tf_name, df in data.items():
        closes = df['close'].values.astype(float)
        highs = df['high'].values.astype(float)
        lows = df['low'].values.astype(float)
        opens = df['open'].values.astype(float)
        timestamps = df['time'].values.astype(np.int64)

        for strat_name, params in strategy_configs:
            # Generate signals based on strategy type
            try:
                if strat_name == 'RSI_Reversion':
                    buy, sell, a = signals_rsi_reversion(closes, highs, lows, timestamps, **params)
                elif strat_name == 'Bollinger':
                    buy, sell, a = signals_bollinger(closes, highs, lows, timestamps, **params)
                elif strat_name == 'EMA_Pullback':
                    buy, sell, a = signals_ema_pullback(closes, highs, lows, timestamps, **params)
                elif strat_name == 'Momentum':
                    buy, sell, a = signals_momentum_breakout(closes, highs, lows, timestamps, **params)
                elif strat_name == 'Stochastic':
                    buy, sell, a = signals_stochastic(closes, highs, lows, timestamps, **params)
                elif strat_name == 'Range_Scalp':
                    buy, sell, a = signals_range_scalp(closes, highs, lows, timestamps, **params)
                elif strat_name == 'VWAP_Reversion':
                    buy, sell, a = signals_vwap_reversion(closes, highs, lows, timestamps, **params)
                elif strat_name == 'Confluence':
                    buy, sell, a = signals_confluence(closes, highs, lows, timestamps, **params)
                else:
                    continue
            except Exception:
                continue

            sig_count = np.sum(buy) + np.sum(sell)
            if sig_count < 3:
                tested += len(EXIT_CONFIGS) * 3
                continue

            for exit_name, sl_d, tp_d, trail_m in EXIT_CONFIGS:
                for cts in [1, 3, 5]:
                    tested += 1
                    result = run_backtest(closes, highs, lows, opens, timestamps,
                                          buy, sell, a, cts, sl_d, tp_d, trail_m)
                    if result is not None:
                        param_str = '|'.join(f"{k}={v}" for k, v in params.items() if k != 'atr_period')
                        result['tf'] = tf_name
                        result['strat'] = strat_name
                        result['params'] = param_str
                        result['exit'] = exit_name
                        result['cts'] = cts
                        result['atr_p'] = params.get('atr_period', 0)
                        result['label'] = f"{tf_name}|{strat_name}|{param_str}|{exit_name}|{cts}ct"
                        all_results.append(result)

            if tested % 5000 == 0 or tested >= total_combos:
                pct = 100.0 * tested / total_combos
                print(f"  [{pct:5.1f}%] {tested}/{total_combos} | {len(all_results)} valid")

    print(f"\n  Completed: {tested} tested, {len(all_results)} valid results")

    # =========================================================================
    # ANALYSIS
    # =========================================================================
    # Filter by daily DD
    valid = [r for r in all_results if r['max_ddd'] <= MAX_DAILY_DD + 50]
    profitable = [r for r in valid if r['pnl_val'] > 0]
    print(f"  Within DD limit: {len(valid)} | Profitable: {len(profitable)}")

    if not profitable:
        print("  No profitable strategies within DD limit. Showing best overall.")
        profitable = sorted(all_results, key=lambda x: x['pnl_val'], reverse=True)[:20]

    # Weekly return calculation
    # Data spans ~1 month, so ~4 weeks
    for r in profitable:
        weeks = max(1, (r['trade_list'][-1]['exit_time'] - r['trade_list'][0]['entry_time']) / (7 * 86400))
        r['weekly_ret'] = (r['pnl_val'] / START_EQ * 100) / weeks
        r['weekly_pnl'] = r['pnl_val'] / weeks

    def show_top(title, results, n=3):
        print(f"\n{'=' * 90}")
        print(f"  {title}")
        print(f"{'=' * 90}")
        for rank, r in enumerate(results[:n], 1):
            weekly = r.get('weekly_ret', 0)
            weekly_pnl = r.get('weekly_pnl', 0)
            print(f"\n  #{rank}: {r['label']}")
            print(f"  {'─' * 80}")
            print(f"  Trades: {r['trades']:>5}  |  Win Rate: {r['wr']:>5.1f}%  |  PF: {r['pf']:>5.2f}")
            print(f"  Total P&L:   ${r['pnl_val']:>10,.2f}  |  Weekly P&L: ${weekly_pnl:>8,.2f}  ({weekly:>+.2f}%/wk)")
            print(f"  Avg Win:     ${r['avg_w']:>10,.2f}  |  Avg Loss:  ${r['avg_l']:>10,.2f}  |  R:R {r['avg_w']/max(r['avg_l'],1):.2f}")
            print(f"  Max DD:      ${r['max_dd']:>10,.2f}  |  Daily DD:  ${r['max_ddd']:>10,.2f}")
            print(f"  Costs:       ${r['costs']:>10,.2f}  |  Final Eq:  ${r['final_eq']:>12,.2f}")
            # Exit breakdown
            exits = {}
            for t in r['trade_list']:
                exits[t['reason']] = exits.get(t['reason'], 0) + 1
            print(f"  Exits: {' | '.join(f'{k}:{v}' for k,v in sorted(exits.items(), key=lambda x:-x[1]))}")

    # Top by P&L
    top_pnl = sorted(profitable, key=lambda x: x['pnl_val'], reverse=True)
    show_top("TOP 3 BY TOTAL P&L", top_pnl)

    # Top by PF (min 15 trades)
    pf_set = [r for r in profitable if r['trades'] >= 15]
    if len(pf_set) < 3:
        pf_set = profitable
    top_pf = sorted(pf_set, key=lambda x: x['pf'], reverse=True)
    show_top("TOP 3 BY PROFIT FACTOR (min 15 trades)", top_pf)

    # Top by weekly return
    top_weekly = sorted(profitable, key=lambda x: x.get('weekly_ret', 0), reverse=True)
    show_top("TOP 3 BY WEEKLY RETURN %", top_weekly)

    # Top by win rate (min 20 trades)
    wr_set = [r for r in profitable if r['trades'] >= 20]
    if len(wr_set) < 3:
        wr_set = profitable
    top_wr = sorted(wr_set, key=lambda x: x['wr'], reverse=True)
    show_top("TOP 3 BY WIN RATE (min 20 trades)", top_wr)

    # Lowest drawdown (profitable)
    top_dd = sorted(profitable, key=lambda x: x['max_dd'])
    show_top("TOP 3 LOWEST DRAWDOWN", top_dd)

    # Best composite
    for r in profitable:
        dd_pen = max(0.1, 1.0 - r['max_dd'] / 5000)
        r['score'] = r['pf'] * np.sqrt(r['trades']) * dd_pen * (r['wr'] / 50.0)
    top_score = sorted(profitable, key=lambda x: x['score'], reverse=True)
    show_top("TOP 3 BEST OVERALL (COMPOSITE)", top_score)

    # =========================================================================
    # Strategy type breakdown
    # =========================================================================
    print(f"\n{'=' * 90}")
    print(f"  BEST STRATEGY PER TYPE")
    print(f"{'=' * 90}")
    strat_types = set(r['strat'] for r in profitable)
    for st in sorted(strat_types):
        subset = [r for r in profitable if r['strat'] == st]
        best = sorted(subset, key=lambda x: x['pnl_val'], reverse=True)[0]
        weekly = best.get('weekly_ret', 0)
        print(f"\n  {st}")
        print(f"    Best: {best['label']}")
        print(f"    P&L: ${best['pnl_val']:,.2f} | PF: {best['pf']:.2f} | WR: {best['wr']:.1f}% | "
              f"Trades: {best['trades']} | DD: ${best['max_dd']:,.0f} | {weekly:+.2f}%/wk")

    # =========================================================================
    # FULL TABLE
    # =========================================================================
    print(f"\n{'=' * 90}")
    print(f"  TOP 40 PROFITABLE STRATEGIES (sorted by P&L)")
    print(f"{'=' * 90}")
    print(f"  {'TF':<5} {'Strategy':<15} {'Exit':<18} {'Ct':>2} {'Trd':>4} {'WR%':>5} "
          f"{'P&L':>9} {'PF':>5} {'MaxDD':>7} {'Wk%':>6}")
    print(f"  {'─' * 88}")
    for r in top_pnl[:40]:
        weekly = r.get('weekly_ret', 0)
        print(f"  {r['tf']:<5} {r['strat']:<15} {r['exit']:<18} {r['cts']:>2} "
              f"{r['trades']:>4} {r['wr']:>4.1f}% ${r['pnl_val']:>7,.0f} "
              f"{r['pf']:>4.2f} ${r['max_dd']:>5,.0f} {weekly:>+5.2f}%")

    # =========================================================================
    # SAVE
    # =========================================================================
    os.makedirs('results', exist_ok=True)
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')

    rows = []
    for r in sorted(all_results, key=lambda x: x['pnl_val'], reverse=True):
        rows.append({
            'Timeframe': r['tf'], 'Strategy': r['strat'], 'Params': r['params'],
            'Exit': r['exit'], 'Contracts': r['cts'], 'ATR_Period': r['atr_p'],
            'Trades': r['trades'], 'Wins': r['wins'], 'Losses': r['losses'],
            'Win_Rate': round(r['wr'], 2), 'PnL': round(r['pnl_val'], 2),
            'Profit_Factor': round(r['pf'], 2),
            'Avg_Win': round(r['avg_w'], 2), 'Avg_Loss': round(r['avg_l'], 2),
            'Max_DD': round(r['max_dd'], 2), 'Max_Daily_DD': round(r['max_ddd'], 2),
            'Costs': round(r['costs'], 2), 'Final_Equity': round(r['final_eq'], 2),
        })
    pd.DataFrame(rows).to_csv(f'results/mgc_ultimate_{ts_str}.csv', index=False)

    # Save trade logs for top 5
    for rank, r in enumerate(top_pnl[:5], 1):
        pd.DataFrame(r['trade_list']).to_csv(f'results/mgc_ultimate_top{rank}_trades_{ts_str}.csv', index=False)

    print(f"\n  Results saved: results/mgc_ultimate_{ts_str}.csv")
    print(f"  Trade logs saved for top 5 strategies")
    print(f"\n  Total: {len(all_results)} valid | {len(profitable)} profitable | {tested} tested")


if __name__ == "__main__":
    main()
