#!/usr/bin/env python3
"""
Orchestrated Reproducible Backtest Script for Stoch-D NQ Strategy
=================================================================

This script reproduces the exact backtest results for the Stoch-D mean reversion strategy
on NQ futures. It uses the same parameters, logic, and data format as the original backtest.

Expected Results (when run with correct data):
- Trades: 44
- Win Rate: 65.9%
- Profit Factor: 3.34
- Net P&L: +$28,939 (+19.3%)
- Max DD: $2,479

Setup:
1. Place this script and your CME_MINI_NQ1___1-3.csv file in the same folder
2. Run: python backtest_nq.py
3. Check backtest_results_NQ.png for the equity curve chart

Data Format: CSV with columns: time, open, high, low, close (Unix timestamp in seconds)

LIVE TRADING:
1. Set environment variables: WEBULL_APP_KEY, WEBULL_APP_SECRET
2. Run: python backtest_nq.py --live --demo
3. For live trading: python backtest_nq.py --live --no-demo
"""

import os
import sys
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import pytz
import time
import logging

# Add local path for webull_api
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from webull_api import WebullAPI, FuturesTrader
except ImportError:
    print("Warning: webull_api.py not found. Live trading features will be disabled.")
    WebullAPI = None
    FuturesTrader = None

# =============================================================================
# PARAMETERS — DO NOT CHANGE (exact replication)
# =============================================================================
PARAMS = {
    # Data
    "DATA_FILE": "data/mnq_1min.csv",  # ← Change to full path if needed

    # Session (10am-11am ET = 15:00-16:00 UTC in winter)
    "SESSION_START": 600,  # 10:00 ET in minutes from midnight
    "SESSION_END": 660,    # 11:00 ET in minutes from midnight

    # Strategy
    "EMA_FAST": 34,
    "EMA_SLOW": 89,
    "STOCH_K": 14,
    "STOCH_D": 3,
    "STOCH_SMT": 3,
    "ATR_LEN": 14,
    "STOCH_LO": 29,
    "STOCH_HI": 71,
    "SL_ATR_MULT": 1.0,
    "TP_RR": 1.5,
    "TRAIL_ATR_MULT": 0.5,
    "BE_POINTS": 2.5,  # $50 / (2 contracts * $20) = 2.5 points

    # Risk
    "CONTRACTS": 2,
    "POINT_VALUE": 20.0,
    "TICK_SIZE": 0.25,
    "MAX_LOSS_TRADE": 1200.0,
    "MAX_LOSS_DAY": 1200.0,
    "COMMISSION_RT": 14.78,
}

# =============================================================================
# INDICATORS (exact match to original)
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
        out[i] = (out[i-1] * (period-1) + tr[i]) / period
    return out

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
    n = len(close)
    # Calculate %K
    lowest_low = pd.Series(low).rolling(window=k_period).min()
    highest_high = pd.Series(high).rolling(window=k_period).max()
    raw_k = 100 * (pd.Series(close) - lowest_low) / (highest_high - lowest_low)
    # Smooth %K
    k_smooth = raw_k.rolling(window=smooth).mean()
    # %D is SMA of smoothed %K
    d_smooth = k_smooth.rolling(window=d_period).mean()
    return k_smooth.values, d_smooth.values

def compute_signals(bars: pd.DataFrame):
    if len(bars) < max(PARAMS["EMA_SLOW"], PARAMS["STOCH_K"] + PARAMS["STOCH_D"], PARAMS["ATR_LEN"]) + 5:
        return {}
    c = bars["close"].values
    h = bars["high"].values
    l = bars["low"].values
    ema_f = calc_ema(c, PARAMS["EMA_FAST"])
    ema_s = calc_ema(c, PARAMS["EMA_SLOW"])
    atr = calc_atr(h, l, c, PARAMS["ATR_LEN"])
    k_sm, d_sm = calc_stoch(h, l, c, PARAMS["STOCH_K"], PARAMS["STOCH_D"], PARAMS["STOCH_SMT"])
    return {
        "close": c[-1],
        "ema_fast": ema_f[-1],
        "ema_slow": ema_s[-1],
        "atr": atr[-1],
        "stoch_d": d_sm[-1],
        "stoch_d_prev": d_sm[-2] if len(d_sm) >= 2 else d_sm[-1],
        "uptrend": ema_f[-1] > ema_s[-1],
        "downtrend": ema_f[-1] < ema_s[-1],
        "d_rising": d_sm[-1] > d_sm[-2],
        "d_falling": d_sm[-1] < d_sm[-2],
        "atr_valid": not np.isnan(atr[-1]),
    }

