#!/usr/bin/env python3
"""
Rithmic MNQ Live Trading Bot (Apex Trader Funding)
====================================================

Baseline 3-contract MNQ strategy via Rithmic Protocol Buffer API.
Uses async-rithmic for market data and order execution.

Strategy: EMA 21/55 crossover + Stochastic Oscillator + ATR-based stops
  - Mean-reversion entries on Stoch oversold/overbought in trend direction
  - Dual stop-loss: ATR-based + hard dollar stop
  - Take profit at 1.8x risk-reward ratio

Setup:
  1. pip install async-rithmic
  2. Set credentials in .env:
       RITHMIC_USER=your_username
       RITHMIC_PASSWORD=your_password
       RITHMIC_SYSTEM_NAME=Apex
       RITHMIC_GATEWAY_URL=rituz01000.rithmic.com:443
  3. Run: python rithmic_mnq_bot.py --demo
"""

import os
import sys
import asyncio
import logging
import argparse
import uuid
from datetime import datetime, timedelta, time as dtime
from typing import Dict, Optional, Any
import numpy as np
import pandas as pd

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from async_rithmic import (
    RithmicClient, DataType, SysInfraType,
    TransactionType, OrderType, TimeBarType
)

# =============================================================================
# STRATEGY PARAMETERS (Baseline 3ct - from TRADING_SYSTEM_RESULTS.md)
# =============================================================================

STRATEGY_PARAMS = {
    "EMA_FAST": 21,
    "EMA_SLOW": 55,
    "STOCH_K": 9,
    "STOCH_D": 3,
    "STOCH_SMT": 2,
    "ATR_LEN": 9,
    "STOCH_LO": 25,
    "STOCH_HI": 75,
    "SL_ATR_MULT": 2.0,
    "TP_RR": 1.8,
    "TRAIL_ATR_MULT": 0.7,
    "BE_POINTS": 3.0,
}

RISK_PARAMS = {
    "CONTRACTS": 3,
    "HARD_STOP_DOLLARS": 25.0,      # $25 per contract hard stop
    "MAX_LOSS_TRADE": 75.0,         # 3 * $25 = $75 max per trade
    "MAX_LOSS_DAY": 225.0,          # 3x max loss per trade
    "MNQ_POINT_VALUE": 2.0,         # MNQ = $2 per point (0.25 tick = $0.50)
    "MNQ_TICK_SIZE": 0.25,
}

# NY Session trading hours
NY_SESSION_START = dtime(9, 30)
NY_SESSION_END = dtime(16, 0)

# =============================================================================
# INDICATORS (vectorized, same as backtest)
# =============================================================================

def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i - 1] * (1 - k)
    return out


def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = np.maximum(
        high - low,
        np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1)))
    )
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period - 1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               k_period: int, d_period: int, smooth: int):
    lowest_low = np.array([np.min(low[i - k_period + 1:i + 1]) for i in range(k_period - 1, len(low))])
    highest_high = np.array([np.max(high[i - k_period + 1:i + 1]) for i in range(k_period - 1, len(high))])
    denom = highest_high - lowest_low
    denom[denom == 0] = 1  # avoid division by zero
    k = 100 * (close[k_period - 1:] - lowest_low) / denom
    k = np.concatenate([np.full(k_period - 1, np.nan), k])
    d = calc_ema(k[~np.isnan(k)], d_period)
    d_full = np.full(len(k), np.nan)
    d_full[~np.isnan(k)] = d
    d_smooth = calc_ema(d_full[~np.isnan(d_full)], smooth)
    d_smooth_full = np.full(len(d_full), np.nan)
    d_smooth_full[~np.isnan(d_full)] = d_smooth
    return k, d_smooth_full


# =============================================================================
# RITHMIC MNQ BOT
# =============================================================================

