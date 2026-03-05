#!/usr/bin/env python3
"""
TradersPost MNQ Trading Bot
==============================
Runs the EMA/Stoch/ATR mean-reversion strategy on historical 1-min bars
and sends entry/exit signals to TradersPost.io → Apex/Tradovate account.

Data source: CSV files in data/ (same as backtester) OR live price feed.

Usage:
  # Dry run (no signals sent, strategy only)
  python traderspost_bot.py --dry-run

  # Live signals to TradersPost
  python traderspost_bot.py

  # Custom settings
  python traderspost_bot.py --contracts 2 --symbol MNQ --ticker MNQM2026

Requirements:
  pip install numpy python-dotenv
  .env with TRADERSPOST_WEBHOOK_URL and TRADERSPOST_TICKER
"""

import os
import sys
import json
import time
import signal
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROJECT_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_DIR))

from traderspost_webhook import TradersPostClient

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"traderspost_bot_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
logger = logging.getLogger("tp_bot")

# ── Strategy Parameters (winner from backtests) ─────────────────────
STRATEGY = {
    "EMA_FAST": 21, "EMA_SLOW": 55,
    "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75,
    "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
}

# ── Indicators (same as mnq_live_bot.py / backtest.py) ──────────────
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


# ── Bot ──────────────────────────────────────────────────────────────
class TradersPostBot:
    def __init__(self, tp_client, contracts=3, hard_stop=25.0,
                 max_daily_loss=225.0, max_trades=10, point_value=2.0):
        self.tp = tp_client
        self.contracts = contracts
        self.hard_stop = hard_stop
        self.max_daily_loss = max_daily_loss
        self.max_trades = max_trades
        self.point_value = point_value  # MNQ = $2/point

        # Position tracking (local mirror of broker state)
        self.position = 0       # +N = long, -N = short, 0 = flat
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.trailing_stop = 0.0

        # Stats
        self.total_trades = 0
        self.wins = 0
        self.total_pnl = 0.0
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.trades = []
        self.emergency_stop = False

        # Data
        self.bars = []
        self.max_bars = 200

        # Control
        self.running = False
        self.state_file = PROJECT_DIR / "bot_state.json"

    # ── Indicators ───────────────────────────────────────────────
    def get_indicators(self):
        if len(self.bars) < STRATEGY["EMA_SLOW"] + 5:
            return None
        c = np.array([b['close'] for b in self.bars])
        h = np.array([b['high'] for b in self.bars])
        l = np.array([b['low'] for b in self.bars])

        ema_f = calc_ema(c, STRATEGY["EMA_FAST"])
        ema_s = calc_ema(c, STRATEGY["EMA_SLOW"])
        atr = calc_atr(h, l, c, STRATEGY["ATR_LEN"])
        _, stoch_d = calc_stoch(h, l, c, STRATEGY["STOCH_K"], STRATEGY["STOCH_D"], STRATEGY["STOCH_SMT"])

        f, s, a, d = ema_f[-1], ema_s[-1], atr[-1], stoch_d[-1]
        d_prev = stoch_d[-2] if len(stoch_d) > 1 else d

        if any(np.isnan(x) for x in [f, s, a, d]):
            return None

        return {
            'ema_fast': f, 'ema_slow': s, 'atr': a,
            'stoch_d': d, 'prev_d': d_prev,
            'uptrend': f > s, 'downtrend': f < s,
            'd_falling': d < d_prev, 'd_rising': d > d_prev,
            'price': c[-1],
        }

    # ── Signal Logic ─────────────────────────────────────────────
    def check_entry(self, ind):
        if ind is None or self.position != 0:
            return None
        if self.daily_trades >= self.max_trades or self.emergency_stop:
            return None
        if self.daily_pnl <= -self.max_daily_loss:
            self.emergency_stop = True
            logger.warning(f"DAILY LOSS LIMIT: ${self.daily_pnl:.2f}")
            return None

        if ind['uptrend'] and ind['d_falling'] and ind['stoch_d'] <= STRATEGY["STOCH_LO"]:
            return 'long'
        if ind['downtrend'] and ind['d_rising'] and ind['stoch_d'] >= STRATEGY["STOCH_HI"]:
            return 'short'
        return None

    def check_exit(self, price, ind):
        if self.position == 0:
            return False

        if self.position > 0:
            pnl = (price - self.entry_price) * abs(self.position) * self.point_value
            if pnl <= -self.hard_stop:
                logger.info(f"HARD STOP: P&L=${pnl:.2f}")
                return True
            if price <= self.stop_loss:
                logger.info(f"ATR STOP: {price:.2f} <= SL {self.stop_loss:.2f}")
                return True
            if price >= self.take_profit:
                logger.info(f"TAKE PROFIT: {price:.2f} >= TP {self.take_profit:.2f}")
                return True
            if self.trailing_stop > 0 and price <= self.trailing_stop:
                logger.info(f"TRAIL STOP: {price:.2f} <= {self.trailing_stop:.2f}")
                return True
            # Update trail
            if ind and ind['atr'] > 0:
                new_t = price - ind['atr'] * STRATEGY["SL_ATR_MULT"]
                if new_t > self.trailing_stop:
                    self.trailing_stop = new_t
        else:
            pnl = (self.entry_price - price) * abs(self.position) * self.point_value
            if pnl <= -self.hard_stop:
                logger.info(f"HARD STOP: P&L=${pnl:.2f}")
                return True
            if price >= self.stop_loss:
                logger.info(f"ATR STOP: {price:.2f} >= SL {self.stop_loss:.2f}")
                return True
            if price <= self.take_profit:
                logger.info(f"TAKE PROFIT: {price:.2f} <= TP {self.take_profit:.2f}")
                return True
            if self.trailing_stop > 0 and price >= self.trailing_stop:
                logger.info(f"TRAIL STOP: {price:.2f} >= {self.trailing_stop:.2f}")
                return True
            if ind and ind['atr'] > 0:
                new_t = price + ind['atr'] * STRATEGY["SL_ATR_MULT"]
                if self.trailing_stop == 0 or new_t < self.trailing_stop:
                    self.trailing_stop = new_t
        return False

    # ── Execution via TradersPost ────────────────────────────────
    def enter(self, direction, price, atr):
        qty = self.contracts
        mult = STRATEGY["SL_ATR_MULT"]
        rr = STRATEGY["TP_RR"]

        if direction == 'long':
            self.stop_loss = price - atr * mult
            self.take_profit = price + atr * mult * rr
            sl_amount = atr * mult
            tp_amount = atr * mult * rr
        else:
            self.stop_loss = price + atr * mult
            self.take_profit = price - atr * mult * rr
            sl_amount = atr * mult
            tp_amount = atr * mult * rr

        self.entry_price = price
        self.position = qty if direction == 'long' else -qty
        self.trailing_stop = 0.0

        d = 'LONG' if direction == 'long' else 'SHORT'
        logger.info("=" * 55)
        logger.info(f"{d} ENTRY: {qty}x @ {price:.2f}")
        logger.info(f"  SL: {self.stop_loss:.2f} ({sl_amount:.2f} pts)")
        logger.info(f"  TP: {self.take_profit:.2f} ({tp_amount:.2f} pts)")
        logger.info(f"  ATR: {atr:.2f}")
        logger.info("=" * 55)

        # Send to TradersPost with bracket orders
        if direction == 'long':
            success, result = self.tp.send_long(
                quantity=qty, signal_price=price,
                stop_loss_amount=sl_amount, take_profit_amount=tp_amount,
            )
        else:
            success, result = self.tp.send_short(
                quantity=qty, signal_price=price,
                stop_loss_amount=sl_amount, take_profit_amount=tp_amount,
            )

        if not success:
            logger.error(f"TradersPost signal FAILED: {result}")
            self.position = 0
            return False

        self.daily_trades += 1
        self._write_state()
        return True

    def exit(self, price):
        pv = self.point_value
        if self.position > 0:
            pnl = (price - self.entry_price) * abs(self.position) * pv
            d = "LONG"
        else:
            pnl = (self.entry_price - price) * abs(self.position) * pv
            d = "SHORT"

        logger.info("=" * 55)
        logger.info(f"{d} EXIT: {abs(self.position)}x @ {price:.2f} | P&L: ${pnl:.2f}")
        logger.info("=" * 55)

        success, result = self.tp.send_exit(cancel_orders=True)
        if not success:
            logger.error(f"TradersPost exit FAILED: {result}")

        trade = {
            'time': datetime.now(timezone.utc).isoformat(),
            'dir': d, 'qty': abs(self.position),
            'entry': self.entry_price, 'exit': price, 'pnl': pnl,
        }
        self.trades.append(trade)
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
        self.total_pnl += pnl
        self.daily_pnl += pnl

        # Reset
        self.position = 0
        self.entry_price = 0
        self.stop_loss = 0
        self.take_profit = 0
        self.trailing_stop = 0

        self._write_state()
        return success

    # ── Data Loading ─────────────────────────────────────────────
    def load_csv_bars(self, symbol="mnq", timeframe="1min"):
        """Load historical bars from CSV for indicator warm-up."""
        csv_path = PROJECT_DIR / "data" / f"{symbol}_{timeframe}.csv"
        if not csv_path.exists():
            logger.warning(f"No CSV found: {csv_path}")
            return 0

        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            warmup = min(self.max_bars, len(df))
            df_tail = df.tail(warmup)
            for _, row in df_tail.iterrows():
                self.bars.append({
                    'time': int(row['time']),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                })
            logger.info(f"Loaded {warmup} bars from {csv_path}")
            return warmup
        except Exception as e:
            logger.error(f"CSV load error: {e}")
            return 0

    def add_bar(self, bar):
        """Add a new completed 1-min bar and evaluate strategy."""
        self.bars.append(bar)
        if len(self.bars) > self.max_bars:
            self.bars = self.bars[-self.max_bars:]

        price = bar['close']
        ind = self.get_indicators()

        # Check exits first
        if self.position != 0:
            if self.check_exit(price, ind):
                self.exit(price)
                return

        # Check entries
        if self.position == 0 and ind:
            entry_signal = self.check_entry(ind)
            if entry_signal:
                self.enter(entry_signal, price, ind['atr'])

    # ── State for Dashboard ──────────────────────────────────────
    def _write_state(self):
        try:
            price = self.bars[-1]['close'] if self.bars else 0
            unrealized = 0.0
            if self.position != 0 and price > 0:
                if self.position > 0:
                    unrealized = (price - self.entry_price) * abs(self.position) * self.point_value
                else:
                    unrealized = (self.entry_price - price) * abs(self.position) * self.point_value

            state = {
                'ts': datetime.now(timezone.utc).isoformat(),
                'symbol': self.tp.ticker, 'mode': 'DRY_RUN' if not self.tp.webhook_url else 'LIVE',
                'connected': True,
                'price': price, 'bid': 0, 'ask': 0,
                'position': self.position, 'entry': self.entry_price,
                'sl': self.stop_loss, 'tp': self.take_profit,
                'trail': self.trailing_stop,
                'unrealized_pnl': unrealized,
                'total_trades': self.total_trades, 'wins': self.wins,
                'total_pnl': self.total_pnl, 'daily_pnl': self.daily_pnl,
                'daily_trades': self.daily_trades,
                'bars': len(self.bars),
                'trades': self.trades[-20:],
            }
            self.state_file.write_text(json.dumps(state, indent=2))
        except Exception:
            pass

    # ── Run Modes ────────────────────────────────────────────────
    def run_on_csv(self, symbol="mnq", timeframe="1min"):
        """Run strategy on CSV data (backtest-style, but sends real signals)."""
        csv_path = PROJECT_DIR / "data" / f"{symbol}_{timeframe}.csv"
        if not csv_path.exists():
            logger.error(f"CSV not found: {csv_path}")
            return

        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas required: pip install pandas")
            return

        df = pd.read_csv(csv_path)
        logger.info(f"Running on {len(df)} bars from {csv_path}")

        # Use first N bars as warm-up (no signals)
        warmup = STRATEGY["EMA_SLOW"] + 20
        for i, row in df.iterrows():
            bar = {
                'time': int(row['time']),
                'open': float(row['open']),
                'high': float(row['high']),
                'low': float(row['low']),
                'close': float(row['close']),
            }
            if i < warmup:
                self.bars.append(bar)
                continue

            self.add_bar(bar)

            if i % 100 == 0:
                wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
                logger.info(f"[bar {i}] Price={bar['close']:.2f} | "
                           f"Trades={self.total_trades} WR={wr:.1f}% | P&L=${self.total_pnl:.2f}")

        # Close any open position at end
        if self.position != 0 and self.bars:
            logger.info("End of data - closing open position")
            self.exit(self.bars[-1]['close'])

        self._print_summary()

    def run_live_poll(self, poll_interval=60):
        """Poll for new price data and run strategy.
        Uses Tastytrade/Webull API for price feed, sends signals to TradersPost."""
        logger.info("Starting live polling mode...")
        logger.info(f"Poll interval: {poll_interval}s")

        # Try to get a price source
        price_source = None
        try:
            from tastytrade_api import TastytradeAPI
            api = TastytradeAPI()
            if api.authenticate():
                price_source = ('tastytrade', api)
                logger.info("Price source: Tastytrade API")
        except Exception:
            pass

        if not price_source:
            try:
                from webull_api import WebullAPI
                api = WebullAPI()
                price_source = ('webull', api)
                logger.info("Price source: Webull API")
            except Exception:
                pass

        if not price_source:
            logger.error("No price source available. Need Tastytrade or Webull API credentials.")
            logger.info("Alternative: run with CSV data using --csv mode")
            return

        # Load warm-up bars from CSV
        self.load_csv_bars()

        self.running = True
        last_price = 0
        bar_start = 0

        while self.running:
            try:
                # Get current price
                source_name, api = price_source
                if source_name == 'tastytrade':
                    price = api.get_current_price('MNQ')
                else:
                    price = api.get_current_price('MNQ')

                if not price or price <= 0:
                    time.sleep(poll_interval)
                    continue

                # Build 1-min bar
                now = int(time.time())
                current_bar_start = (now // 60) * 60

                if current_bar_start != bar_start:
                    # New minute - finalize previous bar if exists
                    if bar_start > 0 and last_price > 0:
                        # We only have close prices from polling, simulate OHLC
                        bar = {
                            'time': bar_start,
                            'open': last_price,
                            'high': max(last_price, price),
                            'low': min(last_price, price),
                            'close': price,
                        }
                        self.add_bar(bar)

                        if len(self.bars) % 5 == 0:
                            pos = f"{'LONG' if self.position > 0 else 'SHORT'} {abs(self.position)}x" if self.position != 0 else "FLAT"
                            logger.info(f"[{len(self.bars)} bars] {price:.2f} | {pos} | "
                                       f"Daily=${self.daily_pnl:.2f} | Trades={self.daily_trades}")

                    bar_start = current_bar_start

                last_price = price

                # Check exits between bars if in position
                if self.position != 0:
                    ind = self.get_indicators()
                    if self.check_exit(price, ind):
                        self.exit(price)

                # Daily reset
                now_et = datetime.now(timezone(timedelta(hours=-5)))
                if now_et.hour == 0 and now_et.minute < 2:
                    self.daily_pnl = 0
                    self.daily_trades = 0
                    self.emergency_stop = False

                time.sleep(poll_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Poll error: {e}", exc_info=True)
                time.sleep(poll_interval)

        # Cleanup
        if self.position != 0 and self.bars:
            logger.info("Shutdown - closing open position")
            self.exit(self.bars[-1]['close'])
        self._print_summary()

    def _print_summary(self):
        wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info(f"  Trades: {self.total_trades} | Wins: {self.wins} | Win Rate: {wr:.1f}%")
        logger.info(f"  Total P&L: ${self.total_pnl:.2f}")
        logger.info(f"  Daily P&L: ${self.daily_pnl:.2f}")
        if self.trades:
            avg = self.total_pnl / self.total_trades
            logger.info(f"  Avg Trade: ${avg:.2f}")
        logger.info("=" * 60)


# ── Main ─────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="TradersPost MNQ Trading Bot")
    p.add_argument('--dry-run', action='store_true',
                   help='Run strategy without sending signals')
    p.add_argument('--csv', action='store_true',
                   help='Run on CSV data (sends real signals unless --dry-run)')
    p.add_argument('--contracts', type=int,
                   default=int(os.getenv('TRADING_CONTRACTS', '3')))
    p.add_argument('--symbol', default='mnq', help='Symbol for CSV data')
    p.add_argument('--timeframe', default='1min', help='CSV timeframe')
    p.add_argument('--ticker', default=None, help='TradersPost ticker (e.g. MNQM2026)')
    p.add_argument('--poll-interval', type=int, default=30, help='Live poll seconds')
    p.add_argument('--test-webhook', action='store_true', help='Test webhook and exit')
    args = p.parse_args()

    # Setup TradersPost client
    url = None if not args.dry_run else ''  # Empty URL = dry run
    if args.dry_run:
        url = ''
    tp = TradersPostClient(webhook_url=url, ticker=args.ticker)

    if args.test_webhook:
        success, result = tp.test()
        print(f"\n{'OK' if success else 'FAILED'}: {json.dumps(result, indent=2)}")
        return

    bot = TradersPostBot(
        tp_client=tp,
        contracts=args.contracts,
        hard_stop=20.0,
        max_daily_loss=1000.0,
        max_trades=int(os.getenv('MAX_TRADES_PER_SESSION', '10')),
        point_value=20.0,
    )

    # Signal handler
    def stop(signum, frame):
        logger.info("Shutdown signal received")
        bot.running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    logger.info("=" * 60)
    logger.info("TRADERSPOST MNQ TRADING BOT")
    logger.info(f"Ticker: {tp.ticker}")
    logger.info(f"Contracts: {args.contracts}")
    logger.info(f"Hard Stop: ${bot.hard_stop:.0f} | Max Daily Loss: ${bot.max_daily_loss:.0f}")
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE SIGNALS'}")
    logger.info(f"Strategy: EMA {STRATEGY['EMA_FAST']}/{STRATEGY['EMA_SLOW']}, "
               f"Stoch {STRATEGY['STOCH_K']}/{STRATEGY['STOCH_D']} "
               f"({STRATEGY['STOCH_LO']}/{STRATEGY['STOCH_HI']}), "
               f"ATR {STRATEGY['ATR_LEN']}, "
               f"SL {STRATEGY['SL_ATR_MULT']}x, TP {STRATEGY['TP_RR']}R")
    logger.info("=" * 60)

    if args.csv:
        bot.run_on_csv(symbol=args.symbol, timeframe=args.timeframe)
    else:
        bot.run_live_poll(poll_interval=args.poll_interval)


if __name__ == "__main__":
    main()
