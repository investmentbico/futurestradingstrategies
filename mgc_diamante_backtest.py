#!/usr/bin/env python3
"""
MGC Diamante Strategy Backtest — Opposite-Signal Exit
=======================================================
Backtests the ATR Trailing Stop strategy (converted from Pine Script)
on Micro Gold Futures (MGC) with Apex Prop Firm costs.

Key change from original: Positions exit ONLY on opposite signal.
  - Long exits when Sell signal fires
  - Short exits when Buy signal fires
  - No premature HFT exits

Tests:
  - Timeframes: 1m, 2m, 3m, 5m, 10m, 15m
  - Candle types: Normal and Heikin Ashi
  - Contracts: 1, 3, 5
  - Key values (ATR sensitivity): 1, 2, 3
  - ATR periods: 10, 14, 20
  - Starting equity: $150,000
  - Max daily drawdown: $2,000

Usage:
  python mgc_diamante_backtest.py
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from itertools import product
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# MGC CONTRACT SPECIFICATIONS
# =============================================================================

MGC_POINT_VALUE = 10.0     # $10 per point
MGC_TICK_SIZE   = 0.10     # $0.10 minimum tick
MGC_TICK_VALUE  = 1.00     # $1.00 per tick

# Apex Prop Firm costs per contract
APEX_EXCHANGE_FEE  = 1.25   # CME exchange fee per side
APEX_CLEARING_FEE  = 0.10   # Clearing fee per side
APEX_NFA_FEE       = 0.02   # NFA regulatory fee per side
APEX_BROKER_COMM   = 0.25   # Tradovate/Apex commission per side
APEX_COST_PER_SIDE = APEX_EXCHANGE_FEE + APEX_CLEARING_FEE + APEX_NFA_FEE + APEX_BROKER_COMM  # $1.62/side
APEX_COST_RT       = APEX_COST_PER_SIDE * 2.0  # $3.24 round trip per contract

SPREAD_SLIPPAGE_TICKS = 1.0  # 1 tick slippage each side

# =============================================================================
# RISK PARAMETERS
# =============================================================================

STARTING_EQUITY = 150000.0
MAX_DAILY_DRAWDOWN = 2000.0

# =============================================================================
# PARAMETER GRID
# =============================================================================

TIMEFRAMES = ['1min', '2min', '3min', '5min', '10min', '15min']
CONTRACT_SIZES = [1, 3, 5]
KEY_VALUES = [1, 2, 3]
ATR_PERIODS = [10, 14, 20]
USE_HEIKIN_ASHI = [False, True]


# =============================================================================
# INDICATORS
# =============================================================================

def calc_ema(prices, period):
    """EMA calculation matching Pine Script exactly."""
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out


def calc_atr(high, low, close, period):
    """ATR with Wilder's smoothing."""
    n = len(close)
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]

    atr = np.full(n, np.nan)
    if n < period:
        return atr

    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    return atr