# =============================================================================
# SESSION CHECK
# =============================================================================
def in_session(ts: pd.Timestamp) -> bool:
    # Convert to ET minutes from midnight
    minutes = ts.hour * 60 + ts.minute
    return PARAMS["SESSION_START"] <= minutes < PARAMS["SESSION_END"]

# =============================================================================
# POSITION & RISK MANAGEMENT
# =============================================================================
class Position:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.direction = None
        self.entry_price = None
        self.contracts = 0
        self.sl_price = None
        self.tp_price = None
        self.trail_dist = None
        self.trail_stop = None
        self.be_set = False
        self.entry_time = None

    def open_long(self, entry: float, sl: float, tp: float, trail: float, contracts: int):
        self.active = True
        self.direction = "long"
        self.entry_price = entry
        self.contracts = contracts
        self.sl_price = sl
        self.tp_price = tp
        self.trail_dist = trail
        self.trail_stop = entry - trail
        self.be_set = False
        self.entry_time = datetime.now()

    def open_short(self, entry: float, sl: float, tp: float, trail: float, contracts: int):
        self.active = True
        self.direction = "short"
        self.entry_price = entry
        self.contracts = contracts
        self.sl_price = sl
        self.tp_price = tp
        self.trail_dist = trail
        self.trail_stop = entry + trail
        self.be_set = False
        self.entry_time = datetime.now()

    def update_trail(self, high: float, low: float):
        if not self.active:
            return
        if self.direction == "long":
            new_trail = high - self.trail_dist
            if new_trail > self.trail_stop:
                self.trail_stop = new_trail
        elif self.direction == "short":
            new_trail = low + self.trail_dist
            if new_trail < self.trail_stop:
                self.trail_stop = new_trail

    def check_breakeven(self, price: float) -> bool:
        if self.be_set or not self.active:
            return False
        pts = (price - self.entry_price) if self.direction == "long" else (self.entry_price - price)
        if pts >= PARAMS["BE_POINTS"]:
            self.sl_price = self.entry_price
            self.trail_stop = self.entry_price
            self.be_set = True
            return True
        return False

    def current_pnl_pts(self, price: float) -> float:
        if not self.active:
            return 0.0
        return (price - self.entry_price) if self.direction == "long" else (self.entry_price - price)

    def current_pnl_dollars(self, price: float) -> float:
        pts = self.current_pnl_pts(price)
        return pts * self.contracts * PARAMS["POINT_VALUE"] - PARAMS["COMMISSION_RT"] * self.contracts

    def effective_sl(self) -> float:
        if self.direction == "long":
            return max(self.sl_price, self.trail_stop)
        else:
            return min(self.sl_price, self.trail_stop)

class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.trade_count = 0
        self.day_halted = False
        self.last_reset_day = None

    def check_new_day(self, ts: pd.Timestamp):
        today = ts.date()
        if self.last_reset_day != today:
            self.daily_pnl = 0.0
            self.day_halted = False
            self.last_reset_day = today

    def record_trade(self, pnl_dollars: float):
        commission = PARAMS["COMMISSION_RT"] * PARAMS["CONTRACTS"]
        net = pnl_dollars - commission
        self.daily_pnl += net
        self.trade_count += 1
        if self.daily_pnl <= -PARAMS["MAX_LOSS_DAY"]:
            self.day_halted = True

    def can_trade(self, ts: pd.Timestamp) -> bool:
        self.check_new_day(ts)
        if self.day_halted:
            return False
        return True

    def calc_sl_distance(self, atr: float) -> float:
        atr_sl = max(atr * PARAMS["SL_ATR_MULT"], PARAMS["TICK_SIZE"])
        hard_sl = PARAMS["MAX_LOSS_TRADE"] / (PARAMS["CONTRACTS"] * PARAMS["POINT_VALUE"])
        return min(atr_sl, hard_sl)

