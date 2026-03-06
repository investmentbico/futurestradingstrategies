#!/usr/bin/env python3
"""
TradersPost Live Bot — Rithmic Price Feed + TradersPost Webhook Signals
=========================================================================
Uses async-rithmic WebSocket for real-time MNQ tick data.
Runs EMA/Stoch/ATR mean-reversion strategy.
Sends entry/exit signals to TradersPost → Apex Trader Funding via Tradovate.

Apex $50K Prop Firm Challenge Mode:
10 MNQ contracts, $80 hard stop, $800 max daily loss.
Broker-side SL capped at hard stop distance for drawdown protection.
Profit lock-in: tightens risk as profits grow (trailing drawdown safe).
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

import urllib.request
import urllib.error
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

    def _fetch_fast_price(self):
        """Ultra-fast price fetch via Yahoo Finance direct HTTP (no library overhead)."""
        try:
            url = 'https://query1.finance.yahoo.com/v8/finance/chart/NQ=F?interval=1m&range=1m'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode())
                meta = data['chart']['result'][0]['meta']
                price = meta.get('regularMarketPrice', 0)
                if price > 0:
                    return float(price)
        except Exception:
            pass
        # Fallback: yfinance fast_info
        try:
            import yfinance as yf
            return float(yf.Ticker('NQ=F').fast_info.get('lastPrice', 0))
        except Exception:
            return 0.0

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
    def _get_effective_daily_loss_limit(self):
        """Prop firm trailing drawdown protection: lock in profits.
        As daily_pnl grows, we tighten the loss limit to protect gains.
        This prevents giving back profits (critical for trailing drawdown accounts)."""
        base_limit = self.max_daily_loss  # $800

        if self.daily_pnl > 500:
            # Lock in 50% of profits above $500
            locked = (self.daily_pnl - 500) * 0.50
            effective = base_limit - locked
            logger.info(f"Profit lock: daily=${self.daily_pnl:.0f}, locked=${locked:.0f}, "
                       f"effective limit=${effective:.0f}")
            return max(effective, 200)  # never go below $200 limit
        return base_limit

    def check_entry(self, ind):
        if ind is None or self.position != 0:
            return None
        if self.daily_trades >= self.max_trades or self.emergency_stop:
            return None

        effective_limit = self._get_effective_daily_loss_limit()
        if self.daily_pnl <= -effective_limit:
            self.emergency_stop = True
            logger.warning(f"DAILY LOSS LIMIT: ${self.daily_pnl:.2f} (limit: ${effective_limit:.0f})")
            return None

        # Prop firm: if we hit profit target, stop trading (don't risk giving it back)
        profit_target = float(os.getenv('APEX_PROFIT_TARGET', '0'))
        if profit_target > 0 and self.daily_pnl >= profit_target:
            logger.info(f"PROFIT TARGET HIT: ${self.daily_pnl:.2f} >= ${profit_target:.0f} — stopping!")
            self.emergency_stop = True
            return None

        # Standard entry: strict EMA + Stoch + ATR conditions
        if ind['uptrend'] and ind['d_falling'] and ind['stoch_d'] <= STRATEGY["STOCH_LO"] and ind['atr'] > 0:
            return 'long'
        if ind['downtrend'] and ind['d_rising'] and ind['stoch_d'] >= STRATEGY["STOCH_HI"] and ind['atr'] > 0:
            return 'short'

        # Quick re-entry: if we just exited profitably and trend is still strong,
        # re-enter with relaxed Stoch threshold (catch continuation moves)
        if hasattr(self, '_last_exit_pnl') and self._last_exit_pnl > 0:
            if hasattr(self, '_last_exit_time') and time.time() - self._last_exit_time < 120:
                if ind['uptrend'] and ind['stoch_d'] <= 50 and ind['atr'] > 0:
                    logger.info(f"QUICK RE-ENTRY LONG: last trade +${self._last_exit_pnl:.0f}, trend still up, Stoch={ind['stoch_d']:.0f}")
                    self._last_exit_pnl = 0  # only re-enter once
                    return 'long'
                if ind['downtrend'] and ind['stoch_d'] >= 50 and ind['atr'] > 0:
                    logger.info(f"QUICK RE-ENTRY SHORT: last trade +${self._last_exit_pnl:.0f}, trend still down, Stoch={ind['stoch_d']:.0f}")
                    self._last_exit_pnl = 0
                    return 'short'

        return None

    def check_exit(self, ind):
        """Smart multi-phase exit system:
        Phase 0 (underwater): Hard stop only — give trade room to work
        Phase 1 (breakeven+): Move stop to breakeven, lock in $0 loss
        Phase 2 (1R profit):  Tight trail — lock 50% of peak profit
        Phase 3 (2R+ spike):  Ultra-tight trail — lock 70% of peak, ride the spike
        Phase 4 (3R+ runner): Lock 80% — massive winner, protect it aggressively
        """
        if self.position == 0:
            return False
        pv = self.point_value
        c = abs(self.position)

        # Calculate current P&L
        if self.position > 0:
            pnl = (self.price - self.entry_price) * c * pv
        else:
            pnl = (self.entry_price - self.price) * c * pv

        # Track peak unrealized P&L for this trade
        if not hasattr(self, '_peak_pnl'):
            self._peak_pnl = 0.0
        if pnl > self._peak_pnl:
            self._peak_pnl = pnl

        # Hard stop — always active, never violated
        if pnl <= -self.hard_stop:
            logger.info(f"HARD STOP: P&L=${pnl:.2f}")
            return True

        # Calculate R-multiple (how many risk units we're up)
        r_unit = self.hard_stop  # 1R = hard stop amount
        r_multiple = pnl / r_unit if r_unit > 0 else 0

        # ── Smart Trailing Logic ──────────────────────────────
        if self.position > 0:  # LONG
            # Phase 4: 3R+ runner → lock 80% of peak
            if self._peak_pnl >= r_unit * 3:
                floor_pnl = self._peak_pnl * 0.80
                floor_price = self.entry_price + floor_pnl / (c * pv)
                if floor_price > self.trailing_stop:
                    self.trailing_stop = floor_price
                    logger.info(f"PHASE4 TRAIL: lock 80% of ${self._peak_pnl:.0f} peak → SL={self.trailing_stop:.2f}")

            # Phase 3: 2R+ spike → lock 70% of peak
            elif self._peak_pnl >= r_unit * 2:
                floor_pnl = self._peak_pnl * 0.70
                floor_price = self.entry_price + floor_pnl / (c * pv)
                if floor_price > self.trailing_stop:
                    self.trailing_stop = floor_price
                    logger.info(f"PHASE3 TRAIL: lock 70% of ${self._peak_pnl:.0f} peak → SL={self.trailing_stop:.2f}")

            # Phase 2: 1R profit → lock 50% of peak
            elif self._peak_pnl >= r_unit:
                floor_pnl = self._peak_pnl * 0.50
                floor_price = self.entry_price + floor_pnl / (c * pv)
                if floor_price > self.trailing_stop:
                    self.trailing_stop = floor_price
                    logger.info(f"PHASE2 TRAIL: lock 50% of ${self._peak_pnl:.0f} peak → SL={self.trailing_stop:.2f}")

            # Phase 1: breakeven zone (0.5R+) → move stop to breakeven + $10
            elif pnl >= r_unit * 0.5:
                be_price = self.entry_price + 0.50  # breakeven + tiny buffer
                if be_price > self.trailing_stop:
                    self.trailing_stop = be_price
                    logger.info(f"PHASE1 BREAKEVEN: moved SL to {self.trailing_stop:.2f}")

            # Also apply ATR trail if it's tighter than phase trail
            if ind and ind['atr'] > 0:
                atr_trail = self.price - ind['atr'] * 1.0  # tighter 1x ATR trail
                if atr_trail > self.trailing_stop:
                    self.trailing_stop = atr_trail

            # Check all exit conditions
            if self.trailing_stop > 0 and self.price <= self.trailing_stop:
                logger.info(f"SMART TRAIL EXIT: {self.price:.2f} <= {self.trailing_stop:.2f} | P&L=${pnl:.2f} | Peak=${self._peak_pnl:.0f} | R={r_multiple:.1f}")
                return True
            if self.price <= self.stop_loss:
                logger.info(f"ATR STOP: {self.price:.2f} <= SL {self.stop_loss:.2f} | P&L=${pnl:.2f}")
                return True

        else:  # SHORT
            # Phase 4: 3R+ runner → lock 80% of peak
            if self._peak_pnl >= r_unit * 3:
                floor_pnl = self._peak_pnl * 0.80
                floor_price = self.entry_price - floor_pnl / (c * pv)
                if self.trailing_stop == 0 or floor_price < self.trailing_stop:
                    self.trailing_stop = floor_price
                    logger.info(f"PHASE4 TRAIL: lock 80% of ${self._peak_pnl:.0f} peak → SL={self.trailing_stop:.2f}")

            # Phase 3: 2R+ spike → lock 70% of peak
            elif self._peak_pnl >= r_unit * 2:
                floor_pnl = self._peak_pnl * 0.70
                floor_price = self.entry_price - floor_pnl / (c * pv)
                if self.trailing_stop == 0 or floor_price < self.trailing_stop:
                    self.trailing_stop = floor_price
                    logger.info(f"PHASE3 TRAIL: lock 70% of ${self._peak_pnl:.0f} peak → SL={self.trailing_stop:.2f}")

            # Phase 2: 1R profit → lock 50% of peak
            elif self._peak_pnl >= r_unit:
                floor_pnl = self._peak_pnl * 0.50
                floor_price = self.entry_price - floor_pnl / (c * pv)
                if self.trailing_stop == 0 or floor_price < self.trailing_stop:
                    self.trailing_stop = floor_price
                    logger.info(f"PHASE2 TRAIL: lock 50% of ${self._peak_pnl:.0f} peak → SL={self.trailing_stop:.2f}")

            # Phase 1: breakeven zone (0.5R+) → move stop to breakeven
            elif pnl >= r_unit * 0.5:
                be_price = self.entry_price - 0.50
                if self.trailing_stop == 0 or be_price < self.trailing_stop:
                    self.trailing_stop = be_price
                    logger.info(f"PHASE1 BREAKEVEN: moved SL to {self.trailing_stop:.2f}")

            # ATR trail for shorts
            if ind and ind['atr'] > 0:
                atr_trail = self.price + ind['atr'] * 1.0
                if self.trailing_stop == 0 or atr_trail < self.trailing_stop:
                    self.trailing_stop = atr_trail

            # Check all exit conditions
            if self.trailing_stop > 0 and self.price >= self.trailing_stop:
                logger.info(f"SMART TRAIL EXIT: {self.price:.2f} >= {self.trailing_stop:.2f} | P&L=${pnl:.2f} | Peak=${self._peak_pnl:.0f} | R={r_multiple:.1f}")
                return True
            if self.price >= self.stop_loss:
                logger.info(f"ATR STOP: {self.price:.2f} >= SL {self.stop_loss:.2f} | P&L=${pnl:.2f}")
                return True

        return False

    # ── Execution via TradersPost Webhook ─────────────────────────
    def enter(self, direction, atr):
        if self.position != 0:
            logger.warning("BLOCKED: position already open")
            return False

        # Prop firm protection: check remaining drawdown budget
        remaining_budget = self.max_daily_loss + self.daily_pnl  # positive = room left
        if remaining_budget < self.hard_stop:
            logger.warning(f"SKIP: remaining budget ${remaining_budget:.2f} < hard stop ${self.hard_stop:.2f}")
            return False

        # Cancel stale orders first
        self.tp.send_cancel()

        qty = self.contracts
        mult = STRATEGY["SL_ATR_MULT"]
        rr = STRATEGY["TP_RR"]

        # Calculate ATR-based stops
        atr_sl = atr * mult
        atr_tp = atr * mult * rr

        # Cap broker-side SL at hard stop distance for drawdown protection
        hard_stop_pts = self.hard_stop / (qty * self.point_value)
        sl_amount = min(atr_sl, hard_stop_pts)
        tp_amount = atr_tp  # TP stays at full ATR target for big wins

        if direction == 'long':
            self.stop_loss = self.price - sl_amount
            self.take_profit = self.price + tp_amount
        else:
            self.stop_loss = self.price + sl_amount
            self.take_profit = self.price - tp_amount

        self.entry_price = self.price
        self.position = qty if direction == 'long' else -qty
        self.trailing_stop = 0.0
        self._peak_pnl = 0.0  # reset peak tracker for new trade

        d = 'LONG' if direction == 'long' else 'SHORT'
        logger.info("=" * 55)
        logger.info(f"{d} ENTRY: {qty}x @ {self.price:.2f}")
        logger.info(f"  SL: {self.stop_loss:.2f} ({sl_amount:.2f} pts, capped from ATR {atr_sl:.2f})")
        logger.info(f"  TP: {self.take_profit:.2f} ({tp_amount:.2f} pts)")
        logger.info(f"  ATR: {atr:.2f} | Hard Stop: ${self.hard_stop:.0f} | Budget: ${remaining_budget:.0f}")
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

        # Track for quick re-entry logic
        self._last_exit_pnl = pnl
        self._last_exit_time = time.time()

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
        self._peak_pnl = 0.0

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
        """FAST MODE: 1-second price polling + 30s bar refresh.
        Checks exits every second, entries on each new 1-min bar."""
        logger.info("Starting FAST yfinance mode (1s price poll + 30s bar refresh)...")
        last_bar_time = 0
        last_bar_refresh = 0
        tick_count = 0
        BAR_REFRESH_INTERVAL = 30  # full bar data refresh
        PRICE_POLL_INTERVAL = 1    # fast price check

        # Initial load — get all today's bars for warmup
        bars = self._fetch_yfinance_bars()
        if bars:
            self.bars = bars[-self.max_bars:]
            last_bar_time = bars[-1]['time']
            self.price = bars[-1]['close']
            logger.info(f"Loaded {len(self.bars)} bars | Price: {self.price:.2f}")
        else:
            self.load_warmup_bars()

        self._write_state()

        while self.running:
            try:
                await asyncio.sleep(PRICE_POLL_INTERVAL)
                tick_count += 1
                now = time.time()

                # ── FAST: Poll price every 1s ──────────────────────
                new_price = self._fetch_fast_price()
                if new_price > 0 and new_price != self.price:
                    self.price = new_price

                    # Build bars from ticks (like Rithmic mode)
                    new_bar = self._update_bar()

                    # Check exits on EVERY price tick when in position
                    if self.position != 0:
                        ind = self.get_indicators()
                        if self.check_exit(ind):
                            self.exit()
                    # Check entries on every tick too (fast re-entry)
                    elif self.position == 0 and len(self.bars) >= STRATEGY["EMA_SLOW"] + 5:
                        ind = self.get_indicators()
                        entry = self.check_entry(ind)
                        if entry and ind:
                            self.enter(entry, ind['atr'])

                    # Evaluate on new bar close
                    if new_bar:
                        self._evaluate_bar()

                # ── SLOW: Full bar data refresh every 30s ──────────
                if now - last_bar_refresh >= BAR_REFRESH_INTERVAL:
                    last_bar_refresh = now
                    new_bars = self._fetch_yfinance_bars()
                    if new_bars:
                        added = 0
                        for bar in new_bars:
                            if bar['time'] > last_bar_time:
                                self.bars.append(bar)
                                if len(self.bars) > self.max_bars:
                                    self.bars = self.bars[-self.max_bars:]
                                last_bar_time = bar['time']
                                self.price = bar['close']
                                added += 1

                        if added > 0:
                            self._evaluate_bar()

                # Status log every 10s
                if tick_count % 10 == 0:
                    self._write_state()
                    pos = f"{'LONG' if self.position > 0 else 'SHORT'} {abs(self.position)}x" if self.position != 0 else "FLAT"
                    wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
                    unrealized = 0.0
                    if self.position != 0:
                        if self.position > 0:
                            unrealized = (self.price - self.entry_price) * abs(self.position) * self.point_value
                        else:
                            unrealized = (self.entry_price - self.price) * abs(self.position) * self.point_value
                    logger.info(
                        f"[{len(self.bars)} bars] {self.price:.2f} | {pos} | "
                        f"Unreal=${unrealized:.0f} | Daily=${self.daily_pnl:.0f} | "
                        f"Trades={self.daily_trades} | Total=${self.total_pnl:.0f} WR={wr:.0f}%"
                    )

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
                await asyncio.sleep(2)

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

    profit_target = float(os.getenv('APEX_PROFIT_TARGET', '0'))
    max_dd = float(os.getenv('APEX_MAX_DRAWDOWN', '0'))
    logger.info("=" * 60)
    logger.info("APEX PROP FIRM CHALLENGE BOT — LIVE")
    logger.info(f"Account:    $50K Apex | Max DD: ${max_dd:.0f} | Target: ${profit_target:.0f}")
    logger.info(f"Signals:    TradersPost → Apex/Tradovate")
    logger.info(f"Ticker:     {tp.ticker}")
    logger.info(f"Contracts:  {args.contracts} MNQ (${args.contracts * 2}/pt)")
    logger.info(f"Hard Stop:  ${bot.hard_stop:.0f}/trade | Max Daily Loss: ${bot.max_daily_loss:.0f}")
    logger.info(f"Mode:       {'DRY RUN' if args.dry_run else 'LIVE SIGNALS'}")
    logger.info(f"Strategy:   EMA {STRATEGY['EMA_FAST']}/{STRATEGY['EMA_SLOW']}, "
                f"Stoch {STRATEGY['STOCH_LO']}/{STRATEGY['STOCH_HI']}, "
                f"ATR {STRATEGY['ATR_LEN']}, SL {STRATEGY['SL_ATR_MULT']}x, TP {STRATEGY['TP_RR']}R")
    logger.info(f"Protection: Broker SL capped at hard stop | Profit lock-in active")
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
