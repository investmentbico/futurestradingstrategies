#!/usr/bin/env python3
"""
TradersPost Live Bot — Rithmic Price Feed + TradersPost Webhook Signals
=========================================================================
Uses async-rithmic WebSocket for real-time MNQ tick data.
Runs EMA/Stoch/ATR mean-reversion strategy.
Sends entry/exit signals to TradersPost → Apex Trader Funding via Tradovate.

Option C: 3 MNQ contracts, $20 hard stop, $836 max daily loss.
One order at a time — no stacking, no hedging.

Usage:
  python traderspost_live.py                # Live signals
  python traderspost_live.py --dry-run      # Strategy only, no webhook signals
  python traderspost_live.py --contracts 2  # Override contracts

Requirements:
  pip install async-rithmic numpy python-dotenv
  .env with RITHMIC_* and TRADERSPOST_* credentials
"""

import os
import sys
import json
import asyncio
import logging
import signal as sig
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Proxy support for restricted networks
try:
    from rithmic_proxy import patch_websockets_for_proxy
    patch_websockets_for_proxy()
except (ImportError, Exception):
    pass

try:
    from async_rithmic import RithmicClient, DataType, SysInfraType
    HAS_RITHMIC = True
except ImportError:
    HAS_RITHMIC = False

from traderspost_webhook import TradersPostClient

PROJECT_DIR = Path(__file__).parent

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"traderspost_live_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
logger = logging.getLogger("tp_live")

# ── Strategy Parameters ──────────────────────────────────────────────
STRATEGY = {
    "EMA_FAST": 21, "EMA_SLOW": 55,
    "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75,
    "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
}


# ── Indicators ───────────────────────────────────────────────────────
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