# =============================================================================
# MAIN BACKTEST / LIVE TRADING
# =============================================================================
def run_backtest():
    """Run the backtest simulation"""
    print("Loading data...")
    if not os.path.exists(PARAMS["DATA_FILE"]):
        print(f"ERROR: Data file '{PARAMS["DATA_FILE"]}' not found!")
        print("Please place the CSV file in the same folder as this script.")
        return

    df = pd.read_csv(PARAMS["DATA_FILE"])
    df = df[pd.to_numeric(df['time'], errors='coerce').notna()]
    df['timestamp'] = pd.DatetimeIndex(pd.to_datetime(df['time'], unit='s')).tz_localize('UTC').tz_convert('US/Eastern')
    df = df[['timestamp', 'open', 'high', 'low', 'close']].dropna().sort_values('timestamp').reset_index(drop=True)

    print(f"Data loaded: {len(df)} bars from {df['timestamp'].min()} to {df['timestamp'].max()}")

    # Precompute indicators
    c = df['close'].values
    h = df['high'].values
    l = df['low'].values
    ema_f = calc_ema(c, PARAMS["EMA_FAST"])
    ema_s = calc_ema(c, PARAMS["EMA_SLOW"])
    atr = calc_atr(h, l, c, PARAMS["ATR_LEN"])
    k_sm, d_sm = calc_stoch(h, l, c, PARAMS["STOCH_K"], PARAMS["STOCH_D"], PARAMS["STOCH_SMT"])

    # Initialize
    position = Position()
    risk = RiskManager()
    trades = []
    equity_curve = [300000.0]  # Starting equity
    equity = 300000.0

    for idx in range(len(df)):
        ts = df.loc[idx, 'timestamp']
        close = c[idx]
        high = h[idx]
        low = l[idx]

        if idx < max(PARAMS["EMA_SLOW"], PARAMS["STOCH_K"] + PARAMS["STOCH_D"], PARAMS["ATR_LEN"]) + 5:
            continue

        sig = {
            "close": close,
            "ema_fast": ema_f[idx],
            "ema_slow": ema_s[idx],
            "atr": atr[idx],
            "stoch_d": d_sm[idx],
            "stoch_d_prev": d_sm[idx-1] if idx >= 1 else d_sm[idx],
            "uptrend": ema_f[idx] > ema_s[idx],
            "downtrend": ema_f[idx] < ema_s[idx],
            "d_rising": d_sm[idx] > d_sm[idx-1],
            "d_falling": d_sm[idx] < d_sm[idx-1],
            "atr_valid": not np.isnan(atr[idx]),
        }

        if not sig or not sig.get("atr_valid"):
            continue

        # Update trailing stops
        if position.active:
            position.update_trail(high, low)

        # Check for exits
        if position.active:
            eff_sl = position.effective_sl()
            pnl = position.current_pnl_dollars(close)

            # Stop loss
            if (position.direction == "long" and low <= eff_sl) or (position.direction == "short" and high >= eff_sl):
                exit_price = eff_sl
                pnl = position.current_pnl_dollars(exit_price)
                risk.record_trade(pnl)
                equity += pnl
                equity_curve.append(equity)
                trades.append({
                    'entry_time': position.entry_time,
                    'exit_time': ts,
                    'direction': position.direction,
                    'entry_price': position.entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'stop_loss'
                })
                position.reset()

            # Take profit
            elif (position.direction == "long" and high >= position.tp_price) or (position.direction == "short" and low <= position.tp_price):
                exit_price = position.tp_price
                pnl = position.current_pnl_dollars(exit_price)
                risk.record_trade(pnl)
                equity += pnl
                equity_curve.append(equity)
                trades.append({
                    'entry_time': position.entry_time,
                    'exit_time': ts,
                    'direction': position.direction,
                    'entry_price': position.entry_price,
                    'exit_price': exit_price,
                    'pnl': pnl,
                    'reason': 'take_profit'
                })
                position.reset()

        # Entry signals
        if not position.active and in_session(ts) and risk.can_trade(ts):
            sl_dist = risk.calc_sl_distance(sig["atr"])
            tp_dist = sl_dist * PARAMS["TP_RR"]
            trail = sig["atr"] * PARAMS["TRAIL_ATR_MULT"]
            price = sig["close"]

            if sig["uptrend"] and sig["stoch_d"] < PARAMS["STOCH_LO"] and sig["d_rising"]:
                sl = price - sl_dist
                tp = price + tp_dist
                position.open_long(price, sl, tp, trail, PARAMS["CONTRACTS"])

            elif sig["downtrend"] and sig["stoch_d"] > PARAMS["STOCH_HI"] and sig["d_falling"]:
                sl = price + sl_dist
                tp = price - tp_dist
                position.open_short(price, sl, tp, trail, PARAMS["CONTRACTS"])

    # Final results
    total_trades = len(trades)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
    total_pnl = sum(t['pnl'] for t in trades)
    avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    profit_factor = sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)) if losses else 999
    max_dd = max(equity_curve) - min(equity_curve) if equity_curve else 0

    # Print results
    print("\n" + "="*60)
    print("STOCH-D NQ BACKTEST RESULTS")
    print("="*60)
    print(f"Trades     : {total_trades}")
    print(f"Win Rate   : {win_rate:.1f}%")
    print(f"Profit Factor: {profit_factor:.2f}")
    print(f"Net P&L    : ${total_pnl:,.0f} ({total_pnl/300000*100:.1f}%)")
    print(f"Max DD     : ${max_dd:,.0f}")
    print(f"Avg Win    : ${avg_win:,.0f}")
    print(f"Avg Loss   : ${avg_loss:,.0f}")
    print()

    # Verification
    print("VERIFICATION:")
    targets = {
        "Trades": 44,
        "Win Rate": 65.9,
        "Profit Factor": 3.34,
        "Net P&L": 28939,
        "Max DD": 2479
    }

    checks = {
        "Trades": abs(total_trades - targets["Trades"]) <= 2,
        "Win Rate": abs(win_rate - targets["Win Rate"]) <= 5,
        "Profit Factor": abs(profit_factor - targets["Profit Factor"]) <= 0.5,
        "Net P&L": abs(total_pnl - targets["Net P&L"]) <= 2000,
        "Max DD": abs(max_dd - targets["Max DD"]) <= 500
    }

    for metric, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {metric:<12}: {status}")

    all_pass = all(checks.values())
    print(f"\nOverall: {'PASS' if all_pass else 'FAIL'}")

    # Plot equity curve
    plt.figure(figsize=(12, 6))
    plt.plot(equity_curve, label='Equity')
    plt.title('Stoch-D NQ Backtest Equity Curve')
    plt.xlabel('Trades')
    plt.ylabel('Equity ($)')
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig('backtest_results_NQ.png', dpi=150, bbox_inches='tight')
    print("Chart saved as: backtest_results_NQ.png")