def to_heikin_ashi(opens, highs, lows, closes):
    """Convert OHLC to Heikin Ashi candles."""
    n = len(closes)
    ha_close = (opens + highs + lows + closes) / 4.0
    ha_open = np.empty(n)
    ha_open[0] = (opens[0] + closes[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2.0
    ha_high = np.maximum(highs, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(lows, np.minimum(ha_open, ha_close))
    return ha_open, ha_high, ha_low, ha_close


# =============================================================================
# ATR TRAILING STOP (matches Pine Script exactly)
# =============================================================================

def calc_atr_trailing_stop(src, atr, key_value):
    """
    Pine Script logic:
      xATRTrailingStop :=
        src > prev and src[1] > prev ? max(prev, src - nLoss) :
        src < prev and src[1] < prev ? min(prev, src + nLoss) :
        src > prev ? src - nLoss : src + nLoss
    """
    n = len(src)
    trail = np.zeros(n)

    # Find first valid ATR bar
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


# =============================================================================
# SIGNAL GENERATION
# =============================================================================

def generate_signals(src, trail):
    """
    Pine Script:
      ema1 = ema(src, 1)  → just src
      above = crossover(ema1, trail)
      below = crossover(trail, ema1)
      buySignal  = src > trail and above
      sellSignal = src < trail and below
    """
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
# BACKTEST ENGINE — OPPOSITE-SIGNAL EXIT ONLY
# =============================================================================

def run_backtest(df, key_value, atr_period, use_ha, contracts):
    """
    Run the Diamante strategy with opposite-signal exit logic.

    EXIT RULE: Long exits ONLY on Sell signal. Short exits ONLY on Buy signal.
    No trailing stop exits, no take-profit, no hard stops for trade management.
    The ATR trailing stop is used purely for signal generation.
    Daily drawdown limit enforced separately.
    """
    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    opens = df['open'].values.astype(float)
    timestamps = df['time'].values

    n = len(closes)
    if n < atr_period + 5:
        return None

    # Get price source
    if use_ha:
        _, _, _, src = to_heikin_ashi(opens, highs, lows, closes)
    else:
        src = closes.copy()

    # Calculate indicators
    atr = calc_atr(highs, lows, closes, atr_period)
    trail = calc_atr_trailing_stop(src, atr, key_value)
    buy_signal, sell_signal = generate_signals(src, trail)

    # Trading state
    position = 0
    entry_price = 0.0
    entry_time = 0
    entry_idx = 0

    equity = STARTING_EQUITY
    peak_equity = STARTING_EQUITY
    max_dd = 0.0

    daily_pnl = 0.0
    max_daily_dd = 0.0
    current_day = -1

    trades = []
    equity_curve = [equity]

    warmup = atr_period + 2
    slippage = SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE

    for i in range(warmup, n):
        ts = int(timestamps[i])
        day = ts // 86400

        # Daily reset
        if day != current_day:
            if current_day >= 0 and daily_pnl < 0:
                max_daily_dd = min(max_daily_dd, daily_pnl)
            daily_pnl = 0.0
            current_day = day

        # Force close if daily drawdown limit hit
        if daily_pnl <= -MAX_DAILY_DRAWDOWN and position != 0:
            exit_price = closes[i]
            if position > 0:
                exit_price -= slippage
                pnl = (exit_price - entry_price) * abs(position) * MGC_POINT_VALUE
            else:
                exit_price += slippage
                pnl = (entry_price - exit_price) * abs(position) * MGC_POINT_VALUE
            costs = APEX_COST_RT * abs(position)
            pnl -= costs
            trades.append({
                'entry_time': entry_time, 'exit_time': ts,
                'entry_price': entry_price, 'exit_price': exit_price,
                'direction': 'Long' if position > 0 else 'Short',
                'contracts': abs(position), 'pnl': pnl, 'costs': costs,
                'exit_reason': 'daily_limit'
            })
            equity += pnl
            daily_pnl += pnl
            equity_curve.append(equity)
            position = 0
            continue

        if daily_pnl <= -MAX_DAILY_DRAWDOWN:
            continue

        # OPPOSITE-SIGNAL EXIT + REVERSAL
        if position > 0 and sell_signal[i]:
            exit_price = closes[i] - slippage
            pnl = (exit_price - entry_price) * contracts * MGC_POINT_VALUE
            costs = APEX_COST_RT * contracts
            pnl -= costs
            trades.append({
                'entry_time': entry_time, 'exit_time': ts,
                'entry_price': entry_price, 'exit_price': exit_price,
                'direction': 'Long', 'contracts': contracts,
                'pnl': pnl, 'costs': costs, 'exit_reason': 'opposite_signal'
            })
            equity += pnl
            daily_pnl += pnl
            equity_curve.append(equity)

            # Reverse to short
            entry_price = closes[i] + slippage
            entry_time = ts
            entry_idx = i
            position = -contracts

        elif position < 0 and buy_signal[i]:
            exit_price = closes[i] + slippage
            pnl = (entry_price - exit_price) * contracts * MGC_POINT_VALUE
            costs = APEX_COST_RT * contracts
            pnl -= costs
            trades.append({
                'entry_time': entry_time, 'exit_time': ts,
                'entry_price': entry_price, 'exit_price': exit_price,
                'direction': 'Short', 'contracts': contracts,
                'pnl': pnl, 'costs': costs, 'exit_reason': 'opposite_signal'
            })
            equity += pnl
            daily_pnl += pnl
            equity_curve.append(equity)

            # Reverse to long
            entry_price = closes[i] + slippage
            entry_time = ts
            entry_idx = i
            position = contracts

        elif position == 0:
            if buy_signal[i]:
                entry_price = closes[i] + slippage
                entry_time = ts
                entry_idx = i
                position = contracts
            elif sell_signal[i]:
                entry_price = closes[i] + slippage
                entry_time = ts
                entry_idx = i
                position = -contracts

        # Update drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd

    # Close open position at end
    if position != 0:
        exit_price = closes[-1]
        if position > 0:
            exit_price -= slippage
            pnl = (exit_price - entry_price) * contracts * MGC_POINT_VALUE
        else:
            exit_price += slippage
            pnl = (entry_price - exit_price) * contracts * MGC_POINT_VALUE
        costs = APEX_COST_RT * contracts
        pnl -= costs
        trades.append({
            'entry_time': entry_time, 'exit_time': int(timestamps[-1]),
            'entry_price': entry_price, 'exit_price': exit_price,
            'direction': 'Long' if position > 0 else 'Short',
            'contracts': contracts, 'pnl': pnl, 'costs': costs,
            'exit_reason': 'end_of_data'
        })
        equity += pnl
        equity_curve.append(equity)

    if daily_pnl < 0:
        max_daily_dd = min(max_daily_dd, daily_pnl)

    # Compute statistics
    if not trades:
        return None

    winning = [t for t in trades if t['pnl'] > 0]
    losing = [t for t in trades if t['pnl'] <= 0]

    total_pnl = sum(t['pnl'] for t in trades)
    gross_profit = sum(t['pnl'] for t in winning)
    gross_loss = abs(sum(t['pnl'] for t in losing))
    total_costs = sum(t['costs'] for t in trades)

    return {
        'total_trades': len(trades),
        'winning_trades': len(winning),
        'losing_trades': len(losing),
        'win_rate': 100.0 * len(winning) / len(trades) if trades else 0,
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
        'equity_curve': equity_curve,
        'trades': trades,
    }


# =============================================================================
# MAIN — COMPREHENSIVE PARAMETER SWEEP
# =============================================================================

def main():
    print("=" * 80)
    print("MGC DIAMANTE STRATEGY — COMPREHENSIVE BACKTEST")
    print("ATR Trailing Stop with Opposite-Signal Exit Only")
    print("=" * 80)
    print(f"Instrument:       MGC (Micro Gold Futures)")
    print(f"Point Value:      ${MGC_POINT_VALUE}/point | Tick: ${MGC_TICK_VALUE}/tick")
    print(f"Apex RT Cost:     ${APEX_COST_RT:.2f}/contract")
    print(f"Spread Slippage:  {SPREAD_SLIPPAGE_TICKS} tick(s)/side")
    print(f"Starting Equity:  ${STARTING_EQUITY:,.0f}")
    print(f"Max Daily DD:     ${MAX_DAILY_DRAWDOWN:,.0f}")
    print()

    # Check for data files
    available_timeframes = []
    for tf in TIMEFRAMES:
        filepath = f"data/mgc_{tf}.csv"
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            if len(df) > 50:
                available_timeframes.append(tf)
                start = pd.to_datetime(df['time'].iloc[0], unit='s')
                end = pd.to_datetime(df['time'].iloc[-1], unit='s')
                print(f"  Found mgc_{tf}.csv: {len(df)} bars ({start.date()} to {end.date()})")
            else:
                print(f"  mgc_{tf}.csv: too few bars ({len(df)}), skipping")
        else:
            print(f"  mgc_{tf}.csv: NOT FOUND")

    if not available_timeframes:
        print("\nERROR: No MGC data files found in data/ directory.")
        print("Run: python mgc_data_downloader.py")
        sys.exit(1)

    print(f"\nAvailable timeframes: {', '.join(available_timeframes)}")

    # Build parameter combinations
    param_combos = list(product(
        available_timeframes,
        CONTRACT_SIZES,
        KEY_VALUES,
        ATR_PERIODS,
        USE_HEIKIN_ASHI
    ))

    total_combos = len(param_combos)
    print(f"Total parameter combinations: {total_combos}")
    print()

    # Run all backtests
    all_results = []
    data_cache = {}

    for idx, (tf, contracts, key_val, atr_per, use_ha) in enumerate(param_combos):
        candle_type = "HA" if use_ha else "Normal"
        label = f"{tf} | {contracts}ct | Key={key_val} ATR={atr_per} | {candle_type}"

        # Cache data loading
        if tf not in data_cache:
            filepath = f"data/mgc_{tf}.csv"
            data_cache[tf] = pd.read_csv(filepath)

        df = data_cache[tf]

        result = run_backtest(df, key_val, atr_per, use_ha, contracts)

        if result is None:
            continue

        # Tag result with parameters
        result['timeframe'] = tf
        result['contracts'] = contracts
        result['key_value'] = key_val
        result['atr_period'] = atr_per
        result['candle_type'] = candle_type
        result['label'] = label

        all_results.append(result)

        # Progress indicator
        if (idx + 1) % 20 == 0 or idx == total_combos - 1:
            print(f"  Progress: {idx + 1}/{total_combos} combinations tested...")

    if not all_results:
        print("\nNo valid results. Check data files.")
        sys.exit(1)

    # =================================================================
    # FILTER: Respect $2K max daily drawdown constraint
    # =================================================================
    valid_results = [r for r in all_results if r['max_daily_drawdown'] <= MAX_DAILY_DRAWDOWN + 100]  # small tolerance

    if not valid_results:
        print("\nWARNING: No strategies met the $2K daily drawdown limit.")
        print("Showing all results instead (sorted by daily drawdown).")
        valid_results = all_results

    # =================================================================
    # RANK AND SHOW TOP 3
    # =================================================================

    # Sort by a composite score: profit_factor * total_pnl (both matter)
    # Only consider strategies with at least 5 trades
    scored = [r for r in valid_results if r['total_trades'] >= 5]

    if not scored:
        scored = valid_results

    # Top 3 by Profit Factor
    top_pf = sorted(scored, key=lambda x: x['profit_factor'], reverse=True)[:3]

    # Top 3 by Total P&L
    top_pnl = sorted(scored, key=lambda x: x['total_pnl'], reverse=True)[:3]

    # Top 3 by composite: PF * sqrt(trades) * (PnL > 0)
    for r in scored:
        r['composite_score'] = (
            r['profit_factor'] * np.sqrt(max(r['total_trades'], 1)) *
            (1 if r['total_pnl'] > 0 else 0.1)
        )
    top_composite = sorted(scored, key=lambda x: x['composite_score'], reverse=True)[:3]

    def print_top_results(title, results):
        print(f"\n{'=' * 80}")
        print(f"  {title}")
        print(f"{'=' * 80}")
        for rank, r in enumerate(results, 1):
            print(f"\n  #{rank}: {r['label']}")
            print(f"  {'─' * 60}")
            print(f"  Total Trades:       {r['total_trades']}")
            print(f"  Win Rate:           {r['win_rate']:.1f}%")
            print(f"  Total P&L:          ${r['total_pnl']:,.2f}")
            print(f"  Profit Factor:      {r['profit_factor']:.2f}")
            print(f"  Gross Profit:       ${r['gross_profit']:,.2f}")
            print(f"  Gross Loss:         ${r['gross_loss']:,.2f}")
            print(f"  Avg Win:            ${r['avg_win']:,.2f}")
            print(f"  Avg Loss:           ${r['avg_loss']:,.2f}")
            print(f"  Max Drawdown:       ${r['max_drawdown']:,.2f}")
            print(f"  Max Daily DD:       ${r['max_daily_drawdown']:,.2f}")
            print(f"  Total Costs:        ${r['total_costs']:,.2f}")
            print(f"  Final Equity:       ${r['final_equity']:,.2f}")
            print(f"  Return:             {((r['final_equity'] - STARTING_EQUITY) / STARTING_EQUITY * 100):.2f}%")

    print_top_results("TOP 3 BY PROFIT FACTOR", top_pf)
    print_top_results("TOP 3 BY TOTAL P&L", top_pnl)
    print_top_results("TOP 3 OVERALL (COMPOSITE SCORE)", top_composite)

    # =================================================================
    # FULL RESULTS TABLE
    # =================================================================
    print(f"\n{'=' * 80}")
    print("  FULL RESULTS SUMMARY (sorted by P&L)")
    print(f"{'=' * 80}")
    print(f"  {'Timeframe':<7} {'Ct':>2} {'Key':>3} {'ATR':>3} {'Type':<6} {'Trades':>6} "
          f"{'WinRate':>7} {'P&L':>12} {'PF':>6} {'MaxDD':>10} {'DailyDD':>8}")
    print(f"  {'─' * 78}")

    sorted_all = sorted(scored, key=lambda x: x['total_pnl'], reverse=True)
    for r in sorted_all[:50]:  # Show top 50
        print(f"  {r['timeframe']:<7} {r['contracts']:>2} {r['key_value']:>3} {r['atr_period']:>3} "
              f"{r['candle_type']:<6} {r['total_trades']:>6} {r['win_rate']:>6.1f}% "
              f"${r['total_pnl']:>10,.2f} {r['profit_factor']:>5.2f} "
              f"${r['max_drawdown']:>8,.0f} ${r['max_daily_drawdown']:>6,.0f}")

    # =================================================================
    # SAVE RESULTS
    # =================================================================
    os.makedirs('results', exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    summary_rows = []
    for r in sorted_all:
        summary_rows.append({
            'Timeframe': r['timeframe'],
            'Contracts': r['contracts'],
            'Key_Value': r['key_value'],
            'ATR_Period': r['atr_period'],
            'Candle_Type': r['candle_type'],
            'Total_Trades': r['total_trades'],
            'Win_Rate': round(r['win_rate'], 2),
            'Total_PnL': round(r['total_pnl'], 2),
            'Profit_Factor': round(r['profit_factor'], 2),
            'Gross_Profit': round(r['gross_profit'], 2),
            'Gross_Loss': round(r['gross_loss'], 2),
            'Max_Drawdown': round(r['max_drawdown'], 2),
            'Max_Daily_DD': round(r['max_daily_drawdown'], 2),
            'Total_Costs': round(r['total_costs'], 2),
            'Final_Equity': round(r['final_equity'], 2),
        })

    results_df = pd.DataFrame(summary_rows)
    results_file = f'results/mgc_diamante_backtest_{timestamp}.csv'
    results_df.to_csv(results_file, index=False)
    print(f"\n  Results saved: {results_file}")

    # Save top 3 trade logs
    for rank, r in enumerate(top_composite[:3], 1):
        trades_df = pd.DataFrame(r['trades'])
        trades_file = f'results/mgc_diamante_top{rank}_trades_{timestamp}.csv'
        trades_df.to_csv(trades_file, index=False)

    print(f"  Trade logs saved for top 3 strategies")
    print(f"\nDone! {len(all_results)} total combinations tested.")


if __name__ == "__main__":
    main()