class RithmicMNQBot:
    """
    Async MNQ trading bot using Rithmic/Apex via async-rithmic.
    Implements the Baseline 3ct strategy.
    """

    def __init__(self, client: RithmicClient, symbol: str = "MNQ",
                 exchange: str = "CME", max_trades: int = 10):
        self.client = client
        self.symbol = symbol
        self.exchange = exchange
        self.max_trades = max_trades

        # Will be set after connecting and resolving front-month
        self.trading_symbol = None
        self.account_id = None

        # Position state
        self.position = 0
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.take_profit = 0.0
        self.trailing_stop = 0.0

        # Price data (OHLC bars for indicators)
        self.bars: list = []
        self.current_bar: Optional[Dict] = None
        self.last_bar_minute: Optional[int] = None
        self.max_bars = 200

        # Live quote state
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.last_price = 0.0

        # Trading stats
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0.0
        self.daily_pnl = 0.0
        self.daily_reset_date = datetime.now().date()

        # Control
        self.running = True

        # Logger
        self.logger = logging.getLogger("RithmicMNQBot")

    # --------------------------------------------------------------------- #
    # Connection & Setup
    # --------------------------------------------------------------------- #

    async def setup(self):
        """Connect, resolve contract, and subscribe to data."""
        self.logger.info("Connecting to Rithmic...")
        await self.client.connect(plants=[
            SysInfraType.TICKER_PLANT,
            SysInfraType.ORDER_PLANT,
        ])
        self.logger.info("Connected!")

        # Get account
        if self.client.accounts:
            self.account_id = self.client.accounts[0]
            self.logger.info(f"Account: {self.account_id}")
        else:
            self.logger.warning("No accounts found - orders will use default account")

        # Resolve front-month MNQ contract
        ticker = self.client.plants.get(SysInfraType.TICKER_PLANT)
        if ticker:
            try:
                contract = await ticker.get_front_month_contract(self.symbol, self.exchange)
                self.trading_symbol = contract.symbol if hasattr(contract, 'symbol') else str(contract)
                self.logger.info(f"Front-month contract: {self.trading_symbol}")
            except Exception as e:
                self.logger.warning(f"Could not resolve front-month: {e}")
                self.trading_symbol = self.symbol
        else:
            self.trading_symbol = self.symbol

        # Pre-load historical bars from CSV for indicator warmup
        self._preload_historical_bars()

        # Subscribe to live market data
        self.client.on_tick += self._on_tick
        if ticker:
            await ticker.subscribe_to_market_data(
                self.trading_symbol, self.exchange,
                DataType.LAST_TRADE | DataType.BBO
            )
            self.logger.info(f"Subscribed to {self.trading_symbol} market data")

    def _preload_historical_bars(self):
        """Load historical 1min bars from CSV for indicator warmup."""
        project_dir = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(project_dir, 'data', f'{self.symbol.lower()}_1min.csv')

        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                warmup = min(100, len(df))
                for _, row in df.tail(warmup).iterrows():
                    self.bars.append({
                        'timestamp': datetime.fromtimestamp(row['time']),
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                    })
                self.logger.info(f"Pre-loaded {warmup} historical bars from {csv_path}")
            except Exception as e:
                self.logger.warning(f"Could not load CSV: {e}")
        else:
            self.logger.info("No historical CSV found, will trade once enough live bars accumulate")

    # --------------------------------------------------------------------- #
    # Market Data Callback
    # --------------------------------------------------------------------- #

    def _on_tick(self, data):
        """Handle incoming tick data (BBO + last trade)."""
        try:
            if hasattr(data, 'bid_price') and data.bid_price:
                self.best_bid = float(data.bid_price)
            if hasattr(data, 'ask_price') and data.ask_price:
                self.best_ask = float(data.ask_price)
            if hasattr(data, 'trade_price') and data.trade_price:
                self.last_price = float(data.trade_price)
            elif hasattr(data, 'close') and data.close:
                self.last_price = float(data.close)

            # Build 1-minute OHLC bars from ticks
            if self.last_price > 0:
                self._update_bar(self.last_price)
        except Exception as e:
            self.logger.error(f"Tick processing error: {e}")

    def _update_bar(self, price: float):
        """Aggregate ticks into 1-minute OHLC bars."""
        now = datetime.now()
        current_minute = now.replace(second=0, microsecond=0)
        minute_key = int(current_minute.timestamp())

        if self.last_bar_minute is None or minute_key != self.last_bar_minute:
            # Close previous bar and start new one
            if self.current_bar is not None:
                self.bars.append(self.current_bar)
                if len(self.bars) > self.max_bars:
                    self.bars = self.bars[-self.max_bars:]

            self.current_bar = {
                'timestamp': current_minute,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
            }
            self.last_bar_minute = minute_key
        else:
            # Update current bar
            if self.current_bar:
                self.current_bar['high'] = max(self.current_bar['high'], price)
                self.current_bar['low'] = min(self.current_bar['low'], price)
                self.current_bar['close'] = price

    # --------------------------------------------------------------------- #
    # Indicators
    # --------------------------------------------------------------------- #

    def calculate_indicators(self) -> Dict[str, Any]:
        """Calculate technical indicators from bar data."""
        if len(self.bars) < max(STRATEGY_PARAMS["EMA_SLOW"], STRATEGY_PARAMS["STOCH_K"]) + 5:
            return {}

        try:
            closes = np.array([b['close'] for b in self.bars])
            highs = np.array([b['high'] for b in self.bars])
            lows = np.array([b['low'] for b in self.bars])

            ema_fast = calc_ema(closes, STRATEGY_PARAMS["EMA_FAST"])
            ema_slow = calc_ema(closes, STRATEGY_PARAMS["EMA_SLOW"])
            atr = calc_atr(highs, lows, closes, STRATEGY_PARAMS["ATR_LEN"])
            k_vals, d_vals = calc_stoch(
                highs, lows, closes,
                STRATEGY_PARAMS["STOCH_K"],
                STRATEGY_PARAMS["STOCH_D"],
                STRATEGY_PARAMS["STOCH_SMT"],
            )

            idx = -1
            prev = -2

            def safe(arr, i):
                return float(arr[i]) if not np.isnan(arr[i]) else None

            ef = safe(ema_fast, idx)
            es = safe(ema_slow, idx)
            sd = safe(d_vals, idx)
            sd_prev = safe(d_vals, prev)
            a = safe(atr, idx)

            if any(v is None for v in [ef, es, sd, sd_prev, a]):
                return {}

            return {
                'ema_fast': ef,
                'ema_slow': es,
                'stoch_d': sd,
                'atr': a,
                'uptrend': ef > es,
                'downtrend': ef < es,
                'd_falling': sd < sd_prev,
                'd_rising': sd > sd_prev,
            }
        except Exception as e:
            self.logger.error(f"Indicator error: {e}")
            return {}

    # --------------------------------------------------------------------- #
    # Signal Detection
    # --------------------------------------------------------------------- #

    def check_entry_signal(self, ind: Dict) -> Optional[str]:
        """Check for long/short entry signals."""
        if not ind or self.position != 0:
            return None

        # Long: uptrend + stoch falling into oversold
        if (ind['uptrend'] and ind['d_falling']
                and ind['stoch_d'] <= STRATEGY_PARAMS["STOCH_LO"]
                and ind['atr'] > 0):
            return 'long'

        # Short: downtrend + stoch rising into overbought
        if (ind['downtrend'] and ind['d_rising']
                and ind['stoch_d'] >= STRATEGY_PARAMS["STOCH_HI"]
                and ind['atr'] > 0):
            return 'short'

        return None

    def check_exit(self, price: float) -> bool:
        """Check stop-loss and take-profit conditions."""
        if self.position == 0:
            return False

        qty = abs(self.position)
        pv = RISK_PARAMS["MNQ_POINT_VALUE"]
        hard_stop_points = RISK_PARAMS["HARD_STOP_DOLLARS"] / (qty * pv)

        if self.position > 0:
            hard_stop_price = self.entry_price - hard_stop_points
            if price <= hard_stop_price:
                self.logger.info(f"HARD STOP HIT (long): {price:.2f} <= {hard_stop_price:.2f}")
                return True
            if price <= self.stop_loss:
                self.logger.info(f"ATR STOP HIT (long): {price:.2f} <= {self.stop_loss:.2f}")
                return True
            if price >= self.take_profit:
                self.logger.info(f"TAKE PROFIT HIT (long): {price:.2f} >= {self.take_profit:.2f}")
                return True
        else:
            hard_stop_price = self.entry_price + hard_stop_points
            if price >= hard_stop_price:
                self.logger.info(f"HARD STOP HIT (short): {price:.2f} >= {hard_stop_price:.2f}")
                return True
            if price >= self.stop_loss:
                self.logger.info(f"ATR STOP HIT (short): {price:.2f} >= {self.stop_loss:.2f}")
                return True
            if price <= self.take_profit:
                self.logger.info(f"TAKE PROFIT HIT (short): {price:.2f} <= {self.take_profit:.2f}")
                return True

        return False

    # --------------------------------------------------------------------- #
    # Order Execution
    # --------------------------------------------------------------------- #

    async def enter_position(self, signal: str, price: float, atr: float):
        """Submit market order to enter position."""
        qty = RISK_PARAMS["CONTRACTS"]
        order_id = f"mnq_{signal}_{uuid.uuid4().hex[:8]}"

        if signal == 'long':
            txn = TransactionType.BUY
            self.stop_loss = price - (atr * STRATEGY_PARAMS["SL_ATR_MULT"])
            self.take_profit = price + (atr * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])
        else:
            txn = TransactionType.SELL
            self.stop_loss = price + (atr * STRATEGY_PARAMS["SL_ATR_MULT"])
            self.take_profit = price - (atr * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

        try:
            order_plant = self.client.plants.get(SysInfraType.ORDER_PLANT)
            kwargs = {}
            if self.account_id:
                kwargs['account_id'] = self.account_id

            result = await order_plant.submit_order(
                order_id=order_id,
                symbol=self.trading_symbol,
                exchange=self.exchange,
                qty=qty,
                transaction_type=txn,
                order_type=OrderType.MARKET,
                **kwargs,
            )

            self.position = qty if signal == 'long' else -qty
            self.entry_price = price
            self.trailing_stop = self.stop_loss

            self.logger.info(
                f"{'LONG' if signal == 'long' else 'SHORT'} ENTRY: "
                f"{qty} contracts @ {price:.2f} | "
                f"SL: {self.stop_loss:.2f} | TP: {self.take_profit:.2f} | "
                f"Order: {order_id}"
            )
            return result

        except Exception as e:
            self.logger.error(f"Order failed: {e}")
            self.position = 0
            return None

    async def exit_position(self, price: float):
        """Exit current position."""
        if self.position == 0:
            return

        try:
            order_plant = self.client.plants.get(SysInfraType.ORDER_PLANT)
            kwargs = {}
            if self.account_id:
                kwargs['account_id'] = self.account_id

            kwargs['symbol'] = self.trading_symbol
            kwargs['exchange'] = self.exchange

            await order_plant.exit_position(**kwargs)

            # Calculate P&L
            pv = RISK_PARAMS["MNQ_POINT_VALUE"]
            if self.position > 0:
                pnl = (price - self.entry_price) * abs(self.position) * pv
            else:
                pnl = (self.entry_price - price) * abs(self.position) * pv

            self.total_trades += 1
            self.total_pnl += pnl
            self.daily_pnl += pnl
            if pnl > 0:
                self.winning_trades += 1

            direction = "LONG" if self.position > 0 else "SHORT"
            self.logger.info(
                f"{direction} EXIT @ {price:.2f} | "
                f"P&L: ${pnl:.2f} | Total P&L: ${self.total_pnl:.2f} | "
                f"Trades: {self.total_trades}"
            )

            # Reset
            self.position = 0
            self.entry_price = 0.0
            self.stop_loss = 0.0
            self.take_profit = 0.0
            self.trailing_stop = 0.0

        except Exception as e:
            self.logger.error(f"Exit failed: {e}")

    # --------------------------------------------------------------------- #
    # Risk Management
    # --------------------------------------------------------------------- #

    def check_risk_limits(self) -> bool:
        """Return False if we should stop trading."""
        if self.daily_pnl <= -RISK_PARAMS["MAX_LOSS_DAY"]:
            self.logger.warning(f"Daily loss limit reached: ${self.daily_pnl:.2f}")
            return False
        return True

    def reset_daily_stats(self):
        today = datetime.now().date()
        if today != self.daily_reset_date:
            self.logger.info(f"Daily Summary ({self.daily_reset_date}): P&L ${self.daily_pnl:.2f}")
            self.daily_pnl = 0.0
            self.daily_reset_date = today

    @staticmethod
    def is_trading_hours() -> bool:
        """Check if within NY session hours."""
        now = datetime.now()
        return NY_SESSION_START <= now.time() <= NY_SESSION_END

    # --------------------------------------------------------------------- #
    # Main Loop
    # --------------------------------------------------------------------- #

    async def run(self):
        """Main async trading loop."""
        self.logger.info("=" * 60)
        self.logger.info("RITHMIC MNQ TRADING BOT STARTED")
        self.logger.info(f"Strategy: EMA {STRATEGY_PARAMS['EMA_FAST']}/{STRATEGY_PARAMS['EMA_SLOW']}")
        self.logger.info(f"Risk: {RISK_PARAMS['CONTRACTS']} contracts, "
                         f"${RISK_PARAMS['HARD_STOP_DOLLARS']} hard stop, "
                         f"${RISK_PARAMS['MAX_LOSS_DAY']} daily limit")
        self.logger.info(f"Max trades: {self.max_trades}")
        self.logger.info("=" * 60)

        while self.running:
            try:
                self.reset_daily_stats()

                if not self.check_risk_limits():
                    self.logger.warning("Risk limit hit - pausing until next session")
                    await asyncio.sleep(60)
                    continue

                if self.total_trades >= self.max_trades:
                    self.logger.info(f"Max trades ({self.max_trades}) reached - stopping")
                    break

                price = self.last_price
                if price <= 0:
                    await asyncio.sleep(1)
                    continue

                # Check exit first
                if self.position != 0:
                    if self.check_exit(price):
                        await self.exit_position(price)

                # Check entry
                if self.position == 0 and len(self.bars) >= 60:
                    ind = self.calculate_indicators()
                    signal = self.check_entry_signal(ind)
                    if signal and ind.get('atr', 0) > 0:
                        self.logger.info(
                            f"SIGNAL: {signal.upper()} | Price: {price:.2f} | "
                            f"StochD: {ind['stoch_d']:.1f} | ATR: {ind['atr']:.2f}"
                        )
                        await self.enter_position(signal, price, ind['atr'])

                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Loop error: {e}")
                await asyncio.sleep(5)

        # Clean up: exit any open position
        if self.position != 0 and self.last_price > 0:
            self.logger.info("Exiting open position before shutdown...")
            await self.exit_position(self.last_price)

        self._log_final_stats()

    def _log_final_stats(self):
        wr = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
        self.logger.info("=" * 60)
        self.logger.info("FINAL STATS")
        self.logger.info(f"  Trades: {self.total_trades}")
        self.logger.info(f"  Wins: {self.winning_trades} ({wr:.1f}%)")
        self.logger.info(f"  Total P&L: ${self.total_pnl:.2f}")
        self.logger.info(f"  Daily P&L: ${self.daily_pnl:.2f}")
        self.logger.info("=" * 60)

    async def shutdown(self):
        """Graceful shutdown."""
        self.running = False
        try:
            await self.client.disconnect()
        except Exception:
            pass


# =============================================================================
# MAIN
# =============================================================================

async def async_main(args):
    user = os.getenv('RITHMIC_USER')
    password = os.getenv('RITHMIC_PASSWORD')
    system_name = os.getenv('RITHMIC_SYSTEM_NAME', 'Apex')
    gateway_url = os.getenv('RITHMIC_GATEWAY_URL', 'rituz01000.rithmic.com:443')

    if not user or not password or user == 'your_rithmic_username':
        logger.error("Set RITHMIC_USER and RITHMIC_PASSWORD in .env file")
        sys.exit(1)

    client = RithmicClient(
        user=user,
        password=password,
        system_name=system_name,
        app_name="MNQ_Apex_Bot",
        app_version="1.0",
        url=gateway_url,
    )

    bot = RithmicMNQBot(
        client=client,
        symbol=args.symbol,
        exchange="CME",
        max_trades=args.max_trades,
    )

    try:
        await bot.setup()
        await bot.run()
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as e:
        logger.error(f"Bot failed: {e}")
    finally:
        await bot.shutdown()


def main():
    parser = argparse.ArgumentParser(description='Rithmic MNQ Live Trading Bot (Apex)')
    parser.add_argument('--symbol', default='MNQ', help='Base symbol (default: MNQ)')
    parser.add_argument('--max-trades', type=int, default=10, help='Max trades per session (default: 10)')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('rithmic_trading.log'),
            logging.StreamHandler(),
        ]
    )

    global logger
    logger = logging.getLogger(__name__)

    logger.info("Rithmic MNQ Trading Bot (Apex Trader Funding)")
    logger.info(f"Symbol: {args.symbol} | Max Trades: {args.max_trades}")

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
