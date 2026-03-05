#!/usr/bin/env python3
"""
Rithmic/Apex Live Trading Bot for MNQ
=======================================
Connects to Apex Trader Funding via Rithmic Protocol Buffer API.
Receives strategy signals and automatically places/manages orders.
Includes webhook server for external signal providers (TradingView, etc).

Usage:
  python rithmic_trading_bot.py --demo         # Paper trading (default)
  python rithmic_trading_bot.py --live         # Live trading
  python rithmic_trading_bot.py --live --symbol MNQM6

Requirements:
  pip install async-rithmic python-dotenv numpy
  .env with RITHMIC_USER, RITHMIC_PASSWORD, RITHMIC_SYSTEM_NAME, RITHMIC_GATEWAY_URL
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
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Proxy support for restricted network environments
try:
    from rithmic_proxy import patch_websockets_for_proxy
    patch_websockets_for_proxy()
except (ImportError, Exception):
    pass

from async_rithmic import RithmicClient, DataType, SysInfraType, TransactionType, OrderType

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"rithmic_bot_{datetime.now().strftime('%Y%m%d')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
)
logger = logging.getLogger("rithmic_bot")

# ── Strategy Parameters (matching mnq_live_bot.py winner) ────────────
STRATEGY = {
    "EMA_FAST": 21, "EMA_SLOW": 55,
    "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75,
    "SL_ATR_MULT": 2.0, "TP_RR": 1.8,
}

RISK = {
    "CONTRACTS": int(os.getenv("TRADING_CONTRACTS", "3")),
    "HARD_STOP_DOLLARS": 25.0,
    "MAX_DAILY_LOSS": 225.0,
    "MAX_TRADES": int(os.getenv("MAX_TRADES_PER_SESSION", "10")),
    "POINT_VALUE": 2.0,   # MNQ = $2/point
}

# ── Indicators (vectorized, same as mnq_live_bot.py) ─────────────────
def calc_ema(prices, period):
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high, low, close, period):
    tr = np.maximum(high - low, np.maximum(
        np.abs(high - np.roll(close, 1)),
        np.abs(low - np.roll(close, 1))
    ))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period-1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period - 1) + tr[i]) / period
    return out

def calc_stoch(high, low, close, k_period, d_period, smooth):
    n = len(close)
    if n < k_period:
        return np.full(n, 50.0), np.full(n, 50.0)
    lowest = np.array([np.min(low[i-k_period+1:i+1]) for i in range(k_period-1, n)])
    highest = np.array([np.max(high[i-k_period+1:i+1]) for i in range(k_period-1, n)])
    denom = highest - lowest
    denom[denom == 0] = 1
    k = 100 * (close[k_period-1:] - lowest) / denom
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    valid = k[~np.isnan(k)]
    d = calc_ema(valid, d_period) if len(valid) > 0 else np.array([])
    d_full = np.full(n, np.nan)
    d_full[~np.isnan(k)] = d if len(d) == np.sum(~np.isnan(k)) else np.full(np.sum(~np.isnan(k)), 50.0)
    valid_d = d_full[~np.isnan(d_full)]
    d_smooth = calc_ema(valid_d, smooth) if len(valid_d) > 0 else np.array([])
    d_smooth_full = np.full(n, np.nan)
    mask = ~np.isnan(d_full)
    if len(d_smooth) == np.sum(mask):
        d_smooth_full[mask] = d_smooth
    return k, d_smooth_full


# ── Trading Bot ──────────────────────────────────────────────────────
class RithmicTradingBot:
    def __init__(self, symbol="MNQ", exchange="CME", demo=True):
        self.symbol = symbol
        self.exchange = exchange
        self.demo = demo

        # Connection
        self.client = None
        self.connected = False
        self.account_id = None
        self.trade_route = None
        self.fcm_id = None
        self.ib_id = None

        # Market data
        self.bars = []          # 1-min OHLC bars
        self.max_bars = 200
        self.price = 0.0
        self.bid = 0.0
        self.ask = 0.0
        self.last_tick = None
        self._bar_start = 0     # current bar start timestamp (epoch, floored to 60s)
        self._bar = None        # current building bar

        # Position
        self.position = 0       # 0=flat, >0=long, <0=short
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.trailing_stop = 0.0
        self.contracts = 0

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
        self.signal_file = Path(__file__).parent / "signal.json"
        self.state_file = Path(__file__).parent / "bot_state.json"

    # ── Connection ───────────────────────────────────────────────────
    async def connect(self):
        user = os.getenv('RITHMIC_USER')
        password = os.getenv('RITHMIC_PASSWORD')
        system_name = os.getenv('RITHMIC_SYSTEM_NAME', 'Apex')
        url = os.getenv('RITHMIC_GATEWAY_URL', 'rituz00100.rithmic.com:443')

        if not user or not password:
            raise ValueError("Set RITHMIC_USER and RITHMIC_PASSWORD in .env")

        logger.info("=" * 60)
        logger.info("RITHMIC TRADING BOT")
        logger.info(f"User: {user} | System: {system_name}")
        logger.info(f"Gateway: {url}")
        logger.info(f"Symbol: {self.symbol} | Mode: {'DEMO' if self.demo else '*** LIVE ***'}")
        logger.info("=" * 60)

        self.client = RithmicClient(
            user=user, password=password,
            system_name=system_name,
            app_name="MNQ_Trading_Bot", app_version="1.0",
            url=url,
        )

        plants = [SysInfraType.TICKER_PLANT]
        if not self.demo:
            plants += [SysInfraType.ORDER_PLANT, SysInfraType.PNL_PLANT]

        await self.client.connect(plants=plants)
        self.connected = True
        logger.info("Connected to Rithmic!")

        # Account setup
        if not self.demo:
            await self._setup_account()

        # Event handlers
        self.client.on_tick += self._on_tick

        if not self.demo:
            self.client.on_rithmic_order_notification += self._on_order
            self.client.on_exchange_order_notification += self._on_fill

    async def _setup_account(self):
        try:
            accts = self.client.accounts
            if accts:
                self.account_id = accts[0]
                logger.info(f"Account: {self.account_id}")
                for a in accts:
                    logger.info(f"  Available: {a}")

            routes = self.client.plants["order"].trade_routes
            for r in routes:
                logger.info(f"Route: {r}")
                self.trade_route = getattr(r, 'trade_route', str(r))
                self.fcm_id = getattr(r, 'fcm_id', None)
                self.ib_id = getattr(r, 'ib_id', None)
                break
        except Exception as e:
            logger.error(f"Account setup error: {e}")

    # ── Market Data Handlers ─────────────────────────────────────────
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

    def _update_bar(self):
        """Build 1-minute bars from tick data."""
        if self.price <= 0:
            return False

        now = time.time()
        bar_start = int(now // 60) * 60

        if self._bar and self._bar_start == bar_start:
            # Update current bar
            self._bar['high'] = max(self._bar['high'], self.price)
            self._bar['low'] = min(self._bar['low'], self.price)
            self._bar['close'] = self.price
            return False
        else:
            # Finalize previous bar
            new_bar_completed = False
            if self._bar and self._bar['close'] > 0:
                self.bars.append(self._bar)
                if len(self.bars) > self.max_bars:
                    self.bars = self.bars[-self.max_bars:]
                new_bar_completed = True

            # Start new bar
            self._bar_start = bar_start
            self._bar = {
                'time': bar_start,
                'open': self.price,
                'high': self.price,
                'low': self.price,
                'close': self.price,
            }
            return new_bar_completed

    # ── Order Event Handlers ─────────────────────────────────────────
    def _on_order(self, notif):
        logger.info(f"ORDER: {notif}")

    def _on_fill(self, notif):
        logger.info(f"FILL: {notif}")

    # ── Indicators ───────────────────────────────────────────────────
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

        fast, slow = ema_f[-1], ema_s[-1]
        cur_atr = atr[-1]
        cur_d = stoch_d[-1]
        prev_d = stoch_d[-2] if len(stoch_d) > 1 else cur_d

        if any(np.isnan(x) for x in [fast, slow, cur_atr, cur_d]):
            return None

        return {
            'ema_fast': fast, 'ema_slow': slow, 'atr': cur_atr,
            'stoch_d': cur_d, 'prev_d': prev_d,
            'uptrend': fast > slow, 'downtrend': fast < slow,
            'd_falling': cur_d < prev_d, 'd_rising': cur_d > prev_d,
        }

    # ── Signal Logic ─────────────────────────────────────────────────
    def check_entry(self, ind):
        if ind is None or self.position != 0:
            return None
        if self.daily_trades >= RISK["MAX_TRADES"] or self.emergency_stop:
            return None
        if self.daily_pnl <= -RISK["MAX_DAILY_LOSS"]:
            if not self.emergency_stop:
                self.emergency_stop = True
                logger.warning(f"DAILY LOSS LIMIT HIT: ${self.daily_pnl:.2f}")
            return None

        # LONG: uptrend + stoch falling into oversold
        if ind['uptrend'] and ind['d_falling'] and ind['stoch_d'] <= STRATEGY["STOCH_LO"] and ind['atr'] > 0:
            return 'long'
        # SHORT: downtrend + stoch rising into overbought
        if ind['downtrend'] and ind['d_rising'] and ind['stoch_d'] >= STRATEGY["STOCH_HI"] and ind['atr'] > 0:
            return 'short'
        return None

    def check_exit(self, ind):
        if self.position == 0:
            return False
        pv = RISK["POINT_VALUE"]
        c = self.contracts

        if self.position > 0:
            pnl = (self.price - self.entry_price) * c * pv
            if pnl <= -RISK["HARD_STOP_DOLLARS"]:
                logger.info(f"HARD STOP: P&L=${pnl:.2f}")
                return True
            if self.price <= self.stop_loss:
                logger.info(f"ATR STOP: {self.price:.2f} <= {self.stop_loss:.2f}")
                return True
            if self.price >= self.take_profit:
                logger.info(f"TAKE PROFIT: {self.price:.2f} >= {self.take_profit:.2f}")
                return True
            if self.trailing_stop > 0 and self.price <= self.trailing_stop:
                logger.info(f"TRAILING STOP: {self.price:.2f} <= {self.trailing_stop:.2f}")
                return True
            # Update trail
            if ind and ind['atr'] > 0:
                new_t = self.price - ind['atr'] * STRATEGY["SL_ATR_MULT"]
                if new_t > self.trailing_stop:
                    self.trailing_stop = new_t
        else:
            pnl = (self.entry_price - self.price) * c * pv
            if pnl <= -RISK["HARD_STOP_DOLLARS"]:
                logger.info(f"HARD STOP: P&L=${pnl:.2f}")
                return True
            if self.price >= self.stop_loss:
                logger.info(f"ATR STOP: {self.price:.2f} >= {self.stop_loss:.2f}")
                return True
            if self.price <= self.take_profit:
                logger.info(f"TAKE PROFIT: {self.price:.2f} <= {self.take_profit:.2f}")
                return True
            if self.trailing_stop > 0 and self.price >= self.trailing_stop:
                logger.info(f"TRAILING STOP: {self.price:.2f} >= {self.trailing_stop:.2f}")
                return True
            if ind and ind['atr'] > 0:
                new_t = self.price + ind['atr'] * STRATEGY["SL_ATR_MULT"]
                if self.trailing_stop == 0 or new_t < self.trailing_stop:
                    self.trailing_stop = new_t
        return False

    # ── Order Execution ──────────────────────────────────────────────
    async def enter(self, direction, atr):
        qty = RISK["CONTRACTS"]
        mult = STRATEGY["SL_ATR_MULT"]
        rr = STRATEGY["TP_RR"]

        if direction == 'long':
            self.stop_loss = self.price - atr * mult
            self.take_profit = self.price + atr * mult * rr
            side = TransactionType.BUY
        else:
            self.stop_loss = self.price + atr * mult
            self.take_profit = self.price - atr * mult * rr
            side = TransactionType.SELL

        self.entry_price = self.price
        self.position = qty if direction == 'long' else -qty
        self.contracts = qty
        self.trailing_stop = 0.0

        d = 'LONG' if direction == 'long' else 'SHORT'
        logger.info("=" * 50)
        logger.info(f"{d} ENTRY: {qty}x @ {self.price:.2f}")
        logger.info(f"  SL: {self.stop_loss:.2f} | TP: {self.take_profit:.2f} | ATR: {atr:.2f}")
        logger.info("=" * 50)

        if not self.demo:
            try:
                await self.client.submit_order(
                    symbol=self.symbol, exchange=self.exchange,
                    quantity=qty, transaction_type=side,
                    order_type=OrderType.MARKET,
                    account_id=self.account_id,
                    trade_route=self.trade_route,
                    fcm_id=self.fcm_id, ib_id=self.ib_id,
                )
                logger.info("Order submitted to exchange")
            except Exception as e:
                logger.error(f"ORDER FAILED: {e}")
                self.position = 0
                self.contracts = 0
                return
        else:
            logger.info("[DEMO] Order simulated")

        self.daily_trades += 1

    async def exit(self):
        pv = RISK["POINT_VALUE"]
        if self.position > 0:
            pnl = (self.price - self.entry_price) * self.contracts * pv
            side = TransactionType.SELL
            d = "LONG"
        else:
            pnl = (self.entry_price - self.price) * self.contracts * pv
            side = TransactionType.BUY
            d = "SHORT"

        logger.info("=" * 50)
        logger.info(f"{d} EXIT: {self.contracts}x @ {self.price:.2f} | P&L: ${pnl:.2f}")
        logger.info("=" * 50)

        if not self.demo:
            try:
                await self.client.submit_order(
                    symbol=self.symbol, exchange=self.exchange,
                    quantity=self.contracts, transaction_type=side,
                    order_type=OrderType.MARKET,
                    account_id=self.account_id,
                    trade_route=self.trade_route,
                    fcm_id=self.fcm_id, ib_id=self.ib_id,
                )
                logger.info("Exit order submitted")
            except Exception as e:
                logger.error(f"EXIT FAILED: {e}")
                return

        trade = {
            'time': datetime.now(timezone.utc).isoformat(),
            'dir': d, 'qty': self.contracts,
            'entry': self.entry_price, 'exit': self.price, 'pnl': pnl,
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
        self.contracts = 0

        self._write_state()

    # ── External Signals ─────────────────────────────────────────────
    def read_signal(self):
        """Read external signal from signal.json (written by webhook)."""
        try:
            if self.signal_file.exists():
                data = json.loads(self.signal_file.read_text())
                self.signal_file.unlink()
                action = data.get('action', '').lower()
                if action in ('buy', 'long'):
                    return 'long'
                elif action in ('sell', 'short'):
                    return 'short'
                elif action in ('close', 'exit', 'flatten'):
                    return 'close'
                logger.info(f"WEBHOOK SIGNAL: {data}")
        except Exception:
            pass
        return None

    # ── State for Dashboard ──────────────────────────────────────────
    def _write_state(self):
        try:
            unrealized = 0.0
            if self.position != 0 and self.price > 0:
                pv = RISK["POINT_VALUE"]
                if self.position > 0:
                    unrealized = (self.price - self.entry_price) * self.contracts * pv
                else:
                    unrealized = (self.entry_price - self.price) * self.contracts * pv

            state = {
                'ts': datetime.now(timezone.utc).isoformat(),
                'symbol': self.symbol, 'mode': 'DEMO' if self.demo else 'LIVE',
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

    # ── Main Loop ────────────────────────────────────────────────────
    async def run(self):
        self.running = True
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

                # Build bars from ticks
                new_bar = self._update_bar()
                tick_count += 1

                # Only evaluate strategy on new completed bars
                if not new_bar and tick_count % 2 == 0 and self.position != 0:
                    # But always check exits on every tick when in position
                    ind = self.get_indicators()
                    if self.check_exit(ind):
                        await self.exit()
                    continue

                if not new_bar:
                    continue

                # New bar completed - full strategy evaluation
                ind = self.get_indicators()

                # Check external signal
                ext = self.read_signal()
                if ext == 'close' and self.position != 0:
                    await self.exit()
                    continue
                if ext in ('long', 'short') and self.position == 0 and ind and ind['atr'] > 0:
                    await self.enter(ext, ind['atr'])
                    continue

                # Strategy signals
                if self.position != 0:
                    if self.check_exit(ind):
                        await self.exit()
                else:
                    entry = self.check_entry(ind)
                    if entry and ind:
                        await self.enter(entry, ind['atr'])

                # Periodic status
                if len(self.bars) % 5 == 0:
                    self._write_state()
                    pos_str = f"{'LONG' if self.position > 0 else 'SHORT'} {self.contracts}x" if self.position != 0 else "FLAT"
                    logger.info(f"[{len(self.bars)} bars] Price={self.price:.2f} | {pos_str} | "
                               f"Daily P&L=${self.daily_pnl:.2f} | Trades={self.daily_trades}")

                # Daily reset (midnight ET)
                now_et = datetime.now(timezone(timedelta(hours=-5)))
                if now_et.hour == 0 and now_et.minute < 2:
                    self.daily_pnl = 0
                    self.daily_trades = 0
                    self.emergency_stop = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Loop error: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def shutdown(self):
        self.running = False
        if self.position != 0 and self.price > 0:
            logger.warning("Closing open position on shutdown!")
            await self.exit()
        self._write_state()
        if self.connected:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        wr = (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0
        logger.info("=" * 60)
        logger.info(f"SESSION: {self.total_trades} trades | {wr:.1f}% wins | P&L=${self.total_pnl:.2f}")
        logger.info("=" * 60)


# ── Webhook Server ───────────────────────────────────────────────────
def start_webhook_server(port=8080):
    """HTTP server for TradingView / external signal webhooks."""
    signal_file = Path(__file__).parent / "signal.json"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                data = json.loads(body) if body else {}
                signal_file.write_text(json.dumps(data))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
                logger.info(f"WEBHOOK: {data}")
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode())

        def do_GET(self):
            # Return bot state
            state_file = Path(__file__).parent / "bot_state.json"
            try:
                state = state_file.read_text() if state_file.exists() else '{}'
            except Exception:
                state = '{}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(state.encode())

        def log_message(self, fmt, *args):
            pass

    srv = HTTPServer(('0.0.0.0', port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    logger.info(f"Webhook server on port {port}")
    logger.info(f"  POST signal: curl -X POST http://localhost:{port} -d '{{\"action\":\"long\"}}'")
    logger.info(f"  GET status:  curl http://localhost:{port}")
    return srv


# ── Main ─────────────────────────────────────────────────────────────
async def main():
    import argparse
    p = argparse.ArgumentParser(description="Rithmic MNQ Trading Bot")
    p.add_argument('--live', action='store_true', help='Live trading mode')
    p.add_argument('--symbol', default=os.getenv('TRADING_SYMBOL', 'MNQ'))
    p.add_argument('--exchange', default='CME')
    p.add_argument('--webhook-port', type=int, default=8080)
    p.add_argument('--no-webhook', action='store_true')
    args = p.parse_args()

    bot = RithmicTradingBot(symbol=args.symbol, exchange=args.exchange, demo=not args.live)

    loop = asyncio.get_event_loop()
    for s in (sig.SIGINT, sig.SIGTERM):
        loop.add_signal_handler(s, lambda: asyncio.create_task(bot.shutdown()))

    try:
        await bot.connect()
        if not args.no_webhook:
            start_webhook_server(args.webhook_port)
        await bot.run()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.error(f"Fatal: {e}", exc_info=True)
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
