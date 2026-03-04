#!/usr/bin/env python3
"""
Realistic Tastytrade Backtest - MNQ & MES Parameter Sweep
==========================================================
Real Tastytrade fees, spreads, and slippage.
Tests 1-5 contracts, multiple stop losses, $10K starting equity.
Finds top 3 winning configurations.

Usage: python tastytrade_backtest.py
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import time
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# REAL TASTYTRADE FEE STRUCTURE (2026)
# =============================================================================

TASTYTRADE_FEES = {
    'MNQ': {
        'commission_per_side': 1.25,       # Tastytrade commission
        'exchange_fee_per_side': 1.18,     # CME exchange fee
        'nfa_fee_per_side': 0.02,          # NFA regulatory fee
        'point_value': 20.0,               # $20 per point (0.25 tick = $5)
        'tick_size': 0.25,
        'spread_points': 0.50,             # Typical spread in points
        'slippage_points': 0.25,           # Estimated slippage per side
    },
    'MES': {
        'commission_per_side': 1.25,
        'exchange_fee_per_side': 0.47,     # CME exchange fee (lower for MES)
        'nfa_fee_per_side': 0.02,
        'point_value': 5.0,               # $5 per point (0.25 tick = $1.25)
        'tick_size': 0.25,
        'spread_points': 0.25,
        'slippage_points': 0.25,
    }
}

def get_round_trip_cost(symbol, contracts):
    """Calculate total round-trip cost per trade including fees, spread, slippage"""
    fees = TASTYTRADE_FEES[symbol]
    # Commission + exchange + NFA fees (per side, per contract)
    fee_per_contract_rt = (fees['commission_per_side'] + fees['exchange_fee_per_side'] + fees['nfa_fee_per_side']) * 2
    total_fees = fee_per_contract_rt * contracts
    # Spread cost: half spread on entry, half on exit
    spread_cost = fees['spread_points'] * fees['point_value'] * contracts
    # Slippage: on both entry and exit
    slippage_cost = fees['slippage_points'] * 2 * fees['point_value'] * contracts
    return total_fees + spread_cost + slippage_cost

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
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out

def calc_stoch(high, low, close, k_period, d_period, smooth):
    n = len(close)
    lowest_low = np.array([np.min(low[max(0, i-k_period+1):i+1]) for i in range(k_period-1, n)])
    highest_high = np.array([np.max(high[max(0, i-k_period+1):i+1]) for i in range(k_period-1, n)])
    denom = highest_high - lowest_low
    denom[denom == 0] = 1e-10
    k = 100 * (close[k_period-1:] - lowest_low) / denom
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    valid = ~np.isnan(k)
    d = calc_ema(k[valid], d_period)
    d_full = np.full(n, np.nan)
    d_full[valid] = d
    valid2 = ~np.isnan(d_full)
    d_smooth = calc_ema(d_full[valid2], smooth)
    d_smooth_full = np.full(n, np.nan)
    d_smooth_full[valid2] = d_smooth
    return k, d_smooth_full

# =============================================================================
# BACKTEST ENGINE
# =============================================================================

def run_backtest(df, symbol, contracts, hard_stop_dollars, ema_fast=21, ema_slow=55,
                 stoch_lo=25, stoch_hi=75, atr_period=9, sl_atr_mult=2.0, tp_rr=1.8,
                 starting_equity=10000.0):
    """Run single backtest with given parameters. Returns detailed results."""

    fees = TASTYTRADE_FEES[symbol]
    point_value = fees['point_value']
    rt_cost = get_round_trip_cost(symbol, contracts)

    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    opens = df['open'].values.astype(float)

    # Calculate indicators
    ema_f = calc_ema(closes, ema_fast)
    ema_s = calc_ema(closes, ema_slow)
    atr = calc_atr(highs, lows, closes, atr_period)
    _, d_sm = calc_stoch(highs, lows, closes, 9, 3, 2)

    # Trading state
    equity = starting_equity
    peak_equity = starting_equity
    position = 0
    entry_price = 0.0
    stop_loss = 0.0
    take_profit = 0.0
    hard_stop_price = 0.0

    # Results tracking
    trades = []
    equity_curve = [starting_equity]
    max_drawdown_pct = 0.0
    max_drawdown_dollars = 0.0
    daily_pnl = {}

    # Minimum bars needed for indicators
    min_bars = max(ema_slow, atr_period) + 5

    for i in range(min_bars, len(closes)):
        if np.isnan(ema_f[i]) or np.isnan(ema_s[i]) or np.isnan(atr[i]) or np.isnan(d_sm[i]) or np.isnan(d_sm[i-1]):
            equity_curve.append(equity)
            continue

        price = closes[i]
        bar_high = highs[i]
        bar_low = lows[i]

        # Track daily P&L
        if 'timestamp' in df.columns:
            day_key = str(df['timestamp'].iloc[i].date()) if hasattr(df['timestamp'].iloc[i], 'date') else str(i // 390)
        else:
            day_key = str(i // 390)

        # Check exits first
        if position != 0:
            exit_price = None
            exit_reason = None

            if position > 0:  # Long
                if bar_low <= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif bar_low <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif bar_high >= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'
            else:  # Short
                if bar_high >= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif bar_high >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif bar_low <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            if exit_price is not None:
                if position > 0:
                    pnl_points = exit_price - entry_price
                else:
                    pnl_points = entry_price - exit_price

                pnl_dollars = (pnl_points * point_value * contracts) - rt_cost
                equity += pnl_dollars

                if day_key not in daily_pnl:
                    daily_pnl[day_key] = 0.0
                daily_pnl[day_key] += pnl_dollars

                trades.append({
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'direction': 'long' if position > 0 else 'short',
                    'pnl_points': pnl_points,
                    'pnl_dollars': pnl_dollars,
                    'fees': rt_cost,
                    'reason': exit_reason,
                    'contracts': contracts
                })

                position = 0
                entry_price = 0.0

                # Update drawdown
                peak_equity = max(peak_equity, equity)
                dd_dollars = peak_equity - equity
                dd_pct = (dd_dollars / peak_equity) * 100 if peak_equity > 0 else 0
                max_drawdown_dollars = max(max_drawdown_dollars, dd_dollars)
                max_drawdown_pct = max(max_drawdown_pct, dd_pct)

        # Check entries (only if flat)
        if position == 0:
            uptrend = ema_f[i] > ema_s[i]
            downtrend = ema_f[i] < ema_s[i]
            d_falling = d_sm[i] < d_sm[i-1]
            d_rising = d_sm[i] > d_sm[i-1]
            current_atr = atr[i]

            # Check margin requirement (rough: need at least $1000 per MNQ contract, $500 per MES)
            margin_per_contract = 1000.0 if symbol == 'MNQ' else 500.0
            if equity < margin_per_contract * contracts:
                equity_curve.append(equity)
                continue

            # Long signal
            if uptrend and d_falling and d_sm[i] <= stoch_lo and current_atr > 0:
                position = 1
                entry_price = price + (fees['spread_points'] / 2)  # Pay half spread on entry
                atr_stop = entry_price - (current_atr * sl_atr_mult)
                hard_stop_price = entry_price - (hard_stop_dollars / (contracts * point_value))
                stop_loss = max(atr_stop, hard_stop_price)  # Use tighter stop
                take_profit = entry_price + (abs(entry_price - stop_loss) * tp_rr)

            # Short signal
            elif downtrend and d_rising and d_sm[i] >= stoch_hi and current_atr > 0:
                position = -1
                entry_price = price - (fees['spread_points'] / 2)  # Pay half spread on entry
                atr_stop = entry_price + (current_atr * sl_atr_mult)
                hard_stop_price = entry_price + (hard_stop_dollars / (contracts * point_value))
                stop_loss = min(atr_stop, hard_stop_price)  # Use tighter stop
                take_profit = entry_price - (abs(stop_loss - entry_price) * tp_rr)

        equity_curve.append(equity)

    # Close any open position at end
    if position != 0:
        if position > 0:
            pnl_points = closes[-1] - entry_price
        else:
            pnl_points = entry_price - closes[-1]
        pnl_dollars = (pnl_points * point_value * contracts) - rt_cost
        equity += pnl_dollars
        trades.append({
            'entry_price': entry_price, 'exit_price': closes[-1],
            'direction': 'long' if position > 0 else 'short',
            'pnl_points': pnl_points, 'pnl_dollars': pnl_dollars,
            'fees': rt_cost, 'reason': 'end_of_data', 'contracts': contracts
        })

    # Calculate stats
    total_trades = len(trades)
    if total_trades == 0:
        return None

    winning_trades = [t for t in trades if t['pnl_dollars'] > 0]
    losing_trades = [t for t in trades if t['pnl_dollars'] <= 0]
    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate = (win_count / total_trades) * 100

    total_pnl = sum(t['pnl_dollars'] for t in trades)
    total_fees = sum(t['fees'] for t in trades)
    avg_win = np.mean([t['pnl_dollars'] for t in winning_trades]) if winning_trades else 0
    avg_loss = np.mean([t['pnl_dollars'] for t in losing_trades]) if losing_trades else 0
    gross_profit = sum(t['pnl_dollars'] for t in winning_trades) if winning_trades else 0
    gross_loss = abs(sum(t['pnl_dollars'] for t in losing_trades)) if losing_trades else 0.01
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Daily stats
    daily_values = list(daily_pnl.values())
    max_daily_loss = min(daily_values) if daily_values else 0
    max_daily_profit = max(daily_values) if daily_values else 0
    avg_daily_pnl = np.mean(daily_values) if daily_values else 0

    # Sharpe ratio (annualized from daily)
    if len(daily_values) > 1 and np.std(daily_values) > 0:
        sharpe = (np.mean(daily_values) / np.std(daily_values)) * np.sqrt(252)
    else:
        sharpe = 0.0

    return {
        'symbol': symbol,
        'contracts': contracts,
        'hard_stop': hard_stop_dollars,
        'ema_fast': ema_fast,
        'ema_slow': ema_slow,
        'stoch_lo': stoch_lo,
        'stoch_hi': stoch_hi,
        'sl_atr_mult': sl_atr_mult,
        'tp_rr': tp_rr,
        'starting_equity': starting_equity,
        'final_equity': equity,
        'total_pnl': total_pnl,
        'total_return_pct': ((equity - starting_equity) / starting_equity) * 100,
        'total_trades': total_trades,
        'win_count': win_count,
        'loss_count': loss_count,
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'avg_trade': total_pnl / total_trades,
        'profit_factor': profit_factor,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'total_fees': total_fees,
        'fee_per_trade': total_fees / total_trades,
        'max_drawdown_pct': max_drawdown_pct,
        'max_drawdown_dollars': max_drawdown_dollars,
        'max_daily_loss': max_daily_loss,
        'max_daily_profit': max_daily_profit,
        'avg_daily_pnl': avg_daily_pnl,
        'sharpe_ratio': sharpe,
        'trading_days': len(daily_pnl),
        'rt_cost_per_trade': rt_cost,
        'equity_curve': equity_curve
    }

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 90)
    print("TASTYTRADE REALISTIC BACKTEST - MNQ & MES PARAMETER SWEEP")
    print("=" * 90)
    print()
    print("Fee Structure (Real Tastytrade 2026):")
    for sym in ['MNQ', 'MES']:
        f = TASTYTRADE_FEES[sym]
        rt_1 = get_round_trip_cost(sym, 1)
        print(f"  {sym}: Commission ${f['commission_per_side']}/side + Exchange ${f['exchange_fee_per_side']}/side + NFA ${f['nfa_fee_per_side']}/side")
        print(f"       Spread: {f['spread_points']} pts, Slippage: {f['slippage_points']} pts/side")
        print(f"       Total cost per 1 contract RT: ${rt_1:.2f}")
    print(f"\n  Starting Equity: $10,000")
    print(f"  Data: 1-min candles, NY session only (9:30-16:00 ET)")
    print()

    # Load data
    data = {}
    for symbol in ['MNQ', 'MES']:
        csv_path = f'data/{symbol.lower()}_1min.csv'
        if not os.path.exists(csv_path):
            print(f"  WARNING: {csv_path} not found, skipping {symbol}")
            continue

        df = pd.read_csv(csv_path)
        df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True).dt.tz_convert('US/Eastern')
        # Filter NY session only
        df = df[(df['timestamp'].dt.hour >= 9) &
                ((df['timestamp'].dt.hour < 16) |
                 ((df['timestamp'].dt.hour == 9) & (df['timestamp'].dt.minute >= 30)))]
        df = df.reset_index(drop=True)
        data[symbol] = df
        print(f"  Loaded {symbol}: {len(df)} bars ({df['timestamp'].iloc[0].date()} to {df['timestamp'].iloc[-1].date()})")

    if not data:
        print("No data files found!")
        return

    print()

    # Parameter sweep
    contract_sizes = [1, 2, 3, 4, 5]
    hard_stops = [15, 25, 40, 60, 80, 100]  # Dollar amounts per trade
    sl_atr_mults = [1.5, 2.0, 2.5]
    tp_rrs = [1.5, 1.8, 2.0, 2.5]

    total_combos = len(data) * len(contract_sizes) * len(hard_stops) * len(sl_atr_mults) * len(tp_rrs)
    print(f"Running {total_combos} parameter combinations...")
    print(f"  Symbols: {list(data.keys())}")
    print(f"  Contracts: {contract_sizes}")
    print(f"  Hard Stops: ${hard_stops}")
    print(f"  ATR SL Multipliers: {sl_atr_mults}")
    print(f"  Take Profit R:R: {tp_rrs}")
    print()

    all_results = []
    count = 0
    start_time = time.time()

    for symbol, df in data.items():
        for contracts in contract_sizes:
            for hard_stop in hard_stops:
                for sl_mult in sl_atr_mults:
                    for tp in tp_rrs:
                        count += 1
                        if count % 50 == 0:
                            elapsed = time.time() - start_time
                            pct = (count / total_combos) * 100
                            print(f"  Progress: {count}/{total_combos} ({pct:.0f}%) - {elapsed:.1f}s elapsed")

                        result = run_backtest(
                            df, symbol, contracts,
                            hard_stop_dollars=hard_stop,
                            sl_atr_mult=sl_mult,
                            tp_rr=tp,
                            starting_equity=10000.0
                        )
                        if result:
                            all_results.append(result)

    elapsed = time.time() - start_time
    print(f"\nCompleted {count} backtests in {elapsed:.1f}s")
    print(f"Valid results: {len(all_results)}")

    if not all_results:
        print("No valid results!")
        return

    # Sort by total P&L
    all_results.sort(key=lambda x: x['total_pnl'], reverse=True)

    # =========================================================================
    # TOP 3 WINNERS
    # =========================================================================
    print()
    print("=" * 90)
    print("TOP 3 WINNING CONFIGURATIONS")
    print("=" * 90)

    for rank, r in enumerate(all_results[:3], 1):
        print(f"\n{'#'*3} RANK #{rank} {'#'*60}")
        print(f"  Symbol: {r['symbol']}  |  Contracts: {r['contracts']}  |  Hard Stop: ${r['hard_stop']}")
        print(f"  EMA: {r['ema_fast']}/{r['ema_slow']}  |  Stoch: {r['stoch_lo']}/{r['stoch_hi']}  |  ATR SL: {r['sl_atr_mult']}x  |  TP R:R: {r['tp_rr']}")
        print(f"  ---------------------------------------------------------------")
        print(f"  Starting Equity:     ${r['starting_equity']:>12,.2f}")
        print(f"  Final Equity:        ${r['final_equity']:>12,.2f}")
        print(f"  Total P&L:           ${r['total_pnl']:>12,.2f}  ({r['total_return_pct']:+.1f}%)")
        print(f"  ---------------------------------------------------------------")
        print(f"  Total Trades:        {r['total_trades']:>8}")
        print(f"  Winning Trades:      {r['win_count']:>8}  ({r['win_rate']:.1f}%)")
        print(f"  Losing Trades:       {r['loss_count']:>8}")
        print(f"  Avg Win:             ${r['avg_win']:>12,.2f}")
        print(f"  Avg Loss:            ${r['avg_loss']:>12,.2f}")
        print(f"  Avg Trade:           ${r['avg_trade']:>12,.2f}")
        print(f"  Profit Factor:       {r['profit_factor']:>12.2f}")
        print(f"  Sharpe Ratio:        {r['sharpe_ratio']:>12.2f}")
        print(f"  ---------------------------------------------------------------")
        print(f"  Max Drawdown:        ${r['max_drawdown_dollars']:>12,.2f}  ({r['max_drawdown_pct']:.1f}%)")
        print(f"  Max Daily Loss:      ${r['max_daily_loss']:>12,.2f}")
        print(f"  Max Daily Profit:    ${r['max_daily_profit']:>12,.2f}")
        print(f"  Avg Daily P&L:       ${r['avg_daily_pnl']:>12,.2f}")
        print(f"  Trading Days:        {r['trading_days']:>8}")
        print(f"  ---------------------------------------------------------------")
        print(f"  Total Fees Paid:     ${r['total_fees']:>12,.2f}")
        print(f"  Fee per Trade:       ${r['fee_per_trade']:>12,.2f}")
        print(f"  RT Cost per Trade:   ${r['rt_cost_per_trade']:>12,.2f}")

    # =========================================================================
    # FULL RESULTS TABLE
    # =========================================================================
    print()
    print("=" * 90)
    print("ALL RESULTS RANKED BY P&L (Top 30)")
    print("=" * 90)
    print(f"{'#':>3} {'Sym':>4} {'Cts':>3} {'HStop':>6} {'ATRx':>5} {'TP':>4} {'Trades':>6} {'WinR':>6} {'PF':>6} {'TotalPnL':>12} {'MaxDD%':>7} {'Fees':>10} {'FinalEq':>12}")
    print("-" * 90)

    for i, r in enumerate(all_results[:30], 1):
        print(f"{i:>3} {r['symbol']:>4} {r['contracts']:>3} ${r['hard_stop']:>4} {r['sl_atr_mult']:>5.1f} {r['tp_rr']:>4.1f} "
              f"{r['total_trades']:>6} {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} "
              f"${r['total_pnl']:>10,.0f} {r['max_drawdown_pct']:>6.1f}% ${r['total_fees']:>8,.0f} ${r['final_equity']:>10,.0f}")

    # =========================================================================
    # BOTTOM 5 (WORST)
    # =========================================================================
    print()
    print("WORST 5 CONFIGURATIONS:")
    print("-" * 90)
    for i, r in enumerate(all_results[-5:], 1):
        print(f"  {r['symbol']} {r['contracts']}ct ${r['hard_stop']}stop ATR{r['sl_atr_mult']}x TP{r['tp_rr']} -> "
              f"{r['total_trades']} trades, {r['win_rate']:.1f}% WR, P&L: ${r['total_pnl']:,.0f}, DD: {r['max_drawdown_pct']:.1f}%")

    # =========================================================================
    # SUMMARY BY SYMBOL AND CONTRACT SIZE
    # =========================================================================
    print()
    print("=" * 90)
    print("BEST CONFIG PER SYMBOL & CONTRACT SIZE")
    print("=" * 90)

    for symbol in data.keys():
        print(f"\n  {symbol}:")
        for cts in contract_sizes:
            symbol_cts_results = [r for r in all_results if r['symbol'] == symbol and r['contracts'] == cts]
            if symbol_cts_results:
                best = symbol_cts_results[0]  # Already sorted by P&L
                print(f"    {cts} contract(s): P&L ${best['total_pnl']:>10,.0f} | {best['total_trades']} trades | "
                      f"{best['win_rate']:.1f}% WR | PF {best['profit_factor']:.2f} | "
                      f"DD {best['max_drawdown_pct']:.1f}% | Stop ${best['hard_stop']} ATR{best['sl_atr_mult']}x TP{best['tp_rr']}")

    # Save results to CSV
    results_df = pd.DataFrame([{k: v for k, v in r.items() if k != 'equity_curve'} for r in all_results])
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs('results', exist_ok=True)
    csv_file = f'results/tastytrade_backtest_{timestamp}.csv'
    results_df.to_csv(csv_file, index=False)
    print(f"\nFull results saved to: {csv_file}")

    # Save equity curves for top 3
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 3, figsize=(18, 6))
        for idx, r in enumerate(all_results[:3]):
            ax = axes[idx]
            curve = r['equity_curve']
            ax.plot(curve, linewidth=0.5)
            ax.set_title(f"#{idx+1}: {r['symbol']} {r['contracts']}ct ${r['hard_stop']}stop\n"
                        f"P&L: ${r['total_pnl']:,.0f} | WR: {r['win_rate']:.1f}%", fontsize=10)
            ax.set_ylabel('Equity ($)')
            ax.axhline(y=10000, color='gray', linestyle='--', alpha=0.5)
            ax.grid(True, alpha=0.3)

        plt.tight_layout()
        chart_file = f'results/tastytrade_top3_equity_{timestamp}.png'
        plt.savefig(chart_file, dpi=150)
        plt.close()
        print(f"Equity curves saved to: {chart_file}")
    except Exception as e:
        print(f"Could not save equity chart: {e}")

    print()
    print("=" * 90)
    print("BACKTEST COMPLETE")
    print("=" * 90)

if __name__ == "__main__":
    main()
