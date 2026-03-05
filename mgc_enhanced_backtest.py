#!/usr/bin/env python3
"""
MGC Diamante Strategy — Enhanced Comprehensive Backtest
=========================================================
Tests every combination of exit strategies, stop losses, take profits,
trailing stops, and parameters to find the optimal MGC trading setup.

Exit Strategy Presets:
  1. Opposite signal only (baseline)
  2-3. Fixed dollar SL + opposite signal
  4-5. ATR-based SL + opposite signal
  6-7. Fixed SL + Fixed TP
  8-9. ATR SL + ATR TP (risk-reward based)
  10-11. Trailing ATR stop + opposite signal
  12-13. Fixed SL + Trailing stop
  14. ATR SL + Trailing + opposite signal (hybrid)
  15. Breakeven after profit threshold + opposite signal

All trades enforce 30-second minimum hold time (unless stop hit).

Usage:
  python mgc_enhanced_backtest.py
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# MGC CONTRACT SPECS & APEX COSTS
# =============================================================================

MGC_POINT_VALUE = 10.0
MGC_TICK_SIZE   = 0.10
MGC_TICK_VALUE  = 1.00

APEX_COST_PER_SIDE = 1.62   # $1.62/side (exchange + clearing + NFA + broker)
APEX_COST_RT       = 3.24   # $3.24 round trip per contract
SPREAD_SLIPPAGE    = 0.10   # 1 tick slippage per side

STARTING_EQUITY    = 150000.0
MAX_DAILY_DRAWDOWN = 2000.0
MIN_HOLD_SECONDS   = 30     # Minimum 30 seconds hold unless stop hit

# =============================================================================
# PARAMETER GRID
# =============================================================================

TIMEFRAMES    = ['1min', '2min', '3min', '5min']
KEY_VALUES    = [1, 2, 3, 4]
ATR_PERIODS   = [5, 10, 14, 20]
CONTRACT_SIZES = [1, 3, 5]
CANDLE_TYPES  = ['Normal', 'HA']

# Exit strategy presets: (name, sl_type, sl_val, tp_type, tp_val, trail_type, trail_val, breakeven_val)
# Types: 'none', 'fixed' (dollar per contract), 'atr' (multiplier), 'rr' (risk-reward ratio)
EXIT_PRESETS = [
    # Name                        SL_type  SL_val  TP_type  TP_val  Trail_type Trail_val BE_val
    ('OppositeOnly',              'none',  0,      'none',  0,      'none',    0,        0),
    ('FixedSL50',                 'fixed', 50,     'none',  0,      'none',    0,        0),
    ('FixedSL100',                'fixed', 100,    'none',  0,      'none',    0,        0),
    ('FixedSL200',                'fixed', 200,    'none',  0,      'none',    0,        0),
    ('ATR_SL_1.5x',               'atr',   1.5,    'none',  0,      'none',    0,        0),
    ('ATR_SL_2x',                 'atr',   2.0,    'none',  0,      'none',    0,        0),
    ('FixedSL100_TP150',          'fixed', 100,    'fixed', 150,    'none',    0,        0),
    ('FixedSL100_TP200',          'fixed', 100,    'fixed', 200,    'none',    0,        0),
    ('FixedSL150_TP300',          'fixed', 150,    'fixed', 300,    'none',    0,        0),
    ('ATR1.5_RR1.5',              'atr',   1.5,    'rr',    1.5,    'none',    0,        0),
    ('ATR1.5_RR2.0',              'atr',   1.5,    'rr',    2.0,    'none',    0,        0),
    ('ATR2x_RR2.0',               'atr',   2.0,    'rr',    2.0,    'none',    0,        0),
    ('ATR2x_RR2.5',               'atr',   2.0,    'rr',    2.5,    'none',    0,        0),
    ('Trail_ATR1.5',              'none',  0,      'none',  0,      'atr',     1.5,      0),
    ('Trail_ATR2x',               'none',  0,      'none',  0,      'atr',     2.0,      0),
    ('Trail_ATR3x',               'none',  0,      'none',  0,      'atr',     3.0,      0),
    ('SL100_Trail1.5',            'fixed', 100,    'none',  0,      'atr',     1.5,      0),
    ('SL150_Trail2x',             'fixed', 150,    'none',  0,      'atr',     2.0,      0),
    ('SL200_Trail2x',             'fixed', 200,    'none',  0,      'atr',     2.0,      0),
    ('ATR1.5_Trail2x',            'atr',   1.5,    'none',  0,      'atr',     2.0,      0),
    ('ATR2x_Trail3x',             'atr',   2.0,    'none',  0,      'atr',     3.0,      0),
    ('Breakeven50',               'none',  0,      'none',  0,      'none',    0,        50),
    ('SL100_BE50',                'fixed', 100,    'none',  0,      'none',    0,        50),
    ('SL100_TP200_Trail1.5',      'fixed', 100,    'fixed', 200,    'atr',     1.5,      0),
    ('ATR1.5_RR2_Trail2x',        'atr',   1.5,    'rr',    2.0,    'atr',     2.0,      0),
]

# =============================================================================
# INDICATORS
# =============================================================================

def calc_atr(high, low, close, period):
    n = len(close)
    tr = np.empty(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i-1]), abs(low[i] - close[i-1]))

    atr = np.full(n, np.nan)
    if n < period:
        return atr
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


def to_heikin_ashi(opens, highs, lows, closes):
    n = len(closes)
    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.empty(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
    ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))
    return ha_open, ha_high, ha_low, ha_close


def calc_atr_trailing_stop(src, atr, key_value):
    n = len(src)
    trail = np.zeros(n)
    start = 0
    for i in range(n):
        if not np.isnan(atr[i]):
            start = i
            trail[i] = src[i] - key_value * atr[i]
            break
    for i in range(start + 1, n):
        if np.isnan(atr[i]):
            trail[i] = trail[i-1]
            continue
        nLoss = key_value * atr[i]
        prev = trail[i-1]
        if src[i] > prev and src[i-1] > prev:
            trail[i] = max(prev, src[i] - nLoss)
        elif src[i] < prev and src[i-1] < prev:
            trail[i] = min(prev, src[i] + nLoss)
        elif src[i] > prev:
            trail[i] = src[i] - nLoss
        else:
            trail[i] = src[i] + nLoss
    return trail


def generate_signals(src, trail):
    n = len(src)
    buy_signal = np.zeros(n, dtype=bool)
    sell_signal = np.zeros(n, dtype=bool)
    for i in range(1, n):
        above = (src[i] > trail[i]) and (src[i-1] <= trail[i-1])
        below = (src[i] < trail[i]) and (src[i-1] >= trail[i-1])
        buy_signal[i] = (src[i] > trail[i]) and above
        sell_signal[i] = (src[i] < trail[i]) and below
    return buy_signal, sell_signal


# =============================================================================
# BACKTEST ENGINE — ENHANCED WITH ALL EXIT STRATEGIES
# =============================================================================

def run_backtest(df, key_value, atr_period, use_ha, contracts, exit_preset):
    """
    Enhanced backtest with configurable exit strategies.

    exit_preset: (name, sl_type, sl_val, tp_type, tp_val, trail_type, trail_val, be_val)
    """
    preset_name, sl_type, sl_val, tp_type, tp_val, trail_type, trail_val, be_val = exit_preset

    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    opens = df['open'].values.astype(float)
    timestamps = df['time'].values.astype(np.int64)

    n = len(closes)
    if n < atr_period + 5:
        return None

    # Price source
    if use_ha:
        _, _, _, src = to_heikin_ashi(opens, highs, lows, closes)
    else:
        src = closes.copy()

    # Indicators
    atr = calc_atr(highs, lows, closes, atr_period)
    trail = calc_atr_trailing_stop(src, atr, key_value)
    buy_signal, sell_signal = generate_signals(src, trail)

    # State
    position = 0  # +1 long, -1 short, 0 flat
    entry_price = 0.0
    entry_time = 0
    qty = 0
    stop_loss_price = 0.0
    take_profit_price = 0.0
    trailing_stop_price = 0.0
    best_price = 0.0          # Best favorable price since entry (for trailing)
    breakeven_active = False

    equity = STARTING_EQUITY
    peak_equity = STARTING_EQUITY
    max_dd = 0.0
    daily_pnl = 0.0
    max_daily_dd = 0.0
    current_day = -1

    trades = []
    warmup = atr_period + 2

    def calc_stop_loss(direction, price, atr_val):
        """Calculate stop loss price based on preset."""
        if sl_type == 'none':
            return 0.0
        elif sl_type == 'fixed':
            dollar_risk = sl_val  # per contract
            point_risk = dollar_risk / MGC_POINT_VALUE
            if direction > 0:
                return price - point_risk
            else:
                return price + point_risk
        elif sl_type == 'atr':
            if np.isnan(atr_val):
                return 0.0
            if direction > 0:
                return price - sl_val * atr_val
            else:
                return price + sl_val * atr_val
        return 0.0

    def calc_take_profit(direction, price, sl_price, atr_val):
        """Calculate take profit price based on preset."""
        if tp_type == 'none':
            return 0.0
        elif tp_type == 'fixed':
            point_profit = tp_val / MGC_POINT_VALUE
            if direction > 0:
                return price + point_profit
            else:
                return price - point_profit
        elif tp_type == 'rr':
            if sl_price == 0.0:
                # Use ATR as fallback risk reference
                if not np.isnan(atr_val):
                    risk = sl_val * atr_val if sl_type == 'atr' else atr_val
                else:
                    return 0.0
            else:
                risk = abs(price - sl_price)
            reward = risk * tp_val
            if direction > 0:
                return price + reward
            else:
                return price - reward
        return 0.0

    def calc_trailing_stop(direction, best_px, atr_val):
        """Calculate trailing stop price."""
        if trail_type == 'none':
            return 0.0
        if trail_type == 'atr':
            if np.isnan(atr_val):
                return 0.0
            if direction > 0:
                return best_px - trail_val * atr_val
            else:
                return best_px + trail_val * atr_val
        return 0.0

    def close_trade(exit_px, exit_ts, reason):
        nonlocal position, equity, daily_pnl, peak_equity, max_dd
        if position > 0:
            pnl = (exit_px - entry_price) * qty * MGC_POINT_VALUE
        else:
            pnl = (entry_price - exit_px) * qty * MGC_POINT_VALUE
        costs = APEX_COST_RT * qty
        pnl -= costs
        trades.append({
            'entry_time': entry_time, 'exit_time': exit_ts,
            'entry_price': entry_price, 'exit_price': exit_px,
            'direction': 'Long' if position > 0 else 'Short',
            'contracts': qty, 'pnl': pnl, 'costs': costs,
            'exit_reason': reason,
            'hold_seconds': exit_ts - entry_time,
        })
        equity += pnl
        daily_pnl += pnl
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        position = 0
        return pnl

    def enter_trade(direction, price, ts, idx, atr_val):
        nonlocal position, entry_price, entry_time, qty
        nonlocal stop_loss_price, take_profit_price, trailing_stop_price
        nonlocal best_price, breakeven_active

        entry_price = price + SPREAD_SLIPPAGE if direction > 0 else price - SPREAD_SLIPPAGE
        entry_time = ts
        qty = contracts
        position = direction

        stop_loss_price = calc_stop_loss(direction, entry_price, atr_val)
        take_profit_price = calc_take_profit(direction, entry_price, stop_loss_price, atr_val)

        best_price = entry_price
        trailing_stop_price = calc_trailing_stop(direction, best_price, atr_val)
        breakeven_active = False

    for i in range(warmup, n):
        ts = int(timestamps[i])
        day = ts // 86400

        # Daily reset
        if day != current_day:
            if current_day >= 0 and daily_pnl < 0:
                max_daily_dd = min(max_daily_dd, daily_pnl)
            daily_pnl = 0.0
            current_day = day

        # Force close on daily limit
        if daily_pnl <= -MAX_DAILY_DRAWDOWN and position != 0:
            exit_px = closes[i] - SPREAD_SLIPPAGE if position > 0 else closes[i] + SPREAD_SLIPPAGE
            close_trade(exit_px, ts, 'daily_limit')
            continue

        if daily_pnl <= -MAX_DAILY_DRAWDOWN:
            continue

        cur_atr = atr[i] if not np.isnan(atr[i]) else 0.0

        # =====================================================================
        # CHECK EXITS FOR OPEN POSITION
        # =====================================================================
        if position != 0:
            bar_high = highs[i]
            bar_low = lows[i]
            hold_time = ts - entry_time
            can_exit_time = hold_time >= MIN_HOLD_SECONDS  # 30 sec min hold

            # Update best price for trailing stop
            if position > 0:
                if bar_high > best_price:
                    best_price = bar_high
                    if trail_type != 'none' and cur_atr > 0:
                        trailing_stop_price = calc_trailing_stop(1, best_price, cur_atr)
            else:
                if bar_low < best_price:
                    best_price = bar_low
                    if trail_type != 'none' and cur_atr > 0:
                        trailing_stop_price = calc_trailing_stop(-1, best_price, cur_atr)

            # Breakeven logic
            if be_val > 0 and not breakeven_active:
                be_points = be_val / MGC_POINT_VALUE
                if position > 0 and bar_high >= entry_price + be_points:
                    stop_loss_price = entry_price + SPREAD_SLIPPAGE  # Move stop to breakeven + spread
                    breakeven_active = True
                elif position < 0 and bar_low <= entry_price - be_points:
                    stop_loss_price = entry_price - SPREAD_SLIPPAGE
                    breakeven_active = True

            exited = False

            # 1. STOP LOSS CHECK (always fires, even before 30s)
            if stop_loss_price > 0:
                if position > 0 and bar_low <= stop_loss_price:
                    close_trade(stop_loss_price - SPREAD_SLIPPAGE, ts, 'stop_loss')
                    exited = True
                elif position < 0 and bar_high >= stop_loss_price:
                    close_trade(stop_loss_price + SPREAD_SLIPPAGE, ts, 'stop_loss')
                    exited = True

            # 2. TRAILING STOP CHECK (always fires, even before 30s)
            if not exited and trailing_stop_price > 0:
                if position > 0 and bar_low <= trailing_stop_price:
                    close_trade(trailing_stop_price - SPREAD_SLIPPAGE, ts, 'trailing_stop')
                    exited = True
                elif position < 0 and bar_high >= trailing_stop_price:
                    close_trade(trailing_stop_price + SPREAD_SLIPPAGE, ts, 'trailing_stop')
                    exited = True

            # 3. TAKE PROFIT CHECK (respects 30s hold)
            if not exited and take_profit_price > 0 and can_exit_time:
                if position > 0 and bar_high >= take_profit_price:
                    close_trade(take_profit_price - SPREAD_SLIPPAGE, ts, 'take_profit')
                    exited = True
                elif position < 0 and bar_low <= take_profit_price:
                    close_trade(take_profit_price + SPREAD_SLIPPAGE, ts, 'take_profit')
                    exited = True

            # 4. OPPOSITE SIGNAL EXIT (respects 30s hold)
            if not exited and can_exit_time:
                if position > 0 and sell_signal[i]:
                    exit_px = closes[i] - SPREAD_SLIPPAGE
                    close_trade(exit_px, ts, 'opposite_signal')
                    exited = True
                    # Reverse into short
                    enter_trade(-1, closes[i], ts, i, cur_atr)
                elif position < 0 and buy_signal[i]:
                    exit_px = closes[i] + SPREAD_SLIPPAGE
                    close_trade(exit_px, ts, 'opposite_signal')
                    exited = True
                    # Reverse into long
                    enter_trade(1, closes[i], ts, i, cur_atr)

            if exited:
                continue

        # =====================================================================
        # ENTER NEW POSITION IF FLAT
        # =====================================================================
        if position == 0:
            if buy_signal[i]:
                enter_trade(1, closes[i], ts, i, cur_atr)
            elif sell_signal[i]:
                enter_trade(-1, closes[i], ts, i, cur_atr)

    # Close any open position at end
    if position != 0:
        exit_px = closes[-1] - SPREAD_SLIPPAGE if position > 0 else closes[-1] + SPREAD_SLIPPAGE
        close_trade(exit_px, int(timestamps[-1]), 'end_of_data')

    if daily_pnl < 0:
        max_daily_dd = min(max_daily_dd, daily_pnl)

    # Compute stats
    if not trades:
        return None

    winning = [t for t in trades if t['pnl'] > 0]
    losing = [t for t in trades if t['pnl'] <= 0]
    total_pnl = sum(t['pnl'] for t in trades)
    gross_profit = sum(t['pnl'] for t in winning)
    gross_loss = abs(sum(t['pnl'] for t in losing))
    total_costs = sum(t['costs'] for t in trades)
    avg_hold = np.mean([t['hold_seconds'] for t in trades])

    # Count exits by type
    exit_counts = {}
    for t in trades:
        r = t['exit_reason']
        exit_counts[r] = exit_counts.get(r, 0) + 1

    return {
        'total_trades': len(trades),
        'winning_trades': len(winning),
        'losing_trades': len(losing),
        'win_rate': 100.0 * len(winning) / len(trades),
        'total_pnl': total_pnl,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'profit_factor': gross_profit / gross_loss if gross_loss > 0 else (999.99 if gross_profit > 0 else 0),
        'avg_win': gross_profit / len(winning) if winning else 0,
        'avg_loss': gross_loss / len(losing) if losing else 0,
        'max_drawdown': max_dd,
        'max_daily_drawdown': abs(max_daily_dd),
        'total_costs': total_costs,
        'final_equity': equity,
        'avg_hold_seconds': avg_hold,
        'exit_counts': exit_counts,
        'trades': trades,
    }


# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 90)
    print("  MGC DIAMANTE STRATEGY — ENHANCED COMPREHENSIVE BACKTEST")
    print("  All Exit Strategies • SL/TP/Trailing • 30s Min Hold • Full Parameter Sweep")
    print("=" * 90)
    print(f"  Instrument:       MGC (Micro Gold Futures)")
    print(f"  Point Value:      ${MGC_POINT_VALUE}/pt | Tick: ${MGC_TICK_VALUE}/tick")
    print(f"  Apex RT Cost:     ${APEX_COST_RT}/contract")
    print(f"  Slippage:         {SPREAD_SLIPPAGE}/side ({SPREAD_SLIPPAGE/MGC_TICK_SIZE:.0f} tick)")
    print(f"  Starting Equity:  ${STARTING_EQUITY:,.0f}")
    print(f"  Max Daily DD:     ${MAX_DAILY_DRAWDOWN:,.0f}")
    print(f"  Min Hold Time:    {MIN_HOLD_SECONDS}s (unless stop hit)")
    print()

    # Load data
    available = {}
    for tf in TIMEFRAMES:
        filepath = f"data/mgc_{tf}.csv"
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            if len(df) > 50:
                available[tf] = df
                start = pd.to_datetime(df['time'].iloc[0], unit='s')
                end = pd.to_datetime(df['time'].iloc[-1], unit='s')
                print(f"  mgc_{tf}.csv: {len(df):>6} bars ({start.date()} to {end.date()})")

    if not available:
        print("\n  ERROR: No MGC data. Run: python mgc_data_downloader.py")
        sys.exit(1)

    # Build combos
    tfs = list(available.keys())
    combos = list(product(tfs, KEY_VALUES, ATR_PERIODS, CONTRACT_SIZES, CANDLE_TYPES, range(len(EXIT_PRESETS))))
    total = len(combos)
    print(f"\n  Timeframes: {', '.join(tfs)}")
    print(f"  Key Values: {KEY_VALUES}")
    print(f"  ATR Periods: {ATR_PERIODS}")
    print(f"  Contracts: {CONTRACT_SIZES}")
    print(f"  Candle Types: {CANDLE_TYPES}")
    print(f"  Exit Presets: {len(EXIT_PRESETS)}")
    print(f"  Total Combinations: {total}")
    print()

    all_results = []

    for idx, (tf, kv, atr_p, cts, candle, ep_idx) in enumerate(combos):
        use_ha = (candle == 'HA')
        preset = EXIT_PRESETS[ep_idx]

        result = run_backtest(available[tf], kv, atr_p, use_ha, cts, preset)

        if result is not None and result['total_trades'] >= 3:
            result['timeframe'] = tf
            result['key_value'] = kv
            result['atr_period'] = atr_p
            result['contracts'] = cts
            result['candle_type'] = candle
            result['exit_preset'] = preset[0]
            result['label'] = f"{tf}|{candle}|K{kv}|ATR{atr_p}|{cts}ct|{preset[0]}"
            all_results.append(result)

        if (idx + 1) % 200 == 0 or idx == total - 1:
            pct = 100.0 * (idx + 1) / total
            print(f"  [{pct:5.1f}%] {idx+1}/{total} tested | {len(all_results)} valid results so far")

    if not all_results:
        print("\n  No valid results.")
        sys.exit(1)

    # =========================================================================
    # FILTER by daily drawdown constraint
    # =========================================================================
    valid = [r for r in all_results if r['max_daily_drawdown'] <= MAX_DAILY_DRAWDOWN + 50]
    print(f"\n  {len(valid)}/{len(all_results)} strategies within ${MAX_DAILY_DRAWDOWN} daily DD limit")

    if len(valid) < 3:
        print("  Relaxing daily DD filter to show results...")
        valid = sorted(all_results, key=lambda x: x['max_daily_drawdown'])

    # Only profitable strategies
    profitable = [r for r in valid if r['total_pnl'] > 0]
    print(f"  {len(profitable)} profitable strategies found")

    analysis_set = profitable if len(profitable) >= 3 else valid

    # =========================================================================
    # TOP 3 BY PROFIT FACTOR (min 10 trades)
    # =========================================================================
    pf_set = [r for r in analysis_set if r['total_trades'] >= 10]
    if len(pf_set) < 3:
        pf_set = analysis_set
    top_pf = sorted(pf_set, key=lambda x: x['profit_factor'], reverse=True)[:3]

    # =========================================================================
    # TOP 3 BY TOTAL P&L
    # =========================================================================
    top_pnl = sorted(analysis_set, key=lambda x: x['total_pnl'], reverse=True)[:3]

    # =========================================================================
    # TOP 3 BY COMPOSITE (PF * sqrt(trades) * low_dd_bonus)
    # =========================================================================
    for r in analysis_set:
        dd_penalty = max(0.1, 1.0 - r['max_drawdown'] / 10000.0)
        r['composite'] = (
            r['profit_factor']
            * np.sqrt(max(r['total_trades'], 1))
            * dd_penalty
            * (1 if r['total_pnl'] > 0 else 0.05)
        )
    top_composite = sorted(analysis_set, key=lambda x: x['composite'], reverse=True)[:3]

    # =========================================================================
    # TOP 3 BY WIN RATE (min 20 trades)
    # =========================================================================
    wr_set = [r for r in analysis_set if r['total_trades'] >= 20]
    if len(wr_set) < 3:
        wr_set = analysis_set
    top_wr = sorted(wr_set, key=lambda x: x['win_rate'], reverse=True)[:3]

    # =========================================================================
    # TOP 3 LOWEST DRAWDOWN (profitable only)
    # =========================================================================
    low_dd_set = [r for r in analysis_set if r['total_pnl'] > 0]
    if len(low_dd_set) < 3:
        low_dd_set = analysis_set
    top_low_dd = sorted(low_dd_set, key=lambda x: x['max_drawdown'])[:3]

    def print_section(title, results):
        print(f"\n{'=' * 90}")
        print(f"  {title}")
        print(f"{'=' * 90}")
        for rank, r in enumerate(results, 1):
            print(f"\n  #{rank}: {r['label']}")
            print(f"  {'─' * 75}")
            print(f"  Trades: {r['total_trades']:>5}  |  Win Rate: {r['win_rate']:>5.1f}%  |  "
                  f"Profit Factor: {r['profit_factor']:>6.2f}")
            print(f"  Total P&L:   ${r['total_pnl']:>10,.2f}  |  Final Equity: ${r['final_equity']:>12,.2f}")
            print(f"  Gross Profit: ${r['gross_profit']:>9,.2f}  |  Gross Loss:   ${r['gross_loss']:>10,.2f}")
            print(f"  Avg Win:      ${r['avg_win']:>9,.2f}  |  Avg Loss:     ${r['avg_loss']:>10,.2f}")
            print(f"  Max Drawdown: ${r['max_drawdown']:>9,.2f}  |  Max Daily DD: ${r['max_daily_drawdown']:>10,.2f}")
            print(f"  Total Costs:  ${r['total_costs']:>9,.2f}  |  Avg Hold:     {r['avg_hold_seconds']:>8.0f}s")
            print(f"  Return: {((r['final_equity'] - STARTING_EQUITY) / STARTING_EQUITY * 100):>+.2f}%")
            ec = r.get('exit_counts', {})
            if ec:
                parts = [f"{k}:{v}" for k, v in sorted(ec.items(), key=lambda x: -x[1])]
                print(f"  Exits: {' | '.join(parts)}")

    print_section("TOP 3 BY PROFIT FACTOR (min 10 trades)", top_pf)
    print_section("TOP 3 BY TOTAL P&L", top_pnl)
    print_section("TOP 3 OVERALL (COMPOSITE SCORE)", top_composite)
    print_section("TOP 3 BY WIN RATE (min 20 trades)", top_wr)
    print_section("TOP 3 LOWEST DRAWDOWN (profitable)", top_low_dd)

    # =========================================================================
    # BEST OVERALL WINNER
    # =========================================================================
    # Score: normalize PF, PnL, win_rate, inverse DD — pick #1
    if profitable:
        for r in profitable:
            pf_norm = min(r['profit_factor'], 5.0) / 5.0
            pnl_max = max(rr['total_pnl'] for rr in profitable)
            pnl_norm = r['total_pnl'] / pnl_max if pnl_max > 0 else 0
            wr_norm = r['win_rate'] / 100.0
            dd_norm = 1.0 - min(r['max_drawdown'] / 5000.0, 1.0)
            r['final_score'] = pf_norm * 0.30 + pnl_norm * 0.30 + wr_norm * 0.20 + dd_norm * 0.20

        best = sorted(profitable, key=lambda x: x['final_score'], reverse=True)[0]
        print_section("BEST OVERALL WINNER STRATEGY", [best])

    # =========================================================================
    # FULL TABLE (top 60)
    # =========================================================================
    print(f"\n{'=' * 90}")
    print(f"  FULL RESULTS (top 60 by P&L)")
    print(f"{'=' * 90}")
    header = (f"  {'TF':<5} {'Can':<4} {'K':>1} {'ATR':>3} {'Ct':>2} {'Exit Preset':<22} "
              f"{'Trd':>4} {'WR%':>5} {'P&L':>10} {'PF':>5} {'MaxDD':>7} {'DlyDD':>6} {'AvgHold':>7}")
    print(header)
    print(f"  {'─' * 86}")

    sorted_all = sorted(analysis_set, key=lambda x: x['total_pnl'], reverse=True)
    for r in sorted_all[:60]:
        print(f"  {r['timeframe']:<5} {r['candle_type']:<4} {r['key_value']:>1} {r['atr_period']:>3} "
              f"{r['contracts']:>2} {r['exit_preset']:<22} "
              f"{r['total_trades']:>4} {r['win_rate']:>4.1f}% ${r['total_pnl']:>8,.0f} "
              f"{r['profit_factor']:>4.2f} ${r['max_drawdown']:>5,.0f} ${r['max_daily_drawdown']:>4,.0f} "
              f"{r['avg_hold_seconds']:>5.0f}s")

    # =========================================================================
    # SAVE RESULTS
    # =========================================================================
    os.makedirs('results', exist_ok=True)
    ts_str = datetime.now().strftime('%Y%m%d_%H%M%S')

    rows = []
    for r in sorted_all:
        rows.append({
            'Timeframe': r['timeframe'], 'Candle': r['candle_type'],
            'Key_Value': r['key_value'], 'ATR_Period': r['atr_period'],
            'Contracts': r['contracts'], 'Exit_Preset': r['exit_preset'],
            'Trades': r['total_trades'], 'Win_Rate': round(r['win_rate'], 2),
            'Total_PnL': round(r['total_pnl'], 2),
            'Profit_Factor': round(r['profit_factor'], 2),
            'Gross_Profit': round(r['gross_profit'], 2),
            'Gross_Loss': round(r['gross_loss'], 2),
            'Avg_Win': round(r['avg_win'], 2), 'Avg_Loss': round(r['avg_loss'], 2),
            'Max_Drawdown': round(r['max_drawdown'], 2),
            'Max_Daily_DD': round(r['max_daily_drawdown'], 2),
            'Total_Costs': round(r['total_costs'], 2),
            'Final_Equity': round(r['final_equity'], 2),
            'Avg_Hold_Sec': round(r['avg_hold_seconds'], 1),
        })
    pd.DataFrame(rows).to_csv(f'results/mgc_enhanced_backtest_{ts_str}.csv', index=False)

    # Save trade logs for top 3 composite
    for rank, r in enumerate(top_composite[:3], 1):
        trades_df = pd.DataFrame(r['trades'])
        trades_df.to_csv(f'results/mgc_enhanced_top{rank}_trades_{ts_str}.csv', index=False)

    # Save trade log for best overall
    if profitable:
        pd.DataFrame(best['trades']).to_csv(f'results/mgc_enhanced_best_trades_{ts_str}.csv', index=False)

    print(f"\n  Results saved to results/mgc_enhanced_backtest_{ts_str}.csv")
    print(f"  Trade logs saved for top strategies")
    print(f"\n  Done! {len(all_results)} total | {len(profitable)} profitable | {total} combinations tested")


if __name__ == "__main__":
    main()
