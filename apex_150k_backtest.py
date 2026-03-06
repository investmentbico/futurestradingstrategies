#!/usr/bin/env python3
"""
Apex Trader Funding 150K Account Backtest
==========================================
Simulates our MNQ strategy against Apex prop firm rules:
- Starting balance: $150,000
- Profit target: $9,000
- Trailing drawdown: $4,000 (intraday trailing threshold)
- Max contracts: 12 mini = 120 MNQ micros
- Consistency rule: best single day < 30% of total profit
- Data: Last 3 months (Dec 2025 - Feb 2026)

Strategy: EMA 21/55 + Stochastic D + ATR (same as live bot)
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# APEX 150K ACCOUNT RULES
# =============================================================================
APEX_RULES = {
    "starting_balance": 150_000,
    "profit_target": 9_000,
    "trailing_drawdown": 4_000,
    "max_mnq_contracts": 120,       # 12 mini = 120 micro
    "consistency_pct": 0.30,        # best day < 30% of total profit
    "safety_net": 150_100,          # trailing stops at starting + $100
}

# =============================================================================
# STRATEGY PARAMETERS (matching live bot)
# =============================================================================
STRATEGY_PARAMS = {
    "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
    "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
}

MNQ_POINT_VALUE = 20   # $20 per point for MNQ
MNQ_TICK_SIZE = 0.25
MNQ_TICK_VALUE = 5.0    # $5 per tick

# =============================================================================
# INDICATORS
# =============================================================================

def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period-1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
    lowest_low = np.array([np.min(low[i-k_period+1:i+1]) for i in range(k_period-1, len(low))])
    highest_high = np.array([np.max(high[i-k_period+1:i+1]) for i in range(k_period-1, len(high))])
    denom = highest_high - lowest_low
    denom[denom == 0] = 1  # avoid division by zero
    k = 100 * (close[k_period-1:] - lowest_low) / denom
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    d = calc_ema(k[~np.isnan(k)], d_period)
    d_full = np.full(len(k), np.nan)
    d_full[~np.isnan(k)] = d
    d_smooth = calc_ema(d_full[~np.isnan(d_full)], smooth)
    d_smooth_full = np.full(len(d_full), np.nan)
    d_smooth_full[~np.isnan(d_full)] = d_smooth
    return k, d_smooth_full

# =============================================================================
# APEX TRAILING DRAWDOWN TRACKER
# =============================================================================

class ApexDrawdownTracker:
    """Tracks Apex intraday trailing drawdown threshold"""

    def __init__(self, starting_balance, trailing_amount, safety_net):
        self.starting_balance = starting_balance
        self.trailing_amount = trailing_amount
        self.safety_net = safety_net  # trailing stops at this level
        self.balance = starting_balance
        self.peak_balance = starting_balance
        self.drawdown_threshold = starting_balance - trailing_amount  # $146,000
        self.threshold_locked = False
        self.blown = False

    def update(self, new_balance):
        """Update balance and trailing threshold. Returns True if account blown."""
        self.balance = new_balance

        # Update peak and trailing threshold
        if new_balance > self.peak_balance:
            self.peak_balance = new_balance
            if not self.threshold_locked:
                new_threshold = new_balance - self.trailing_amount
                # Threshold stops trailing at safety_net ($150,100)
                if new_threshold >= self.safety_net:
                    self.drawdown_threshold = self.safety_net
                    self.threshold_locked = True
                else:
                    self.drawdown_threshold = new_threshold

        # Check if account is blown
        if self.balance <= self.drawdown_threshold:
            self.blown = True
            return True
        return False

    def remaining_drawdown(self):
        return self.balance - self.drawdown_threshold

# =============================================================================
# BACKTEST ENGINE WITH APEX RULES
# =============================================================================

def run_apex_backtest(contracts: int, hard_stop: float, data_file: str = 'data/mnq_1min.csv'):
    """Run backtest simulating Apex 150K prop firm rules"""

    if contracts > APEX_RULES["max_mnq_contracts"]:
        print(f"WARNING: {contracts} contracts exceeds Apex 150K max of {APEX_RULES['max_mnq_contracts']} MNQ")
        contracts = APEX_RULES["max_mnq_contracts"]

    # Load data
    df = pd.read_csv(data_file)
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    # Filter last 3 months
    end_date = df['timestamp'].max()
    start_date = end_date - pd.DateOffset(months=3)
    df = df[df['timestamp'] >= start_date].reset_index(drop=True)

    # Filter NY session hours (9:30 AM - 4:00 PM ET)
    df = df[(df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute >= 570) &  # 9:30
            (df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute <= 960)]    # 16:00
    df = df.reset_index(drop=True)

    print(f"  Data: {start_date.date()} to {end_date.date()}")
    print(f"  Bars: {len(df)} (NY session only)")

    # Calculate indicators
    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)

    df['ema_fast'] = calc_ema(closes, STRATEGY_PARAMS["EMA_FAST"])
    df['ema_slow'] = calc_ema(closes, STRATEGY_PARAMS["EMA_SLOW"])
    df['atr'] = calc_atr(highs, lows, closes, STRATEGY_PARAMS["ATR_LEN"])
    k_sm, d_sm = calc_stoch(highs, lows, closes, STRATEGY_PARAMS["STOCH_K"],
                          STRATEGY_PARAMS["STOCH_D"], STRATEGY_PARAMS["STOCH_SMT"])
    df['stoch_k'] = k_sm
    df['stoch_d'] = d_sm

    # Signals
    df['uptrend'] = df['ema_fast'] > df['ema_slow']
    df['downtrend'] = df['ema_fast'] < df['ema_slow']
    df['d_falling'] = df['stoch_d'] < df['stoch_d'].shift(1)
    df['d_rising'] = df['stoch_d'] > df['stoch_d'].shift(1)

    # Initialize Apex tracker
    apex = ApexDrawdownTracker(
        APEX_RULES["starting_balance"],
        APEX_RULES["trailing_drawdown"],
        APEX_RULES["safety_net"]
    )

    # Trading variables
    position = 0
    entry_price = 0
    stop_loss = 0
    take_profit = 0

    trades = []
    cumulative_pnl = 0
    cumulative_pnl_history = []
    daily_pnls = {}
    daily_pnl = 0
    daily_reset_date = df['timestamp'].iloc[0].date()
    equity_curve = []
    target_hit = False
    target_hit_day = None
    days_to_target = 0
    account_blown = False
    blown_day = None

    # Trading loop
    for idx in range(55, len(df)):  # start after indicator warmup
        row = df.iloc[idx]
        current_price = float(row['close'])
        bar_high = float(row['high'])
        bar_low = float(row['low'])

        # Reset daily P&L
        current_date = row['timestamp'].date()
        if current_date != daily_reset_date:
            daily_pnls[daily_reset_date] = daily_pnl
            daily_pnl = 0
            daily_reset_date = current_date

        # Track equity each bar
        equity_curve.append({
            'timestamp': row['timestamp'],
            'balance': apex.balance,
            'drawdown_threshold': apex.drawdown_threshold,
            'pnl': cumulative_pnl
        })

        # Stop trading if account blown
        if account_blown:
            continue

        # Stop trading if profit target reached
        if target_hit:
            continue

        # Entry signals (only when flat)
        if position == 0 and not np.isnan(row['atr']):
            # Long entry
            if (row['uptrend'] and row['d_falling'] and
                row['stoch_d'] <= STRATEGY_PARAMS["STOCH_LO"]):

                position = contracts
                entry_price = current_price
                atr_value = float(row['atr'])
                stop_loss = current_price - (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"])
                take_profit = current_price + (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

                trades.append({
                    'entry_time': row['timestamp'],
                    'entry_price': entry_price,
                    'direction': 'long',
                    'contracts': contracts,
                    'hard_stop': hard_stop,
                    'atr': atr_value
                })

            # Short entry
            elif (row['downtrend'] and row['d_rising'] and
                  row['stoch_d'] >= STRATEGY_PARAMS["STOCH_HI"]):

                position = -contracts
                entry_price = current_price
                atr_value = float(row['atr'])
                stop_loss = current_price + (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"])
                take_profit = current_price - (atr_value * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

                trades.append({
                    'entry_time': row['timestamp'],
                    'entry_price': entry_price,
                    'direction': 'short',
                    'contracts': contracts,
                    'hard_stop': hard_stop,
                    'atr': atr_value
                })

        # Exit logic
        elif position != 0:
            exit_reason = None
            exit_price = current_price

            if position > 0:  # Long
                hard_stop_price = entry_price - (hard_stop / (contracts * MNQ_POINT_VALUE))

                # Check using bar high/low for more realistic fills
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
                hard_stop_price = entry_price + (hard_stop / (contracts * MNQ_POINT_VALUE))

                if bar_high >= hard_stop_price:
                    exit_price = hard_stop_price
                    exit_reason = 'hard_stop'
                elif bar_high >= stop_loss:
                    exit_price = stop_loss
                    exit_reason = 'atr_stop'
                elif bar_low <= take_profit:
                    exit_price = take_profit
                    exit_reason = 'take_profit'

            if exit_reason:
                # Calculate P&L
                if position > 0:
                    pnl = (exit_price - entry_price) * abs(position) * MNQ_POINT_VALUE
                else:
                    pnl = (entry_price - exit_price) * abs(position) * MNQ_POINT_VALUE

                # Fees: $14.78 RT routing + $0.02/contract commission + slippage
                commissions = 14.78 + (abs(position) * 0.02)
                slippage = abs(position) * MNQ_TICK_VALUE * 0.5 * 2  # 0.5 tick each side
                pnl -= (commissions + slippage)

                # Record trade
                trades[-1].update({
                    'exit_time': row['timestamp'],
                    'exit_price': exit_price,
                    'pnl': round(pnl, 2),
                    'exit_reason': exit_reason
                })

                cumulative_pnl += pnl
                daily_pnl += pnl
                cumulative_pnl_history.append(cumulative_pnl)

                # Update Apex account balance
                new_balance = APEX_RULES["starting_balance"] + cumulative_pnl
                blown = apex.update(new_balance)

                if blown:
                    account_blown = True
                    blown_day = current_date
                    print(f"  *** ACCOUNT BLOWN on {current_date} ***")
                    print(f"      Balance: ${new_balance:,.2f}, Threshold: ${apex.drawdown_threshold:,.2f}")

                # Check profit target
                if cumulative_pnl >= APEX_RULES["profit_target"] and not target_hit:
                    target_hit = True
                    target_hit_day = current_date
                    trading_dates = sorted(daily_pnls.keys())
                    days_to_target = len(trading_dates) + 1  # +1 for current day

                # Reset position
                position = 0
                entry_price = 0
                stop_loss = 0
                take_profit = 0

    # Record last day
    daily_pnls[daily_reset_date] = daily_pnl

    # =============================================================================
    # CALCULATE RESULTS
    # =============================================================================
    completed_trades = [t for t in trades if 'pnl' in t]
    total_trades = len(completed_trades)
    winning_trades = [t for t in completed_trades if t['pnl'] > 0]
    losing_trades = [t for t in completed_trades if t['pnl'] <= 0]
    win_count = len(winning_trades)
    loss_count = len(losing_trades)
    win_rate = win_count / total_trades * 100 if total_trades > 0 else 0

    avg_win = np.mean([t['pnl'] for t in winning_trades]) if winning_trades else 0
    avg_loss = np.mean([t['pnl'] for t in losing_trades]) if losing_trades else 0

    total_wins = sum(t['pnl'] for t in winning_trades)
    total_losses = abs(sum(t['pnl'] for t in losing_trades))
    profit_factor = total_wins / total_losses if total_losses > 0 else float('inf')

    # Max drawdown (from cumulative PnL)
    max_drawdown = 0
    if cumulative_pnl_history:
        peak = cumulative_pnl_history[0]
        for pnl in cumulative_pnl_history:
            if pnl > peak:
                peak = pnl
            dd = peak - pnl
            if dd > max_drawdown:
                max_drawdown = dd

    max_daily_drawdown = abs(min(daily_pnls.values())) if daily_pnls else 0
    best_day_pnl = max(daily_pnls.values()) if daily_pnls else 0
    trading_days = len([d for d, p in daily_pnls.items() if p != 0])

    # Consistency check
    consistency_pass = True
    if cumulative_pnl > 0:
        consistency_pass = best_day_pnl <= (cumulative_pnl * APEX_RULES["consistency_pct"])

    # Exit type breakdown
    hard_stops = len([t for t in completed_trades if t.get('exit_reason') == 'hard_stop'])
    atr_stops = len([t for t in completed_trades if t.get('exit_reason') == 'atr_stop'])
    take_profits = len([t for t in completed_trades if t.get('exit_reason') == 'take_profit'])

    # Streak analysis
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    streak_type = None
    for t in completed_trades:
        if t['pnl'] > 0:
            if streak_type == 'win':
                current_streak += 1
            else:
                current_streak = 1
                streak_type = 'win'
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if streak_type == 'loss':
                current_streak += 1
            else:
                current_streak = 1
                streak_type = 'loss'
            max_loss_streak = max(max_loss_streak, current_streak)

    return {
        'contracts': contracts,
        'hard_stop': hard_stop,
        'total_trades': total_trades,
        'win_count': win_count,
        'loss_count': loss_count,
        'win_rate': win_rate,
        'total_pnl': cumulative_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_factor': profit_factor,
        'max_drawdown': max_drawdown,
        'max_daily_drawdown': max_daily_drawdown,
        'best_day_pnl': best_day_pnl,
        'trading_days': trading_days,
        'hard_stops': hard_stops,
        'atr_stops': atr_stops,
        'take_profits': take_profits,
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'target_hit': target_hit,
        'target_hit_day': target_hit_day,
        'days_to_target': days_to_target,
        'account_blown': account_blown,
        'blown_day': blown_day,
        'consistency_pass': consistency_pass,
        'start_date': start_date,
        'end_date': end_date,
        'trades': completed_trades,
        'daily_pnls': daily_pnls,
        'equity_curve': equity_curve,
        'apex_final_balance': apex.balance,
        'apex_threshold': apex.drawdown_threshold,
        'apex_threshold_locked': apex.threshold_locked,
    }

# =============================================================================
# MAIN
# =============================================================================

def main():
    print("=" * 80)
    print("APEX TRADER FUNDING 150K ACCOUNT BACKTEST")
    print("=" * 80)
    print(f"Account Rules:")
    print(f"  Starting Balance:   ${APEX_RULES['starting_balance']:,}")
    print(f"  Profit Target:      ${APEX_RULES['profit_target']:,}")
    print(f"  Trailing Drawdown:  ${APEX_RULES['trailing_drawdown']:,}")
    print(f"  Max MNQ Contracts:  {APEX_RULES['max_mnq_contracts']}")
    print(f"  Consistency Rule:   Best day < {APEX_RULES['consistency_pct']*100:.0f}% of total profit")
    print(f"\nStrategy: EMA {STRATEGY_PARAMS['EMA_FAST']}/{STRATEGY_PARAMS['EMA_SLOW']}, "
          f"Stoch {STRATEGY_PARAMS['STOCH_LO']}/{STRATEGY_PARAMS['STOCH_HI']}, "
          f"ATR {STRATEGY_PARAMS['ATR_LEN']}, "
          f"SL {STRATEGY_PARAMS['SL_ATR_MULT']}x ATR, "
          f"TP {STRATEGY_PARAMS['TP_RR']}x R:R")
    print(f"Data: Last 3 months, MNQ 1-min bars, NY session only")
    print("=" * 80)

    # Test configurations matching what we've been running
    configs = [
        (3, 25, "Conservative (3 contracts, $25 hard stop)"),
        (5, 50, "Moderate (5 contracts, $50 hard stop)"),
        (10, 100, "Aggressive (10 contracts, $100 hard stop)"),
        (10, 400, "Live Bot Config (10 contracts, $400 hard stop)"),
    ]

    all_results = []

    for contracts, hard_stop, label in configs:
        print(f"\n{'─' * 80}")
        print(f"CONFIG: {label}")
        print(f"{'─' * 80}")

        result = run_apex_backtest(contracts, hard_stop)
        result['label'] = label
        all_results.append(result)

        # Results
        status = "PASSED" if result['target_hit'] and not result['account_blown'] else "BLOWN" if result['account_blown'] else "DID NOT REACH TARGET"
        print(f"\n  STATUS: {status}")
        print(f"  Final P&L: ${result['total_pnl']:,.2f}")
        print(f"  Final Balance: ${result['apex_final_balance']:,.2f}")
        print(f"  Drawdown Threshold: ${result['apex_threshold']:,.2f}")
        print(f"  Threshold Locked: {'Yes' if result['apex_threshold_locked'] else 'No'}")

        if result['target_hit']:
            print(f"  TARGET HIT on {result['target_hit_day']} (Day {result['days_to_target']})")

        if result['account_blown']:
            print(f"  ACCOUNT BLOWN on {result['blown_day']}")

        print(f"\n  Trades: {result['total_trades']} ({result['win_count']}W / {result['loss_count']}L)")
        print(f"  Win Rate: {result['win_rate']:.1f}%")
        print(f"  Profit Factor: {result['profit_factor']:.2f}")
        print(f"  Avg Win: ${result['avg_win']:,.2f}")
        print(f"  Avg Loss: ${result['avg_loss']:,.2f}")
        print(f"  Max Drawdown: ${result['max_drawdown']:,.2f}")
        print(f"  Max Daily Drawdown: ${result['max_daily_drawdown']:,.2f}")
        print(f"  Best Day: ${result['best_day_pnl']:,.2f}")
        print(f"  Trading Days: {result['trading_days']}")

        print(f"\n  Exit Breakdown:")
        print(f"    Hard Stops:   {result['hard_stops']} ({result['hard_stops']/result['total_trades']*100:.1f}%)" if result['total_trades'] > 0 else "    Hard Stops: 0")
        print(f"    ATR Stops:    {result['atr_stops']} ({result['atr_stops']/result['total_trades']*100:.1f}%)" if result['total_trades'] > 0 else "    ATR Stops: 0")
        print(f"    Take Profits: {result['take_profits']} ({result['take_profits']/result['total_trades']*100:.1f}%)" if result['total_trades'] > 0 else "    Take Profits: 0")

        print(f"\n  Streaks: {result['max_win_streak']} max wins, {result['max_loss_streak']} max losses")
        print(f"  Consistency Rule: {'PASS' if result['consistency_pass'] else 'FAIL (best day > 30% of total)'}")

        # Daily P&L breakdown
        print(f"\n  Daily P&L (non-zero days):")
        sorted_days = sorted(result['daily_pnls'].items())
        for day, pnl in sorted_days:
            if pnl != 0:
                marker = ""
                if pnl > 0 and result['total_pnl'] > 0:
                    pct_of_total = (pnl / result['total_pnl']) * 100
                    if pct_of_total > 30:
                        marker = " << CONSISTENCY VIOLATION"
                print(f"    {day}: ${pnl:>+10,.2f}{marker}")

    # =============================================================================
    # SUMMARY COMPARISON
    # =============================================================================
    print(f"\n{'=' * 80}")
    print("APEX 150K BACKTEST COMPARISON SUMMARY")
    print(f"{'=' * 80}")
    print(f"{'Config':<45} {'P&L':>10} {'Status':>12} {'WR':>6} {'PF':>6} {'MaxDD':>10} {'Days':>5}")
    print(f"{'─' * 95}")

    for r in all_results:
        status = "PASSED" if r['target_hit'] and not r['account_blown'] else "BLOWN" if r['account_blown'] else "NO TARGET"
        print(f"{r['label']:<45} ${r['total_pnl']:>9,.0f} {status:>12} "
              f"{r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} ${r['max_drawdown']:>9,.0f} {r['trading_days']:>5}")

    print(f"\nApex 150K Rules: $9,000 target | $4,000 trailing drawdown | 30% consistency")
    print(f"Data period: Last 3 months of MNQ 1-min bars (NY session)")

    # Save results to CSV
    os.makedirs('results', exist_ok=True)
    summary_rows = []
    for r in all_results:
        summary_rows.append({
            'config': r['label'],
            'contracts': r['contracts'],
            'hard_stop': r['hard_stop'],
            'total_pnl': round(r['total_pnl'], 2),
            'target_hit': r['target_hit'],
            'account_blown': r['account_blown'],
            'target_hit_day': r.get('target_hit_day', ''),
            'days_to_target': r['days_to_target'],
            'total_trades': r['total_trades'],
            'win_rate': round(r['win_rate'], 1),
            'profit_factor': round(r['profit_factor'], 2),
            'max_drawdown': round(r['max_drawdown'], 2),
            'max_daily_drawdown': round(r['max_daily_drawdown'], 2),
            'best_day': round(r['best_day_pnl'], 2),
            'consistency_pass': r['consistency_pass'],
            'trading_days': r['trading_days'],
        })

    pd.DataFrame(summary_rows).to_csv('results/apex_150k_backtest_results.csv', index=False)
    print(f"\nResults saved to: results/apex_150k_backtest_results.csv")

if __name__ == "__main__":
    main()
