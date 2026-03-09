#!/usr/bin/env python3
"""
MT5 Live Trading Bot — Upcomers Prop Firm Challenge
=====================================================
Run this on your LOCAL machine (iTerm/Mac/Windows) where MT5 terminal is installed.

OPTIMIZED PARAMETERS (from backtest optimization):
  Primary:   XAUUSD 2min — EMA 13/89, Stoch 25/80, SL 1.5x ATR, TP 1.5R, 2 lots
  Secondary: US100  2min — EMA 21/55, Stoch 20/80, SL 2.5x ATR, TP 1.5R, 5 lots

PROP FIRM RULES:
  Account:       $500,000
  Max DD/day:    1.5% ($7,500)
  Target/day:    $10,100
  Days to pass:  5
  Min hold time: 61 SECONDS (trades closed before 61s are VIOLATIONS)

Usage:
  python mt5_live_bot.py                        # Run XAUUSD (default)
  python mt5_live_bot.py --symbol US100         # Run US100/NASDAQ
  python mt5_live_bot.py --symbol XAUUSD --lots 3
  python mt5_live_bot.py --dry-run              # Paper trade mode

Requirements:
  pip install MetaTrader5 pandas numpy
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

PROJECT_DIR = Path(__file__).parent

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"mt5_live_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
)
logger = logging.getLogger("mt5_bot")

# ── MT5 Credentials ─────────────────────────────────────────────────
MT5_LOGIN = 1237326
MT5_PASSWORD = "kzC{9]2o;LU2"
MT5_SERVER = "Upcomers"

# ── Prop Firm Rules ──────────────────────────────────────────────────
ACCOUNT_BALANCE = 500_000
MAX_DAILY_DD_PCT = 1.5
MAX_DAILY_LOSS = ACCOUNT_BALANCE * MAX_DAILY_DD_PCT / 100  # $7,500
DAILY_PROFIT_TARGET = 10_100
MIN_HOLD_SECONDS = 61  # CRITICAL: trades must be held at least 61 seconds

# ── Instrument Configs ───────────────────────────────────────────────
INSTRUMENT_CONFIGS = {
    'XAUUSD': {
        'strategy': {
            'EMA_FAST': 13, 'EMA_SLOW': 89,
            'STOCH_K': 9, 'STOCH_D': 3, 'STOCH_SMT': 2,
            'ATR_LEN': 9,
            'STOCH_LO': 25, 'STOCH_HI': 80,
            'SL_ATR_MULT': 1.5, 'TP_RR': 1.5,
        },
        'lots': 2.0,
        'hard_stop': 2000,  # $ per trade
        'point_value_per_lot': 100.0,  # $100 per $1 move per lot
        'tick_size': 0.01,
        'description': 'Gold (best avg $2,512/day)',
    },
    'US100': {
        'strategy': {
            'EMA_FAST': 21, 'EMA_SLOW': 55,
            'STOCH_K': 9, 'STOCH_D': 3, 'STOCH_SMT': 2,
            'ATR_LEN': 9,
            'STOCH_LO': 20, 'STOCH_HI': 80,
            'SL_ATR_MULT': 2.5, 'TP_RR': 1.5,
        },
        'lots': 5.0,
        'hard_stop': 2000,
        'point_value_per_lot': 20.0,
        'tick_size': 0.25,
        'description': 'NASDAQ 100 (avg $2,158/day)',
    },
    'NAS100': {  # Alias for US100
        'strategy': {
            'EMA_FAST': 21, 'EMA_SLOW': 55,
            'STOCH_K': 9, 'STOCH_D': 3, 'STOCH_SMT': 2,
            'ATR_LEN': 9,
            'STOCH_LO': 20, 'STOCH_HI': 80,
            'SL_ATR_MULT': 2.5, 'TP_RR': 1.5,
        },
        'lots': 5.0,
        'hard_stop': 2000,
        'point_value_per_lot': 20.0,
        'tick_size': 0.25,
        'description': 'NASDAQ 100 (avg $2,158/day)',
    },
    'USTEC': {  # Another NASDAQ alias
        'strategy': {
            'EMA_FAST': 21, 'EMA_SLOW': 55,
            'STOCH_K': 9, 'STOCH_D': 3, 'STOCH_SMT': 2,
            'ATR_LEN': 9,
            'STOCH_LO': 20, 'STOCH_HI': 80,
            'SL_ATR_MULT': 2.5, 'TP_RR': 1.5,
        },
        'lots': 5.0,
        'hard_stop': 2000,
        'point_value_per_lot': 20.0,
        'tick_size': 0.25,
        'description': 'US Tech 100 (avg $2,158/day)',
    },
    'BTCUSD': {
        'strategy': {
            'EMA_FAST': 21, 'EMA_SLOW': 55,
            'STOCH_K': 9, 'STOCH_D': 3, 'STOCH_SMT': 2,
            'ATR_LEN': 9,
            'STOCH_LO': 25, 'STOCH_HI': 75,
            'SL_ATR_MULT': 2.0, 'TP_RR': 1.8,
        },
        'lots': 1.0,
        'hard_stop': 2000,
        'point_value_per_lot': 1.0,
        'tick_size': 0.01,
        'description': 'Bitcoin (experimental)',
    },
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
class MT5LiveBot:
    def __init__(self, symbol='XAUUSD', lots=None, dry_run=False):
        self.symbol = symbol.upper()
        self.dry_run = dry_run

        if self.symbol not in INSTRUMENT_CONFIGS:
            logger.error(f"Unknown symbol: {self.symbol}. Available: {list(INSTRUMENT_CONFIGS.keys())}")
            sys.exit(1)

        self.config = INSTRUMENT_CONFIGS[self.symbol]
        self.strategy = self.config['strategy']
        self.lots = lots or self.config['lots']
        self.hard_stop = self.config['hard_stop']
        self.pv = self.config['point_value_per_lot']
        self.tick_size = self.config['tick_size']

        # MT5 module (loaded on connect)
        self.mt5 = None

        # Bars for indicators (2-min bars)
        self.bars = []
        self.max_bars = 200
        self.bar_interval = 120  # 2-minute bars

        # Position state
        self.position = 0       # +1=long, -1=short, 0=flat
        self.entry_price = 0.0
        self.entry_time = 0.0   # timestamp of entry (for 61s rule)
        self.stop_loss = 0.0
        self.trailing_stop = 0.0
        self.peak_pnl = 0.0
        self.mt5_ticket = None   # MT5 position ticket

        # Stats
        self.total_trades = 0
        self.wins = 0
        self.total_pnl = 0.0
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.emergency_stop = False
        self.consecutive_losses = 0
        self.trades = []

        # Control
        self.running = False
        self.prev_uptrend = None
        self.last_exit_pnl = 0
        self.last_exit_time = 0

    def connect(self):
        """Connect to MT5 terminal."""
        try:
            import MetaTrader5 as mt5
            self.mt5 = mt5
        except ImportError:
            logger.error("MetaTrader5 not installed. Run: pip install MetaTrader5")
            sys.exit(1)

        if not self.mt5.initialize():
            logger.error(f"MT5 init failed: {self.mt5.last_error()}")
            sys.exit(1)

        if not self.mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            logger.error(f"MT5 login failed: {self.mt5.last_error()}")
            self.mt5.shutdown()
            sys.exit(1)

        # Verify symbol
        info = self.mt5.symbol_info(self.symbol)
        if info is None:
            logger.error(f"Symbol {self.symbol} not found. Try --list-symbols with mt5_connector.py")
            self.mt5.shutdown()
            sys.exit(1)
        if not info.visible:
            self.mt5.symbol_select(self.symbol, True)

        account = self.mt5.account_info()
        logger.info("=" * 65)
        logger.info("MT5 PROP FIRM CHALLENGE BOT — LIVE")
        logger.info(f"  Server:      {MT5_SERVER}")
        logger.info(f"  Account:     {MT5_LOGIN} | Balance: ${account.balance:,.2f}")
        logger.info(f"  Symbol:      {self.symbol} ({self.config['description']})")
        logger.info(f"  Lots:        {self.lots}")
        logger.info(f"  Strategy:    EMA {self.strategy['EMA_FAST']}/{self.strategy['EMA_SLOW']}, "
                     f"Stoch {self.strategy['STOCH_LO']}/{self.strategy['STOCH_HI']}, "
                     f"ATR {self.strategy['ATR_LEN']}, "
                     f"SL {self.strategy['SL_ATR_MULT']}x, TP {self.strategy['TP_RR']}R")
        logger.info(f"  Hard Stop:   ${self.hard_stop}/trade")
        logger.info(f"  Daily Limit: ${MAX_DAILY_LOSS:,.0f} loss | ${DAILY_PROFIT_TARGET:,} target")
        logger.info(f"  Min Hold:    {MIN_HOLD_SECONDS}s (prop firm rule)")
        logger.info(f"  Mode:        {'DRY RUN' if self.dry_run else 'LIVE TRADING'}")
        logger.info("=" * 65)

    def load_warmup_bars(self):
        """Load recent 2-min bars from MT5 for indicator warmup."""
        tf = self.mt5.TIMEFRAME_M2
        rates = self.mt5.copy_rates_from_pos(self.symbol, tf, 0, self.max_bars)
        if rates is not None and len(rates) > 0:
            for r in rates:
                self.bars.append({
                    'time': int(r['time']),
                    'open': float(r['open']),
                    'high': float(r['high']),
                    'low': float(r['low']),
                    'close': float(r['close']),
                })
            logger.info(f"Loaded {len(self.bars)} warmup bars")
        else:
            logger.warning("Could not load warmup bars")

    def get_price(self):
        """Get current bid/ask from MT5."""
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return None, None
        return tick.bid, tick.ask

    def get_account_equity(self):
        """Get current account equity."""
        info = self.mt5.account_info()
        return info.equity if info else 0

    def get_indicators(self):
        """Compute indicators from current bars."""
        min_bars = max(self.strategy['STOCH_K'] + self.strategy['STOCH_D'] + 2, 20)
        if len(self.bars) < min_bars:
            return None

        c = np.array([b['close'] for b in self.bars])
        h = np.array([b['high'] for b in self.bars])
        l = np.array([b['low'] for b in self.bars])

        ema_f = calc_ema(c, self.strategy['EMA_FAST'])
        ema_s = calc_ema(c, self.strategy['EMA_SLOW'])
        atr = calc_atr(h, l, c, self.strategy['ATR_LEN'])
        _, stoch_d = calc_stoch(h, l, c, self.strategy['STOCH_K'],
                                self.strategy['STOCH_D'], self.strategy['STOCH_SMT'])

        f, s, a, d = ema_f[-1], ema_s[-1], atr[-1], stoch_d[-1]
        d_prev = stoch_d[-2] if len(stoch_d) > 1 else d

        if any(np.isnan(x) for x in [f, s, a, d]):
            return None

        return {
            'ema_fast': f, 'ema_slow': s, 'atr': a,
            'stoch_d': d, 'prev_d': d_prev,
            'uptrend': f > s, 'downtrend': f < s,
            'd_rising': d > d_prev, 'd_falling': d < d_prev,
            'price': c[-1],
        }

    def check_entry(self, ind):
        """Check for entry signals."""
        if ind is None or self.position != 0:
            return None
        if self.emergency_stop:
            return None
        if self.daily_trades >= 6:
            return None
        if self.consecutive_losses >= 3:
            return None

        # Cooldown after loss
        if self.last_exit_pnl < 0 and (time.time() - self.last_exit_time) < 120:
            return None

        # Daily limits
        if self.daily_pnl <= -MAX_DAILY_LOSS:
            self.emergency_stop = True
            logger.warning(f"DAILY LOSS LIMIT HIT: ${self.daily_pnl:,.2f}")
            return None
        if self.daily_pnl >= DAILY_PROFIT_TARGET:
            self.emergency_stop = True
            logger.info(f"DAILY TARGET HIT: ${self.daily_pnl:,.2f}")
            return None

        stoch = ind['stoch_d']
        stoch_lo = self.strategy['STOCH_LO']
        stoch_hi = self.strategy['STOCH_HI']
        atr_ok = ind['atr'] > 0

        signal = None

        # PRIMARY: Mean reversion from stoch extreme
        if ind['uptrend'] and atr_ok and stoch <= stoch_lo and ind['d_rising']:
            signal = 'long'
            logger.info(f"SIGNAL: Reversal LONG — Stoch {stoch:.1f} turning up in uptrend")
        elif ind['downtrend'] and atr_ok and stoch >= stoch_hi and ind['d_falling']:
            signal = 'short'
            logger.info(f"SIGNAL: Reversal SHORT — Stoch {stoch:.1f} turning down in downtrend")

        # SECONDARY: Moderate pullback
        if signal is None and atr_ok:
            if ind['uptrend'] and stoch <= stoch_lo + 10 and ind['d_rising']:
                signal = 'long'
                logger.info(f"SIGNAL: Momentum LONG — Stoch {stoch:.1f} rising")
            elif ind['downtrend'] and stoch >= stoch_hi - 10 and ind['d_falling']:
                signal = 'short'
                logger.info(f"SIGNAL: Momentum SHORT — Stoch {stoch:.1f} falling")

        # EMA crossover
        if signal is None and self.prev_uptrend is not None and atr_ok:
            if ind['uptrend'] and not self.prev_uptrend and stoch <= stoch_hi - 10:
                signal = 'long'
                logger.info(f"SIGNAL: EMA Cross LONG — bullish cross + Stoch {stoch:.1f}")
            elif ind['downtrend'] and self.prev_uptrend and stoch >= stoch_lo + 10:
                signal = 'short'
                logger.info(f"SIGNAL: EMA Cross SHORT — bearish cross + Stoch {stoch:.1f}")

        self.prev_uptrend = ind['uptrend']
        return signal

    def check_exit(self, ind, bid, ask):
        """Check exit conditions. ENFORCES 61-SECOND MINIMUM HOLD."""
        if self.position == 0:
            return False

        now = time.time()
        hold_time = now - self.entry_time
        price = bid if self.position > 0 else ask

        # Calculate current P&L
        if self.position > 0:
            pnl = (price - self.entry_price) * self.lots * self.pv
        else:
            pnl = (self.entry_price - price) * self.lots * self.pv

        if pnl > self.peak_pnl:
            self.peak_pnl = pnl

        # ══════════════════════════════════════════════════════════
        # 61-SECOND RULE: NEVER close before 61 seconds
        # Exception: hard stop hit (emergency only)
        # ══════════════════════════════════════════════════════════
        if hold_time < MIN_HOLD_SECONDS:
            # Only allow exit on catastrophic hard stop
            if pnl <= -self.hard_stop:
                logger.warning(f"EMERGENCY HARD STOP at {hold_time:.0f}s (< 61s) | P&L: ${pnl:,.2f}")
                return True
            # Otherwise WAIT — do not exit
            return False

        # After 61 seconds, normal exit logic applies:

        # Hard stop
        if pnl <= -self.hard_stop:
            logger.info(f"HARD STOP: P&L=${pnl:,.2f} | Hold={hold_time:.0f}s")
            return True

        r_unit = self.hard_stop

        # Smart trailing
        if self.position > 0:
            if self.peak_pnl >= r_unit * 3:
                floor = self.entry_price + (self.peak_pnl * 0.65) / (self.lots * self.pv)
                self.trailing_stop = max(self.trailing_stop, floor)
            elif self.peak_pnl >= r_unit * 2:
                floor = self.entry_price + (self.peak_pnl * 0.45) / (self.lots * self.pv)
                self.trailing_stop = max(self.trailing_stop, floor)
            elif self.peak_pnl >= r_unit:
                be = self.entry_price + self.tick_size
                self.trailing_stop = max(self.trailing_stop, be)

            if ind and ind['atr'] > 0:
                atr_trail = price - ind['atr'] * 1.5
                self.trailing_stop = max(self.trailing_stop, atr_trail)

            if self.trailing_stop > 0 and price <= self.trailing_stop:
                logger.info(f"TRAIL EXIT: {price:.2f} <= {self.trailing_stop:.2f} | P&L=${pnl:,.2f} | Hold={hold_time:.0f}s")
                return True
            if price <= self.stop_loss:
                logger.info(f"ATR STOP: {price:.2f} <= SL {self.stop_loss:.2f} | P&L=${pnl:,.2f} | Hold={hold_time:.0f}s")
                return True

        elif self.position < 0:
            if self.peak_pnl >= r_unit * 3:
                floor = self.entry_price - (self.peak_pnl * 0.65) / (self.lots * self.pv)
                if self.trailing_stop == 0 or floor < self.trailing_stop:
                    self.trailing_stop = floor
            elif self.peak_pnl >= r_unit * 2:
                floor = self.entry_price - (self.peak_pnl * 0.45) / (self.lots * self.pv)
                if self.trailing_stop == 0 or floor < self.trailing_stop:
                    self.trailing_stop = floor
            elif self.peak_pnl >= r_unit:
                be = self.entry_price - self.tick_size
                if self.trailing_stop == 0 or be < self.trailing_stop:
                    self.trailing_stop = be

            if ind and ind['atr'] > 0:
                atr_trail = price + ind['atr'] * 1.5
                if self.trailing_stop == 0 or atr_trail < self.trailing_stop:
                    self.trailing_stop = atr_trail

            if self.trailing_stop > 0 and price >= self.trailing_stop:
                logger.info(f"TRAIL EXIT: {price:.2f} >= {self.trailing_stop:.2f} | P&L=${pnl:,.2f} | Hold={hold_time:.0f}s")
                return True
            if price >= self.stop_loss:
                logger.info(f"ATR STOP: {price:.2f} >= SL {self.stop_loss:.2f} | P&L=${pnl:,.2f} | Hold={hold_time:.0f}s")
                return True

        return False

    def enter_trade(self, direction, atr):
        """Place entry order via MT5."""
        if self.position != 0:
            return False

        bid, ask = self.get_price()
        if bid is None:
            return False

        mult = self.strategy['SL_ATR_MULT']
        rr = self.strategy['TP_RR']
        atr_sl = atr * mult
        hard_stop_pts = self.hard_stop / (self.lots * self.pv)
        sl_amount = min(atr_sl, hard_stop_pts)
        tp_amount = atr * mult * rr

        if direction == 'long':
            price = ask  # buy at ask
            sl = price - sl_amount
            tp = price + tp_amount
            order_type = self.mt5.ORDER_TYPE_BUY
        else:
            price = bid  # sell at bid
            sl = price + sl_amount
            tp = price - tp_amount
            order_type = self.mt5.ORDER_TYPE_SELL

        logger.info("=" * 60)
        logger.info(f"{'LONG' if direction == 'long' else 'SHORT'} ENTRY: {self.lots} lots @ {price:.2f}")
        logger.info(f"  SL: {sl:.2f} ({sl_amount:.2f} pts) | TP: {tp:.2f} ({tp_amount:.2f} pts)")
        logger.info(f"  ATR: {atr:.2f} | Hard Stop: ${self.hard_stop}")
        logger.info("=" * 60)

        if self.dry_run:
            logger.info("[DRY RUN] Order not sent")
            self.position = 1 if direction == 'long' else -1
            self.entry_price = price
            self.entry_time = time.time()
            self.stop_loss = sl
            self.trailing_stop = 0.0
            self.peak_pnl = 0.0
            self.daily_trades += 1
            return True

        # Send MT5 order
        request = {
            'action': self.mt5.TRADE_ACTION_DEAL,
            'symbol': self.symbol,
            'volume': self.lots,
            'type': order_type,
            'price': price,
            'sl': sl,
            'tp': tp,
            'deviation': 20,
            'magic': 123789,
            'comment': 'PropFirmBot',
            'type_time': self.mt5.ORDER_TIME_GTC,
            'type_filling': self.mt5.ORDER_FILLING_IOC,
        }

        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            error = result.comment if result else "Unknown error"
            logger.error(f"ORDER FAILED: {error}")
            return False

        self.mt5_ticket = result.order
        self.position = 1 if direction == 'long' else -1
        self.entry_price = result.price
        self.entry_time = time.time()
        self.stop_loss = sl
        self.trailing_stop = 0.0
        self.peak_pnl = 0.0
        self.daily_trades += 1

        logger.info(f"ORDER FILLED: Ticket #{self.mt5_ticket} @ {self.entry_price:.2f}")
        return True

    def exit_trade(self):
        """Close position via MT5."""
        if self.position == 0:
            return False

        bid, ask = self.get_price()
        if bid is None:
            return False

        hold_time = time.time() - self.entry_time
        if self.position > 0:
            price = bid
            pnl = (price - self.entry_price) * self.lots * self.pv
            order_type = self.mt5.ORDER_TYPE_SELL if not self.dry_run else None
        else:
            price = ask
            pnl = (self.entry_price - price) * self.lots * self.pv
            order_type = self.mt5.ORDER_TYPE_BUY if not self.dry_run else None

        direction = "LONG" if self.position > 0 else "SHORT"

        logger.info("=" * 60)
        logger.info(f"{direction} EXIT: {self.lots} lots @ {price:.2f}")
        logger.info(f"  P&L: ${pnl:,.2f} | Hold: {hold_time:.0f}s | Peak: ${self.peak_pnl:,.2f}")
        logger.info("=" * 60)

        if not self.dry_run:
            request = {
                'action': self.mt5.TRADE_ACTION_DEAL,
                'symbol': self.symbol,
                'volume': self.lots,
                'type': order_type,
                'price': price,
                'deviation': 20,
                'magic': 123789,
                'comment': 'PropFirmBot_Exit',
                'type_time': self.mt5.ORDER_TIME_GTC,
                'type_filling': self.mt5.ORDER_FILLING_IOC,
            }

            # Close by position ticket if available
            if self.mt5_ticket:
                request['position'] = self.mt5_ticket

            result = self.mt5.order_send(request)
            if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
                error = result.comment if result else "Unknown error"
                logger.error(f"EXIT ORDER FAILED: {error}")
                return False

        # Update stats
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        self.total_pnl += pnl
        self.daily_pnl += pnl
        self.last_exit_pnl = pnl
        self.last_exit_time = time.time()

        self.trades.append({
            'time': datetime.now(timezone.utc).isoformat(),
            'dir': direction,
            'entry': self.entry_price,
            'exit': price,
            'pnl': pnl,
            'hold_seconds': hold_time,
            'lots': self.lots,
        })

        # Reset position state
        self.position = 0
        self.entry_price = 0.0
        self.entry_time = 0.0
        self.stop_loss = 0.0
        self.trailing_stop = 0.0
        self.peak_pnl = 0.0
        self.mt5_ticket = None

        return True

    def refresh_bars(self):
        """Fetch latest 2-min bars from MT5."""
        tf = self.mt5.TIMEFRAME_M2
        rates = self.mt5.copy_rates_from_pos(self.symbol, tf, 0, self.max_bars)
        if rates is not None and len(rates) > 0:
            new_bars = []
            for r in rates:
                new_bars.append({
                    'time': int(r['time']),
                    'open': float(r['open']),
                    'high': float(r['high']),
                    'low': float(r['low']),
                    'close': float(r['close']),
                })
            if new_bars:
                old_count = len(self.bars)
                self.bars = new_bars[-self.max_bars:]
                return len(self.bars) > old_count
        return False

    def run(self):
        """Main trading loop — polls every 0.5s, refreshes bars every 15s."""
        self.running = True
        self.load_warmup_bars()

        logger.info(f"Starting main loop — 0.5s price poll, 15s bar refresh...")
        logger.info(f"Loaded {len(self.bars)} bars | Waiting for signals...")

        tick_count = 0
        last_bar_refresh = time.time()
        BAR_REFRESH = 15
        PRICE_POLL = 0.5
        last_daily_reset = None

        try:
            while self.running:
                time.sleep(PRICE_POLL)
                tick_count += 1
                now = time.time()

                # Daily reset at midnight UTC
                today = datetime.now(timezone.utc).date()
                if today != last_daily_reset:
                    if last_daily_reset is not None:
                        logger.info(f"DAILY RESET | Yesterday P&L: ${self.daily_pnl:,.2f}")
                    self.daily_pnl = 0.0
                    self.daily_trades = 0
                    self.emergency_stop = False
                    self.consecutive_losses = 0
                    last_daily_reset = today

                # Get live price
                bid, ask = self.get_price()
                if bid is None or ask is None:
                    continue

                mid = (bid + ask) / 2

                # Check exits every tick
                if self.position != 0:
                    ind = self.get_indicators()
                    if self.check_exit(ind, bid, ask):
                        self.exit_trade()

                # Refresh bars periodically
                if now - last_bar_refresh >= BAR_REFRESH:
                    last_bar_refresh = now
                    self.refresh_bars()

                # Check entries
                if self.position == 0:
                    ind = self.get_indicators()
                    signal = self.check_entry(ind)
                    if signal and ind:
                        self.enter_trade(signal, ind['atr'])

                # Status log every 5s (10 ticks)
                if tick_count % 10 == 0:
                    pos_str = f"{'LONG' if self.position > 0 else 'SHORT'} {self.lots}" if self.position != 0 else "FLAT"
                    unrealized = 0.0
                    hold_str = ""
                    if self.position != 0:
                        hold_time = now - self.entry_time
                        if self.position > 0:
                            unrealized = (bid - self.entry_price) * self.lots * self.pv
                        else:
                            unrealized = (self.entry_price - ask) * self.lots * self.pv
                        hold_str = f" | Hold={hold_time:.0f}s{'*' if hold_time < MIN_HOLD_SECONDS else ''}"

                    ind = self.get_indicators()
                    ind_str = ""
                    if ind:
                        trend = "UP" if ind['uptrend'] else "DN"
                        d_dir = "^" if ind['d_rising'] else "v"
                        ind_str = f" | {trend} StD={ind['stoch_d']:.0f}{d_dir} ATR={ind['atr']:.1f}"

                    wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
                    logger.info(
                        f"[{len(self.bars)} bars] {bid:.2f}/{ask:.2f} | {pos_str} | "
                        f"Unreal=${unrealized:,.0f}{hold_str} | Daily=${self.daily_pnl:,.0f} | "
                        f"Trades={self.daily_trades} | Total=${self.total_pnl:,.0f} WR={wr:.0f}%{ind_str}"
                    )

        except KeyboardInterrupt:
            logger.info("\nShutdown requested...")
        finally:
            self.shutdown()

    def shutdown(self):
        """Clean shutdown — close position if open."""
        self.running = False
        if self.position != 0:
            hold_time = time.time() - self.entry_time
            if hold_time < MIN_HOLD_SECONDS:
                wait = MIN_HOLD_SECONDS - hold_time + 1
                logger.warning(f"Waiting {wait:.0f}s for 61s rule before closing...")
                time.sleep(wait)
            self.exit_trade()

        wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info(f"  Trades: {self.total_trades} | Wins: {self.wins} | WR: {wr:.1f}%")
        logger.info(f"  Total P&L: ${self.total_pnl:,.2f}")
        logger.info(f"  Daily P&L: ${self.daily_pnl:,.2f}")
        logger.info("=" * 60)

        # Save trade log
        if self.trades:
            log_path = PROJECT_DIR / "logs" / f"mt5_trades_{datetime.now().strftime('%Y%m%d')}.json"
            with open(log_path, 'w') as f:
                json.dump(self.trades, f, indent=2)
            logger.info(f"Trade log saved: {log_path}")

        if self.mt5:
            self.mt5.shutdown()


def main():
    parser = argparse.ArgumentParser(description='MT5 Live Trading Bot — Prop Firm Challenge')
    parser.add_argument('--symbol', type=str, default='XAUUSD',
                       help='Trading symbol (XAUUSD, US100, NAS100, USTEC, BTCUSD)')
    parser.add_argument('--lots', type=float, help='Override lot size')
    parser.add_argument('--dry-run', action='store_true', help='Paper trade mode')
    parser.add_argument('--list-symbols', action='store_true', help='List configured symbols')
    args = parser.parse_args()

    if args.list_symbols:
        print("\nConfigured symbols:")
        for sym, cfg in INSTRUMENT_CONFIGS.items():
            print(f"  {sym:12s} — {cfg['description']} | Lots: {cfg['lots']} | "
                  f"EMA {cfg['strategy']['EMA_FAST']}/{cfg['strategy']['EMA_SLOW']}")
        return

    bot = MT5LiveBot(
        symbol=args.symbol,
        lots=args.lots,
        dry_run=args.dry_run,
    )
    bot.connect()
    bot.run()


if __name__ == "__main__":
    main()