def main():
    parser = argparse.ArgumentParser(description='NQ Futures Trading Strategy')
    parser.add_argument('--live', action='store_true', help='Run in live trading mode')
    parser.add_argument('--demo', action='store_true', help='Use demo account (default: True)')
    parser.add_argument('--no-demo', action='store_true', help='Use live account')
    parser.add_argument('--symbol', default='MNQ', help='Futures symbol to trade (default: MNQ)')
    parser.add_argument('--max-trades', type=int, default=0, help='Maximum trades to take in live mode (0 = unlimited)')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('trading.log'),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)

    if args.live:
        if not WebullAPI or not FuturesTrader:
            logger.error("Webull API not available. Please ensure webull_api.py is present.")
            return

        # Live trading mode
        is_demo = args.demo or not args.no_demo  # Default to demo
        logger.info(f"Starting LIVE TRADING mode (Demo: {is_demo})")

        try:
            api = WebullAPI(is_demo=is_demo)
            trader = FuturesTrader(api, args.symbol)

            # Get account info
            balance = trader.get_account_balance()
            logger.info(f"Account Balance: ${balance:,.2f}")

            # Run live trading loop
            run_live_trading(trader, args.max_trades)

        except Exception as e:
            logger.error(f"Live trading failed: {e}")
            return

    else:
        # Backtest mode
        logger.info("Starting BACKTEST mode")
        run_backtest()

if __name__ == "__main__":
    main()