# ── Live Trading Bot ─────────────────────────────────────────────────
class TradersPostLiveBot:
    def __init__(self, tp_client, contracts=3, hard_stop=20.0,
                 max_daily_loss=836.0, max_trades=10, point_value=2.0,
                 symbol="MNQ", exchange="CME"):
        self.tp = tp_client
        self.contracts = contracts
        self.hard_stop = hard_stop
        self.max_daily_loss = max_daily_loss
        self.max_trades = max_trades
        self.point_value = point_value
        self.symbol = symbol
        self.exchange = exchange

        # Rithmic connection
        self.client = None
        self.connected = False

        # Market data
        self.price = 0.0
        self.bid = 0.0
        self.ask = 0.0
        self.last_tick = None
        self._bar_start = 0
        self._bar = None

        # Bars for indicators
        self.bars = []
        self.max_bars = 200

        # Position (ONE ORDER AT A TIME)
        self.position = 0       # +N=long, -N=short, 0=flat
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

        # Control
        self.running = False
        self.state_file = PROJECT_DIR / "bot_state.json"

    # ── Connection ─────────────────────────────────────────────────
    async def connect(self):
        """Try Rithmic WebSocket first, fall back to yfinance polling."""
        self.price_source = None

        if HAS_RITHMIC:
            try:
                await self._connect_rithmic()
                return
            except Exception as e:
                logger.warning(f"Rithmic failed: {e}")
                logger.info("Falling back to yfinance price feed...")

        # Fallback: yfinance
        try:
            import yfinance  # noqa: F401
            self.price_source = 'yfinance'
            self.connected = True
            logger.info("Price source: yfinance (NQ=F 1-min bars, ~15s delay)")
        except ImportError:
            raise RuntimeError("No price source: Rithmic failed and yfinance not installed")

    async def _connect_rithmic(self):
        user = os.getenv('RITHMIC_USER')
        password = os.getenv('RITHMIC_PASSWORD')
        system_name = os.getenv('RITHMIC_SYSTEM_NAME', 'Apex')
        url = os.getenv('RITHMIC_GATEWAY_URL', 'rituz00100.rithmic.com:443')

        if not user or not password:
            raise ValueError("Set RITHMIC_USER and RITHMIC_PASSWORD in .env")

        self.client = RithmicClient(
            user=user, password=password,
            system_name=system_name,
            app_name="MNQ_TradersPost_Bot", app_version="1.0",
            url=url,
        )

        logger.info("Connecting to Rithmic for price feed...")
        await self.client.connect(plants=[SysInfraType.TICKER_PLANT])
        self.connected = True
        self.price_source = 'rithmic'
        logger.info(f"Connected! User: {user} | Gateway: {url}")
        self.client.on_tick += self._on_tick

    def _on_tick(self, tick):
        try:
            p = getattr(tick, 'close', None) or getattr(tick, 'trade_price', None)
            if p and p > 0:
                self.price = float(p)
            b = getattr(tick, 'bid', None)
            a = getattr(tick, 'ask', None)
            if b and b > 0:
                self.bid = float(b)
            if a and a > 0:
                self.ask = float(a)
            self.last_tick = time.time()
        except Exception:
            pass

    def _fetch_yfinance_bars(self):
        """Fetch latest 1-min bars from yfinance (NQ=F ≈ MNQ price)."""
        try:
            import yfinance as yf
            nq = yf.Ticker('NQ=F')
            df = nq.history(period='1d', interval='1m')
            if df.empty:
                return []
            bars = []
            for idx, row in df.iterrows():
                bars.append({
                    'time': int(idx.timestamp()),
                    'open': float(row['Open']),
                    'high': float(row['High']),
                    'low': float(row['Low']),
                    'close': float(row['Close']),
                })
            return bars
        except Exception as e:
            logger.error(f"yfinance fetch error: {e}")
            return []

    # ── Bar Building ──────────────────────────────────────────────
    def _update_bar(self):
        if self.price <= 0:
            return False

        bar_start = int(time.time() // 60) * 60

        if self._bar and self._bar_start == bar_start:
            self._bar['high'] = max(self._bar['high'], self.price)
            self._bar['low'] = min(self._bar['low'], self.price)
            self._bar['close'] = self.price
            return False
        else:
            new_bar = False
            if self._bar and self._bar['close'] > 0:
                self.bars.append(self._bar)
                if len(self.bars) > self.max_bars:
                    self.bars = self.bars[-self.max_bars:]
                new_bar = True

            self._bar_start = bar_start
            self._bar = {
                'time': bar_start,
                'open': self.price,
                'high': self.price,
                'low': self.price,
                'close': self.price,
            }
            return new_bar

    # ── CSV Warm-Up ───────────────────────────────────────────────
    def load_warmup_bars(self):
        csv_path = PROJECT_DIR / "data" / f"{self.symbol.lower()}_1min.csv"
        if not csv_path.exists():
            logger.warning(f"No warmup CSV: {csv_path}")
            return 0
        try:
            import pandas as pd
            df = pd.read_csv(csv_path)
            warmup = min(self.max_bars, len(df))
            for _, row in df.tail(warmup).iterrows():
                self.bars.append({
                    'time': int(row['time']),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                })
            logger.info(f"Loaded {warmup} warmup bars from {csv_path}")
            return warmup
        except Exception as e:
            logger.error(f"Warmup load error: {e}")
            return 0

    # ── Indicators ────────────────────────────────────────────────
    def get_indicators(self):
        if len(self.bars) < STRATEGY["EMA_SLOW"] + 5:
            return None
        c = np.array([b['close'] for b in self.bars])
        h = np.array([b['high'] for b in self.bars])
        l = np.array([b['low'] for b in self.bars])

        ema_f = calc_ema(c, STRATEGY["EMA_FAST"])
        ema_s = calc_ema(c, STRATEGY["EMA_SLOW"])
        atr = calc_atr(h, l, c, STRATEGY["ATR_LEN"])
        _, stoch_d = calc_stoch(h, l, c, STRATEGY["STOCH_K"],
                                STRATEGY["STOCH_D"], STRATEGY["STOCH_SMT"])

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

    # ── Signal Logic (ONE ORDER AT A TIME) ────────────────────────
    def check_entry(self, ind):
        if ind is None or self.position != 0:
            return None
        if self.daily_trades >= self.max_trades or self.emergency_stop:
            return None
        if self.daily_pnl <= -self.max_daily_loss:
            self.emergency_stop = True
            logger.warning(f"DAILY LOSS LIMIT: ${self.daily_pnl:.2f}")
            return None

        if ind['uptrend'] and ind['d_falling'] and ind['stoch_d'] <= STRATEGY["STOCH_LO"] and ind['atr'] > 0:
            return 'long'
        if ind['downtrend'] and ind['d_rising'] and ind['stoch_d'] >= STRATEGY["STOCH_HI"] and ind['atr'] > 0:
            return 'short'
        return None

    def check_exit(self, ind):
        if self.position == 0:
            return False
        pv = self.point_value
        c = abs(self.position)

        if self.position > 0:
            pnl = (self.price - self.entry_price) * c * pv
            if pnl <= -self.hard_stop:
                logger.info(f"HARD STOP: P&L=${pnl:.2f}")
                return True
            if self.price <= self.stop_loss:
                logger.info(f"ATR STOP: {self.price:.2f} <= SL {self.stop_loss:.2f}")
                return True
            if self.price >= self.take_profit:
                logger.info(f"TAKE PROFIT: {self.price:.2f} >= TP {self.take_profit:.2f}")
                return True
            if self.trailing_stop > 0 and self.price <= self.trailing_stop:
                logger.info(f"TRAIL STOP: {self.price:.2f} <= {self.trailing_stop:.2f}")
                return True
            if ind and ind['atr'] > 0:
                new_t = self.price - ind['atr'] * STRATEGY["SL_ATR_MULT"]
                if new_t > self.trailing_stop:
                    self.trailing_stop = new_t
        else:
            pnl = (self.entry_price - self.price) * c * pv
            if pnl <= -self.hard_stop:
                logger.info(f"HARD STOP: P&L=${pnl:.2f}")
                return True
            if self.price >= self.stop_loss:
                logger.info(f"ATR STOP: {self.price:.2f} >= SL {self.stop_loss:.2f}")
                return True
            if self.price <= self.take_profit:
                logger.info(f"TAKE PROFIT: {self.price:.2f} <= TP {self.take_profit:.2f}")
                return True
            if self.trailing_stop > 0 and self.price >= self.trailing_stop:
                logger.info(f"TRAIL STOP: {self.price:.2f} >= {self.trailing_stop:.2f}")
                return True
            if ind and ind['atr'] > 0:
                new_t = self.price + ind['atr'] * STRATEGY["SL_ATR_MULT"]
                if self.trailing_stop == 0 or new_t < self.trailing_stop:
                    self.trailing_stop = new_t
        return False

    # ── Execution via TradersPost Webhook ─────────────────────────
    def enter(self, direction, atr):
        if self.position != 0:
            logger.warning("BLOCKED: position already open")
            return False

        # Cancel stale orders first
        self.tp.send_cancel()

        qty = self.contracts
        mult = STRATEGY["SL_ATR_MULT"]
        rr = STRATEGY["TP_RR"]

        if direction == 'long':
            self.stop_loss = self.price - atr * mult
            self.take_profit = self.price + atr * mult * rr
            sl_amount = atr * mult
            tp_amount = atr * mult * rr
        else:
            self.stop_loss = self.price + atr * mult
            self.take_profit = self.price - atr * mult * rr
            sl_amount = atr * mult
            tp_amount = atr * mult * rr

        self.entry_price = self.price
        self.position = qty if direction == 'long' else -qty
        self.trailing_stop = 0.0

        d = 'LONG' if direction == 'long' else 'SHORT'
        logger.info("=" * 55)
        logger.info(f"{d} ENTRY: {qty}x @ {self.price:.2f}")
        logger.info(f"  SL: {self.stop_loss:.2f} ({sl_amount:.2f} pts)")
        logger.info(f"  TP: {self.take_profit:.2f} ({tp_amount:.2f} pts)")
        logger.info(f"  ATR: {atr:.2f}")
        logger.info("=" * 55)

        if direction == 'long':
            success, result = self.tp.send_long(
                quantity=qty, signal_price=self.price,
                stop_loss_amount=sl_amount, take_profit_amount=tp_amount,
            )
        else:
            success, result = self.tp.send_short(
                quantity=qty, signal_price=self.price,
                stop_loss_amount=sl_amount, take_profit_amount=tp_amount,
            )

        if not success:
            logger.error(f"TradersPost signal FAILED: {result}")
            self.position = 0
            return False

        self.daily_trades += 1
        self._write_state()
        return True

    def exit(self):
        pv = self.point_value
        if self.position > 0:
            pnl = (self.price - self.entry_price) * abs(self.position) * pv
            d = "LONG"
        else:
            pnl = (self.entry_price - self.price) * abs(self.position) * pv
            d = "SHORT"

        logger.info("=" * 55)
        logger.info(f"{d} EXIT: {abs(self.position)}x @ {self.price:.2f} | P&L: ${pnl:.2f}")
        logger.info("=" * 55)

        success, result = self.tp.send_exit(cancel_orders=True)
        if not success:
            logger.error(f"TradersPost exit FAILED: {result}")

        trade = {
            'time': datetime.now(timezone.utc).isoformat(),
            'dir': d, 'qty': abs(self.position),
            'entry': self.entry_price, 'exit': self.price, 'pnl': pnl,
        }
        self.trades.append(trade)
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
        self.total_pnl += pnl
        self.daily_pnl += pnl

        self.position = 0
        self.entry_price = 0
        self.stop_loss = 0
        self.take_profit = 0
        self.trailing_stop = 0

        self._write_state()
        return success

    # ── State for Dashboard ───────────────────────────────────────
    def _write_state(self):
        try:
            unrealized = 0.0
            if self.position != 0 and self.price > 0:
                pv = self.point_value
                if self.position > 0:
                    unrealized = (self.price - self.entry_price) * abs(self.position) * pv
                else:
                    unrealized = (self.entry_price - self.price) * abs(self.position) * pv

            state = {
                'ts': datetime.now(timezone.utc).isoformat(),
                'symbol': self.symbol, 'ticker': self.tp.ticker,
                'mode': 'DRY_RUN' if not self.tp.webhook_url else 'LIVE',
                'connected': self.connected,
                'price': self.price, 'bid': self.bid, 'ask': self.ask,
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

    # ── Main Loop ─────────────────────────────────────────────────
    async def run(self):
        self.running = True

        if self.price_source == 'rithmic':
            await self._run_rithmic()
        elif self.price_source == 'yfinance':
            await self._run_yfinance()

    async def _run_rithmic(self):
        """Real-time tick-by-tick via Rithmic WebSocket."""
        self.load_warmup_bars()

        logger.info(f"Subscribing to {self.symbol} on {self.exchange}...")
        await self.client.subscribe_to_market_data(
            self.symbol, self.exchange,
            DataType.LAST_TRADE | DataType.BBO
        )
        logger.info("Market data subscribed. Waiting for ticks...")

        tick_count = 0
        while self.running:
            try:
                await asyncio.sleep(0.5)

                if self.price <= 0:
                    if tick_count % 20 == 0:
                        logger.info("Waiting for market data...")
                    tick_count += 1
                    continue

                new_bar = self._update_bar()
                tick_count += 1

                # Always check exits on every tick when in position
                if self.position != 0 and tick_count % 2 == 0:
                    ind = self.get_indicators()
                    if self.check_exit(ind):
                        self.exit()
                    if not new_bar:
                        continue

                if not new_bar:
                    continue

                self._evaluate_bar()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _run_yfinance(self):
        """Poll yfinance for 1-min bars (NQ=F). ~30s refresh."""
        logger.info("Starting yfinance polling mode (NQ=F → MNQ price)...")
        last_bar_time = 0

        # Initial load — get all today's bars for warmup
        bars = self._fetch_yfinance_bars()
        if bars:
            self.bars = bars[-self.max_bars:]
            last_bar_time = bars[-1]['time']
            self.price = bars[-1]['close']
            logger.info(f"Loaded {len(self.bars)} bars from yfinance | Price: {self.price:.2f}")
        else:
            # Fallback to CSV warmup
            self.load_warmup_bars()

        self._write_state()

        while self.running:
            try:
                await asyncio.sleep(30)  # poll every 30s

                new_bars = self._fetch_yfinance_bars()
                if not new_bars:
                    continue

                # Add only new bars we haven't seen
                added = 0
                for bar in new_bars:
                    if bar['time'] > last_bar_time:
                        self.bars.append(bar)
                        if len(self.bars) > self.max_bars:
                            self.bars = self.bars[-self.max_bars:]
                        last_bar_time = bar['time']
                        self.price = bar['close']
                        added += 1

                        # Check exits on each new bar
                        if self.position != 0:
                            ind = self.get_indicators()
                            if self.check_exit(ind):
                                self.exit()
                                continue

                if added > 0:
                    self._evaluate_bar()

                # Daily reset (midnight ET)
                now_et = datetime.now(timezone(timedelta(hours=-5)))
                if now_et.hour == 0 and now_et.minute < 2:
                    self.daily_pnl = 0
                    self.daily_trades = 0
                    self.emergency_stop = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Poll error: {e}", exc_info=True)
                await asyncio.sleep(30)

    def _evaluate_bar(self):
        """Run strategy on latest completed bar."""
        ind = self.get_indicators()

        if self.position != 0:
            if self.check_exit(ind):
                self.exit()
        elif self.position == 0:
            entry = self.check_entry(ind)
            if entry and ind:
                self.enter(entry, ind['atr'])

        # Periodic status log
        if len(self.bars) % 5 == 0:
            self._write_state()
            pos = f"{'LONG' if self.position > 0 else 'SHORT'} {abs(self.position)}x" if self.position != 0 else "FLAT"
            wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
            logger.info(
                f"[{len(self.bars)} bars] {self.price:.2f} | {pos} | "
                f"Daily=${self.daily_pnl:.2f} | Trades={self.daily_trades} | "
                f"Total=${self.total_pnl:.2f} WR={wr:.0f}%"
            )

    async def shutdown(self):
        self.running = False
        if self.position != 0 and self.price > 0:
            logger.warning("Closing open position on shutdown!")
            self.exit()
        self._write_state()
        if self.connected:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info(f"  Trades: {self.total_trades} | Wins: {self.wins} | WR: {wr:.1f}%")
        logger.info(f"  Total P&L: ${self.total_pnl:.2f}")
        logger.info(f"  Daily P&L: ${self.daily_pnl:.2f}")
        logger.info("=" * 60)


# ── Main ─────────────────────────────────────────────────────────────
async def main():
    import argparse
    p = argparse.ArgumentParser(description="TradersPost Live Bot (Rithmic + TradersPost)")
    p.add_argument('--dry-run', action='store_true', help='No webhook signals')
    p.add_argument('--contracts', type=int,
                   default=int(os.getenv('TRADING_CONTRACTS', '3')))
    p.add_argument('--symbol', default=os.getenv('TRADING_SYMBOL', 'MNQ'))
    p.add_argument('--exchange', default='CME')
    p.add_argument('--ticker', default=None, help='TradersPost ticker override')
    args = p.parse_args()

    # TradersPost webhook client
    url = '' if args.dry_run else None
    tp = TradersPostClient(webhook_url=url, ticker=args.ticker)

    # Bot with Option C params
    bot = TradersPostLiveBot(
        tp_client=tp,
        contracts=args.contracts,
        hard_stop=float(os.getenv('HARD_STOP_DOLLARS', '20')),
        max_daily_loss=float(os.getenv('MAX_DAILY_LOSS', '836')),
        max_trades=int(os.getenv('MAX_TRADES_PER_SESSION', '10')),
        point_value=2.0,  # MNQ = $2/point
        symbol=args.symbol,
        exchange=args.exchange,
    )

    loop = asyncio.get_event_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, lambda: asyncio.create_task(bot.shutdown()))

    logger.info("=" * 60)
    logger.info("TRADERSPOST LIVE BOT — OPTION C")
    logger.info(f"Signals:    TradersPost → Apex/Tradovate")
    logger.info(f"Ticker:     {tp.ticker}")
    logger.info(f"Contracts:  {args.contracts} MNQ")
    logger.info(f"Hard Stop:  ${bot.hard_stop:.0f} | Max Daily: ${bot.max_daily_loss:.0f}")
    logger.info(f"Mode:       {'DRY RUN' if args.dry_run else 'LIVE SIGNALS'}")
    logger.info(f"Strategy:   EMA {STRATEGY['EMA_FAST']}/{STRATEGY['EMA_SLOW']}, "
                f"Stoch {STRATEGY['STOCH_LO']}/{STRATEGY['STOCH_HI']}, "
                f"ATR {STRATEGY['ATR_LEN']}, SL {STRATEGY['SL_ATR_MULT']}x, TP {STRATEGY['TP_RR']}R")
    logger.info(f"Enforcement: ONE ORDER AT A TIME — no stacking, no hedging")
    logger.info("=" * 60)

    try:
        await bot.connect()
        logger.info(f"Price Feed: {bot.price_source}")
        await bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
