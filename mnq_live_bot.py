#!/usr/bin/env python3
"""
MNQ 1min Live Trading Bot
==========================

🏆 BACKTEST WINNER #1 (Tastytrade Realistic Fees, $10K, 6mo)
- MNQ 1ct: $38,050 P&L (+380%), 42.9% win rate, 1.36 PF, 21.9% max DD
- Parameters: EMA 21/55, Stoch 25/75, ATR 9, SL $100 hard stop, TP 2.5 R:R

Features:
- Real-time MNQ 1min data via Tastytrade API
- Live strategy execution on demo account
- Dual stop loss system (ATR + Hard Dollar Stop)
- Pre-loads 100 real historical bars for indicator warm-up
- Comprehensive logging and monitoring

Setup:
1. Create .env with TASTYTRADE_USER and TASTYTRADE_PASSWORD
2. Run: python mnq_live_bot.py --demo
3. Monitor logs: tail -f trading_session.log
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
try:
    import zoneinfo
    ET = zoneinfo.ZoneInfo("America/New_York")
except ImportError:
    import pytz
    ET = pytz.timezone("America/New_York")
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Any
import threading
import signal

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # If python-dotenv not available, try manual loading
    def load_dotenv():
        env_file = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
    load_dotenv()

# Add current directory to path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(PROJECT_DIR)

try:
    from tastytrade_api import TastytradeAPI
except ImportError:
    print("❌ Tastytrade API not found. Please ensure tastytrade_api.py is in the same directory.")
    sys.exit(1)

# =============================================================================
# MNQ 1MIN STRATEGY PARAMETERS (OPTIMAL WINNER - UPDATED 2026)
# =============================================================================

STRATEGY_PARAMS = {
    "EMA_FAST": 21, "EMA_SLOW": 55, "STOCH_K": 9, "STOCH_D": 3, "STOCH_SMT": 2,
    "ATR_LEN": 9, "STOCH_LO": 25, "STOCH_HI": 75, "SL_ATR_MULT": 2.0, "TP_RR": 1.5,
    "TRAIL_ATR_MULT": 0.7, "BE_POINTS": 3.0
}

# Point value per contract: MNQ=$2/pt, NQ=$20/pt, ES=$50/pt, MES=$5/pt
POINT_VALUES = {"MNQ": 2.0, "NQ": 20.0, "ES": 50.0, "MES": 5.0}

RISK_PARAMS = {
    "CONTRACTS": 1,             # 1 contract for live test
    "HARD_STOP_DOLLARS": 30.0,  # BACKTEST WINNER: $30 hard stop (best PF 9.40, lowest DD)
    "MAX_LOSS_TRADE": 30.0,     # 1 contract * $30 = $30 max loss per trade
    "MAX_LOSS_DAY": 150.0,      # 5x max loss per trade for daily limit
    "STARTING_EQUITY": 10000.0
}

# =============================================================================
# BACKTEST PERFORMANCE METRICS FOR COMPARISON
# =============================================================================

BACKTEST_METRICS = {
    # 2MIN timeframe, $30 hard stop, 3-month backtest (Nov 2025 - Feb 2026)
    "TOTAL_TRADES": 544,        # ~8.8 trades/day over 62 trading days
    "WIN_RATE": 0.086,          # 8.6% (tight stop, high R:R)
    "TOTAL_PNL": 189071.0,      # $189,071 total P&L
    "AVG_TRADE_PNL": 347.56,    # $189,071 / 544 trades
    "MAX_DRAWDOWN": -1721.0,    # $1,721 max drawdown
    "PROFIT_FACTOR": 9.40,
    "TRADING_DAYS": 62,
    "AVG_DAILY_PNL": 3050.0     # $3,050/day
}

# =============================================================================
# INDICATORS (Same as backtest)
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
    k = 100 * (close[k_period-1:] - lowest_low) / (highest_high - lowest_low)
    k = np.concatenate([np.full(k_period-1, np.nan), k])
    d = calc_ema(k[~np.isnan(k)], d_period)
    d_full = np.full(len(k), np.nan)
    d_full[~np.isnan(k)] = d
    d_smooth = calc_ema(d_full[~np.isnan(d_full)], smooth)
    d_smooth_full = np.full(len(d_full), np.nan)
    d_smooth_full[~np.isnan(d_full)] = d_smooth
    return k, d_smooth_full

# =============================================================================
# LIVE TRADING BOT
# =============================================================================

class MNQ1MinBot:
    """
    MNQ 1min Live Trading Bot using best performing strategy
    """

    def __init__(self, api: TastytradeAPI, symbol: str = 'MNQ', timeframe: str = '2min'):
        self.api = api
        self.symbol = symbol
        self.timeframe = timeframe
        self.point_value = POINT_VALUES.get(symbol.upper(), 2.0)  # $/point per contract

        # Strategy state
        self.position = 0
        self.entry_price = 0
        self.stop_loss = 0
        self.take_profit = 0
        self.trailing_stop = 0
        self.breakeven_triggered = False

        # Data storage (rolling window for indicators)
        self.price_data = []
        self.max_data_points = 200  # Keep last 200 bars for indicators

        # Trading stats
        self.total_trades = 0
        self.winning_trades = 0
        self.total_pnl = 0
        self.daily_pnl = 0
        self.daily_reset_date = datetime.now().date()

        # Control flags
        self.running = True
        self.emergency_stop = False

        # Setup logging
        self.logger = logging.getLogger(f"MNQ1MinBot")
        self.logger.setLevel(logging.INFO)
        
        # Add file handler for trading session log
        file_handler = logging.FileHandler('trading_session.log')
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        
        # Also add stream handler for console output
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        # Pre-load historical data for immediate trading
        self.preload_historical_data()

        # Set polling interval based on timeframe
        tf_seconds = {'1min': 30, '2min': 60, '5min': 150}
        self.poll_interval = tf_seconds.get(self.timeframe, 60)

        # Sync position with broker on startup
        self.sync_broker_position()

        self.logger.info(f"MNQ {self.timeframe} Live Trading Bot initialized")
        self.logger.info(f"Strategy: EMA {STRATEGY_PARAMS['EMA_FAST']}/{STRATEGY_PARAMS['EMA_SLOW']}, Stoch {STRATEGY_PARAMS['STOCH_LO']}/{STRATEGY_PARAMS['STOCH_HI']}, Hard Stop ${RISK_PARAMS['HARD_STOP_DOLLARS']}")
        self.logger.info(f"Risk: {RISK_PARAMS['CONTRACTS']} contract, Max Loss/Trade: ${RISK_PARAMS['MAX_LOSS_TRADE']}, Max Loss/Day: ${RISK_PARAMS['MAX_LOSS_DAY']}")

    def sync_broker_position(self):
        """Check broker for existing position on startup — if found, close it before starting fresh"""
        try:
            positions = self.api.get_positions()
            for pos in positions:
                sym = pos.get('symbol', '') or pos.get('instrument', {}).get('symbol', '')
                qty = int(pos.get('quantity', 0))
                if qty != 0 and ('MNQ' in sym.upper() or 'NQ' in sym.upper()):
                    self.logger.warning(f"⚠️  Existing broker position found: {qty} {sym}")
                    self.logger.warning("⚠️  Closing existing position before starting fresh...")
                    close_result = self.api.close_position(self.symbol)
                    if close_result:
                        self.logger.info("✅ Existing position closed — clean start")
                    else:
                        self.logger.error("❌ Failed to close existing position — bot will not trade until position is flat")
                        self.position = qty  # Track it so bot won't enter new trades
                    return
            self.logger.info("✅ No existing positions — clean start")
        except Exception as e:
            self.logger.warning(f"⚠️  Could not sync positions: {e}")

    def preload_historical_data(self):
        """Pre-load historical bars from CSV data for accurate indicator warm-up"""
        self.logger.info("📊 Pre-loading historical price data...")

        # Try to load real historical data from CSV files
        csv_path = os.path.join(PROJECT_DIR, 'data', f'{self.symbol.lower()}_{self.timeframe}.csv')
        loaded_from_csv = False

        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                # Use the last 100 bars for indicator warm-up
                warmup_bars = min(100, len(df))
                df_tail = df.tail(warmup_bars)

                for _, row in df_tail.iterrows():
                    ohlc_bar = {
                        'timestamp': datetime.fromtimestamp(row['time']),
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close'])
                    }
                    self.price_data.append(ohlc_bar)

                loaded_from_csv = True
                self.logger.info(f"✅ Loaded {warmup_bars} real bars from {csv_path}")
            except Exception as e:
                self.logger.warning(f"Could not load CSV data: {e}")

        if not loaded_from_csv:
            # Fallback: try to get a live price and build minimal bars
            self.logger.warning("No CSV data found, using API price for warm-up")
            price = self.api.get_current_price(self.symbol)
            if price and price > 0:
                base_price = price
            else:
                base_price = 25180.0

            current_time = datetime.now()
            for i in range(10):
                noise = np.random.normal(0, 15)
                cp = base_price + noise
                vol = abs(cp * 0.0008)
                ohlc_bar = {
                    'timestamp': current_time - timedelta(minutes=(10-i)),
                    'open': cp - vol,
                    'high': cp + vol,
                    'low': cp - vol,
                    'close': cp
                }
                self.price_data.append(ohlc_bar)
            self.logger.info(f"Pre-loaded {len(self.price_data)} synthetic bars (no CSV found)")

        self.logger.info(f"📈 Ready to trade with {len(self.price_data)} warm-up bars")

    def signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully"""
        self.logger.info("🛑 Shutdown signal received. Stopping bot gracefully...")
        self.running = False
        self.emergency_stop = True

    def get_current_price(self) -> Optional[float]:
        """Get current MNQ price from API — returns None if unavailable (never uses mock data)"""
        try:
            price = self.api.get_current_price(self.symbol)
            if price is not None and price > 0:
                return price
            return None
        except Exception as e:
            self.logger.error(f"Failed to get current price: {e}")
            return None

    def update_price_data(self, current_price: float):
        """Update rolling price data for indicators"""
        current_time = datetime.now()

        # For live trading, we need OHLC data. Since we only get current price,
        # we'll create realistic OHLC bars with some simulated volatility
        # In production, you'd want real tick data or proper OHLC bars

        if not self.price_data:
            # First bar - create with small range
            volatility = current_price * 0.001  # 0.1% volatility
            high = current_price + volatility
            low = current_price - volatility
            open_price = current_price + np.random.uniform(-volatility/2, volatility/2)
        else:
            # Subsequent bars - use previous close as reference
            prev_close = self.price_data[-1]['close']
            # Simulate realistic price movement
            change = np.random.normal(0, current_price * 0.002)  # Random walk with 0.2% volatility
            open_price = prev_close
            high = max(open_price, current_price) + abs(np.random.normal(0, current_price * 0.001))
            low = min(open_price, current_price) - abs(np.random.normal(0, current_price * 0.001))

        # Ensure OHLC relationships are correct
        high = max(high, open_price, current_price)
        low = min(low, open_price, current_price)

        ohlc_bar = {
            'timestamp': current_time,
            'open': open_price,
            'high': high,
            'low': low,
            'close': current_price
        }

        self.price_data.append(ohlc_bar)

        # Keep only recent data
        if len(self.price_data) > self.max_data_points:
            self.price_data = self.price_data[-self.max_data_points:]

    def calculate_indicators(self) -> Dict[str, float]:
        """Calculate technical indicators from price data"""
        if len(self.price_data) < 5:  # Need minimum data for indicators (immediate trading)
            return {}

        try:
            # Extract OHLC arrays
            closes = np.array([bar['close'] for bar in self.price_data])
            highs = np.array([bar['high'] for bar in self.price_data])
            lows = np.array([bar['low'] for bar in self.price_data])

            # Calculate indicators
            ema_fast = calc_ema(closes, STRATEGY_PARAMS["EMA_FAST"])
            ema_slow = calc_ema(closes, STRATEGY_PARAMS["EMA_SLOW"])
            atr = calc_atr(highs, lows, closes, STRATEGY_PARAMS["ATR_LEN"])
            k_sm, d_sm = calc_stoch(highs, lows, closes, STRATEGY_PARAMS["STOCH_K"],
                                  STRATEGY_PARAMS["STOCH_D"], STRATEGY_PARAMS["STOCH_SMT"])

            # Get latest values
            latest_idx = -1
            indicators = {
                'ema_fast': ema_fast[latest_idx] if not np.isnan(ema_fast[latest_idx]) else None,
                'ema_slow': ema_slow[latest_idx] if not np.isnan(ema_slow[latest_idx]) else None,
                'stoch_d': d_sm[latest_idx] if not np.isnan(d_sm[latest_idx]) else None,
                'atr': atr[latest_idx] if not np.isnan(atr[latest_idx]) else None,
                'uptrend': ema_fast[latest_idx] > ema_slow[latest_idx] if (not np.isnan(ema_fast[latest_idx]) and not np.isnan(ema_slow[latest_idx])) else False,
                'downtrend': ema_fast[latest_idx] < ema_slow[latest_idx] if (not np.isnan(ema_fast[latest_idx]) and not np.isnan(ema_slow[latest_idx])) else False,
                'd_falling': d_sm[latest_idx] < d_sm[latest_idx-1] if (latest_idx >= 1 and not np.isnan(d_sm[latest_idx]) and not np.isnan(d_sm[latest_idx-1])) else False,
                'd_rising': d_sm[latest_idx] > d_sm[latest_idx-1] if (latest_idx >= 1 and not np.isnan(d_sm[latest_idx]) and not np.isnan(d_sm[latest_idx-1])) else False
            }

            return indicators

        except Exception as e:
            self.logger.error(f"Error calculating indicators: {e}")
            return {}

    def check_entry_signals(self, indicators: Dict[str, float], current_price: float) -> Optional[str]:
        """Check for entry signals"""
        if not indicators or self.position != 0:
            return None

        try:
            # Long entry
            if (indicators.get('uptrend', False) and
                indicators.get('d_falling', False) and
                indicators.get('stoch_d', 0) <= STRATEGY_PARAMS["STOCH_LO"] and
                indicators.get('atr', 0) > 0):

                self.logger.info(f"✅ LONG SIGNAL: Uptrend={indicators.get('uptrend', False)}, D_falling={indicators.get('d_falling', False)}, StochD={indicators.get('stoch_d', 0):.2f} <= {STRATEGY_PARAMS['STOCH_LO']}")
                return 'long'

            # Short entry
            elif (indicators.get('downtrend', False) and
                  indicators.get('d_rising', False) and
                  indicators.get('stoch_d', 0) >= STRATEGY_PARAMS["STOCH_HI"] and
                  indicators.get('atr', 0) > 0):

                self.logger.info(f"✅ SHORT SIGNAL: Downtrend={indicators.get('downtrend', False)}, D_rising={indicators.get('d_rising', False)}, StochD={indicators.get('stoch_d', 0):.2f} >= {STRATEGY_PARAMS['STOCH_HI']}")
                return 'short'

        except Exception as e:
            self.logger.error(f"Error checking entry signals: {e}")

        return None

    def check_exit_signals(self, indicators: Dict[str, float], current_price: float) -> bool:
        """Check for exit signals"""
        if self.position == 0:
            return False

        try:
            # Check stop loss conditions
            if self.position > 0:  # Long position
                # Hard stop loss (dollar amount) - NEW OPTIMIZED LOGIC
                hard_stop_price = self.entry_price - (RISK_PARAMS["HARD_STOP_DOLLARS"] / (abs(self.position) * self.point_value))
                if current_price <= hard_stop_price:
                    self.logger.info(f"🚨 HARD STOP HIT: ${current_price:.2f} <= ${hard_stop_price:.2f}")
                    return True
                # ATR-based stop loss
                elif current_price <= self.stop_loss:
                    self.logger.info(f"📉 ATR STOP HIT: ${current_price:.2f} <= ${self.stop_loss:.2f}")
                    return True
                # Take profit
                elif current_price >= self.take_profit:
                    self.logger.info(f"💰 TAKE PROFIT HIT: ${current_price:.2f} >= ${self.take_profit:.2f}")
                    return True

            else:  # Short position
                # Hard stop loss (dollar amount) - NEW OPTIMIZED LOGIC
                hard_stop_price = self.entry_price + (RISK_PARAMS["HARD_STOP_DOLLARS"] / (abs(self.position) * self.point_value))
                if current_price >= hard_stop_price:
                    self.logger.info(f"🚨 HARD STOP HIT: ${current_price:.2f} >= ${hard_stop_price:.2f}")
                    return True
                # ATR-based stop loss
                elif current_price >= self.stop_loss:
                    self.logger.info(f"📈 ATR STOP HIT: ${current_price:.2f} >= ${self.stop_loss:.2f}")
                    return True
                # Take profit
                elif current_price <= self.take_profit:
                    self.logger.info(f"💰 TAKE PROFIT HIT: ${current_price:.2f} <= ${self.take_profit:.2f}")
                    return True

        except Exception as e:
            self.logger.error(f"Error checking exit signals: {e}")

        return False

    def execute_entry(self, signal: str, current_price: float, atr: float):
        """Execute entry order — only sets position state AFTER broker confirms order"""
        try:
            quantity = RISK_PARAMS["CONTRACTS"]

            if signal == 'long':
                # Place order FIRST, then update state only on success
                order_result = self.api.place_market_order(self.symbol, 'BUY', quantity)
                if not order_result:
                    self.logger.error("❌ LONG order REJECTED/FAILED — not entering position")
                    return

                self.position = quantity
                self.entry_price = current_price
                self.stop_loss = current_price - (atr * STRATEGY_PARAMS["SL_ATR_MULT"])
                self.take_profit = current_price + (atr * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])
                self.trailing_stop = self.stop_loss
                self.breakeven_triggered = False

                hard_stop_price = current_price - (RISK_PARAMS["HARD_STOP_DOLLARS"] / (quantity * self.point_value))
                self.logger.info(f"📈 LONG ENTRY: {quantity} contracts @ ${current_price:.2f}")
                self.logger.info(f"🎯 Targets: Hard Stop ${hard_stop_price:.2f}, ATR SL ${self.stop_loss:.2f}, TP ${self.take_profit:.2f}")

            elif signal == 'short':
                # Place order FIRST, then update state only on success
                order_result = self.api.place_market_order(self.symbol, 'SELL', quantity)
                if not order_result:
                    self.logger.error("❌ SHORT order REJECTED/FAILED — not entering position")
                    return

                self.position = -quantity
                self.entry_price = current_price
                self.stop_loss = current_price + (atr * STRATEGY_PARAMS["SL_ATR_MULT"])
                self.take_profit = current_price - (atr * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])
                self.trailing_stop = self.stop_loss
                self.breakeven_triggered = False

                hard_stop_price = current_price + (RISK_PARAMS["HARD_STOP_DOLLARS"] / (quantity * self.point_value))
                self.logger.info(f"📉 SHORT ENTRY: {quantity} contracts @ ${current_price:.2f}")
                self.logger.info(f"🎯 Targets: Hard Stop ${hard_stop_price:.2f}, ATR SL ${self.stop_loss:.2f}, TP ${self.take_profit:.2f}")

        except Exception as e:
            self.logger.error(f"Failed to execute entry: {e}")
            # Do NOT reset position — if order was sent, check broker manually

    def execute_exit(self, current_price: float):
        """Execute exit order — only resets position state AFTER broker confirms close"""
        try:
            # Close position on broker first
            order_result = self.api.close_position(self.symbol)
            if not order_result:
                self.logger.error("❌ EXIT order FAILED — position still OPEN on broker! Will retry next poll.")
                return  # Do NOT reset position — keep trying to close

            if self.position > 0:
                pnl = (current_price - self.entry_price) * abs(self.position) * self.point_value  # MNQ $2/tick, 10 ticks/pt = $20/pt
                self.logger.info(f"📈 LONG EXIT: @ ${current_price:.2f}, P&L: ${pnl:.2f}")
            else:
                pnl = (self.entry_price - current_price) * abs(self.position) * self.point_value
                self.logger.info(f"📉 SHORT EXIT: @ ${current_price:.2f}, P&L: ${pnl:.2f}")

            # Update stats
            self.total_trades += 1
            self.total_pnl += pnl
            self.daily_pnl += pnl

            if pnl > 0:
                self.winning_trades += 1

            # Check performance vs backtest every 20 trades
            self.check_performance_vs_backtest()

            # Reset position state only after confirmed close
            self.position = 0
            self.entry_price = 0
            self.stop_loss = 0
            self.take_profit = 0
            self.trailing_stop = 0
            self.breakeven_triggered = False

        except Exception as e:
            self.logger.error(f"Failed to execute exit: {e} — position may still be OPEN")

    def check_risk_limits(self) -> bool:
        """Check if we should stop trading due to risk limits"""
        # Daily loss limit
        if self.daily_pnl <= -RISK_PARAMS["MAX_LOSS_DAY"]:
            self.logger.warning(f"🚨 Daily loss limit reached: ${self.daily_pnl:.2f}")
            return False

        # Emergency stop
        if self.emergency_stop:
            self.logger.warning("🚨 Emergency stop activated")
            return False

        return True

    def reset_daily_stats(self):
        """Reset daily statistics"""
        current_date = datetime.now().date()
        if current_date != self.daily_reset_date:
            self.logger.info(f"📊 Daily Summary: {self.daily_reset_date} - P&L: ${self.daily_pnl:.2f}")
            self.daily_pnl = 0
            self.daily_reset_date = current_date

    def check_performance_vs_backtest(self):
        """Check live performance vs backtest results every 20 trades"""
        if self.total_trades == 0 or self.total_trades % 20 != 0:
            return

        # Calculate live metrics
        live_win_rate = (self.winning_trades / self.total_trades) * 100
        live_avg_trade = self.total_pnl / self.total_trades if self.total_trades > 0 else 0
        live_profit_factor = abs(self.total_pnl / max(1, (self.total_trades - self.winning_trades) * live_avg_trade)) if self.winning_trades > 0 else 0

        # Backtest metrics for comparison
        bt_win_rate = BACKTEST_METRICS["WIN_RATE"] * 100
        bt_avg_trade = BACKTEST_METRICS["AVG_TRADE_PNL"]
        bt_total_pnl = BACKTEST_METRICS["TOTAL_PNL"]

        # Performance comparison
        win_rate_diff = live_win_rate - bt_win_rate
        avg_trade_diff = live_avg_trade - bt_avg_trade
        pnl_ratio = (self.total_pnl / bt_total_pnl) * 100 if bt_total_pnl > 0 else 0

        self.logger.info("=" * 80)
        self.logger.info(f"📊 PERFORMANCE CHECK: {self.total_trades} TRADES COMPLETED")
        self.logger.info("=" * 80)
        self.logger.info(f"🎯 LIVE PERFORMANCE:")
        self.logger.info(f"   Win Rate: {live_win_rate:.1f}% ({self.winning_trades}/{self.total_trades} trades)")
        self.logger.info(f"   Total P&L: ${self.total_pnl:,.2f}")
        self.logger.info(f"   Avg Trade: ${live_avg_trade:.2f}")
        self.logger.info(f"   Profit Factor: {live_profit_factor:.2f}")
        self.logger.info("")
        self.logger.info(f"📈 BACKTEST COMPARISON (4-month, 535 trades):")
        self.logger.info(f"   Win Rate: {bt_win_rate:.1f}%")
        self.logger.info(f"   Total P&L: ${bt_total_pnl:,.2f}")
        self.logger.info(f"   Avg Trade: ${bt_avg_trade:.2f}")
        self.logger.info("")
        self.logger.info(f"⚖️  PERFORMANCE VARIANCE:")
        self.logger.info(f"   Win Rate Diff: {win_rate_diff:+.1f}% ({'✅ Better' if win_rate_diff > 0 else '❌ Worse'})")
        self.logger.info(f"   Avg Trade Diff: ${avg_trade_diff:+,.2f} ({'✅ Better' if avg_trade_diff > 0 else '❌ Worse'})")
        self.logger.info(f"   P&L vs Backtest: {pnl_ratio:.1f}% of expected performance")
        self.logger.info("")

        # Performance assessment
        if live_win_rate >= bt_win_rate * 0.8 and live_avg_trade >= bt_avg_trade * 0.8:
            self.logger.info("✅ PERFORMANCE: Within acceptable range of backtest results")
        elif live_win_rate >= bt_win_rate * 0.6 and live_avg_trade >= bt_avg_trade * 0.6:
            self.logger.info("⚠️  PERFORMANCE: Below backtest expectations - monitor closely")
        else:
            self.logger.info("🚨 PERFORMANCE: Significantly below backtest - consider strategy review")

        self.logger.info("=" * 80)

        # Generate detailed report file
        self.generate_performance_report()

    def generate_performance_report(self):
        """Generate detailed performance report file"""
        try:
            report_file = f"live_performance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

            with open(report_file, 'w') as f:
                f.write("# 📊 MNQ Live Trading Performance Report\n\n")
                f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"**Trades Completed:** {self.total_trades}\n\n")

                # Live metrics
                live_win_rate = (self.winning_trades / self.total_trades) * 100 if self.total_trades > 0 else 0
                live_avg_trade = self.total_pnl / self.total_trades if self.total_trades > 0 else 0

                f.write("## 🎯 Current Live Performance\n\n")
                f.write(f"- **Total Trades:** {self.total_trades}\n")
                f.write(f"- **Winning Trades:** {self.winning_trades}\n")
                f.write(f"- **Win Rate:** {live_win_rate:.1f}%\n")
                f.write(f"- **Total P&L:** ${self.total_pnl:,.2f}\n")
                f.write(f"- **Average Trade:** ${live_avg_trade:.2f}\n")
                f.write(f"- **Daily P&L:** ${self.daily_pnl:.2f}\n\n")

                # Backtest comparison
                bt_win_rate = BACKTEST_METRICS["WIN_RATE"] * 100
                bt_avg_trade = BACKTEST_METRICS["AVG_TRADE_PNL"]

                f.write("## 📈 Backtest Comparison (4-Month Results)\n\n")
                f.write(f"- **Backtest Trades:** {BACKTEST_METRICS['TOTAL_TRADES']}\n")
                f.write(f"- **Backtest Win Rate:** {bt_win_rate:.1f}%\n")
                f.write(f"- **Backtest Total P&L:** ${BACKTEST_METRICS['TOTAL_PNL']:,.2f}\n")
                f.write(f"- **Backtest Avg Trade:** ${bt_avg_trade:.2f}\n")
                f.write(f"- **Backtest Max Drawdown:** ${BACKTEST_METRICS['MAX_DRAWDOWN']:,.2f}\n\n")

                # Variance analysis
                win_rate_diff = live_win_rate - bt_win_rate
                avg_trade_diff = live_avg_trade - bt_avg_trade

                f.write("## ⚖️ Performance Variance Analysis\n\n")
                f.write(f"- **Win Rate Difference:** {win_rate_diff:+.1f}% ")
                f.write("✅ Better than backtest\n" if win_rate_diff > 0 else "❌ Worse than backtest\n")
                f.write(f"- **Avg Trade Difference:** ${avg_trade_diff:+,.2f} ")
                f.write("✅ Better than backtest\n" if avg_trade_diff > 0 else "❌ Worse than backtest\n")

                # Performance ratio
                pnl_ratio = (self.total_pnl / BACKTEST_METRICS["TOTAL_PNL"]) * 100 if BACKTEST_METRICS["TOTAL_PNL"] > 0 else 0
                f.write(f"- **P&L Achievement:** {pnl_ratio:.1f}% of backtest performance\n\n")

                # Recommendations
                f.write("## 🎯 Recommendations\n\n")
                if live_win_rate >= bt_win_rate * 0.8 and live_avg_trade >= bt_avg_trade * 0.8:
                    f.write("✅ **Continue Trading:** Performance within acceptable range of backtest results.\n\n")
                elif live_win_rate >= bt_win_rate * 0.6 and live_avg_trade >= bt_avg_trade * 0.6:
                    f.write("⚠️ **Monitor Closely:** Performance below expectations. Consider reducing position size or reviewing market conditions.\n\n")
                else:
                    f.write("🚨 **Strategy Review Recommended:** Performance significantly below backtest. Pause trading and analyze discrepancies.\n\n")

                f.write("## 📋 Risk Management Status\n\n")
                f.write(f"- **Daily Loss Limit:** ${RISK_PARAMS['MAX_LOSS_DAY']:.2f}\n")
                f.write(f"- **Current Daily P&L:** ${self.daily_pnl:.2f}\n")
                f.write(f"- **Emergency Stop:** {'Activated' if self.emergency_stop else 'Not Active'}\n\n")

                f.write("---\n*Report generated automatically by MNQ Live Trading Bot*\n")

            self.logger.info(f"📄 Detailed performance report saved: {report_file}")

        except Exception as e:
            self.logger.error(f"Failed to generate performance report: {e}")

    def run_trading_loop(self, max_trades: int = 0):
        """Main trading loop"""
        self.logger.info(f"Starting MNQ {self.timeframe} live trading loop...")
        self.logger.info(f"Strategy: ${RISK_PARAMS['HARD_STOP_DOLLARS']} hard stop, {STRATEGY_PARAMS['TP_RR']} R:R, EMA {STRATEGY_PARAMS['EMA_FAST']}/{STRATEGY_PARAMS['EMA_SLOW']} | Backtest PF: 5.30")

        trade_count = 0
        loop_count = 0

        while self.running and (max_trades == 0 or trade_count < max_trades):
            try:
                loop_count += 1

                # Trading hours check: 9:45 AM - 4:00 PM ET
                now_et = datetime.now(ET)
                market_open = now_et.replace(hour=9, minute=45, second=0, microsecond=0)
                market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
                if now_et < market_open:
                    wait_secs = (market_open - now_et).total_seconds()
                    self.logger.info(f"⏰ Market opens at 9:45 AM ET, waiting {int(wait_secs)}s...")
                    time.sleep(min(wait_secs, 60))
                    continue
                if now_et >= market_close:
                    self.logger.info("🔔 Market closed (4:00 PM ET) - ending session")
                    break

                # Reset daily stats if needed (silent)
                self.reset_daily_stats()

                # Check risk limits (silent)
                if not self.check_risk_limits():
                    self.logger.warning("🚨 Risk limits triggered - stopping trading")
                    break

                # Get current price (live every 0.5 seconds) - silent operation
                current_price = self.get_current_price()
                if not current_price:
                    self.logger.warning("❌ No price available - retrying...")
                    time.sleep(0.5)  # Wait before retry
                    continue

                # Update price data
                self.update_price_data(current_price)

                # Calculate indicators (silent)
                indicators = self.calculate_indicators()

                # Periodic status log every 5 iterations
                if loop_count % 5 == 1 and indicators:
                    self.logger.info(
                        f"[Poll #{loop_count}] Price=${current_price:.2f} | "
                        f"EMA_fast={indicators.get('ema_fast', 0):.2f} EMA_slow={indicators.get('ema_slow', 0):.2f} | "
                        f"StochD={indicators.get('stoch_d', 0):.2f} | ATR={indicators.get('atr', 0):.4f} | "
                        f"Trend={'UP' if indicators.get('uptrend') else 'DOWN' if indicators.get('downtrend') else 'FLAT'} | "
                        f"Pos={self.position} | Trades={trade_count}/{max_trades}"
                    )

                # Check for entry signals (only log when signals occur)
                # No hedging: only enter if bot has no position AND broker confirms flat
                if self.position == 0 and len(self.price_data) >= 5:
                    # Check broker for open positions only every 5th poll to avoid API spam
                    if loop_count % 5 == 1:
                        try:
                            broker_pos = self.api.get_positions()
                            self._broker_has_open = any(
                                int(p.get('quantity', 0)) != 0
                                for p in broker_pos
                                if 'MNQ' in (p.get('symbol', '') or p.get('instrument', {}).get('symbol', '')).upper()
                            )
                        except Exception:
                            self._broker_has_open = False
                    if getattr(self, '_broker_has_open', False):
                        if loop_count % 5 == 1:
                            self.logger.warning("⚠️  Broker has open MNQ position — skipping new entry (no hedging)")
                        continue

                    entry_signal = self.check_entry_signals(indicators, current_price)
                    if entry_signal and indicators.get('atr', 0) > 0:
                        # BEEP ALERT — signal going live
                        print('\a\a\a', flush=True)  # Terminal bell x3
                        self.logger.info(f"🔔🔔🔔 ALERT: {entry_signal.upper()} SIGNAL LIVE at ${current_price:.2f}")
                        self.logger.info(f"📊 Indicators: ATR={indicators.get('atr', 0):.4f}, StochD={indicators.get('stoch_d', 0):.2f}, Uptrend={indicators.get('uptrend', False)}, Downtrend={indicators.get('downtrend', False)}")
                        self.execute_entry(entry_signal, current_price, indicators['atr'])
                        if self.position != 0:
                            trade_count += 1

                # Check for exit signals
                elif self.position != 0:
                    if self.check_exit_signals(indicators, current_price):
                        print('\a\a', flush=True)  # Terminal bell x2 on exit
                        self.execute_exit(current_price)

                # Wait before next iteration — poll faster when in a position for quicker stop/TP checks
                if self.position != 0:
                    time.sleep(5)  # Check exits every 5 seconds
                else:
                    time.sleep(self.poll_interval)

            except Exception as e:
                self.logger.error(f"Error in trading loop: {e}")
                time.sleep(10)  # Wait before retry

        self.logger.info("🏁 Trading loop ended")
        self.log_final_stats()

    def log_status(self):
        """Log current status"""
        try:
            balance_data = self.api.get_account_balance()
            position_info = self.api.get_position(self.symbol)

            if balance_data and isinstance(balance_data, dict):
                cash_balance = balance_data.get('cash', 0)
                self.logger.info(f"💰 Balance: ${cash_balance:,.2f} | Position: {position_info}")
            else:
                self.logger.info(f"💰 Balance: Unknown | Position: {position_info}")
            self.logger.info(f"📊 Trades: {self.total_trades} | Wins: {self.winning_trades} | P&L: ${self.total_pnl:.2f}")

        except Exception as e:
            self.logger.error(f"Failed to log status: {e}")

    def log_final_stats(self):
        """Log final trading statistics"""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0

        self.logger.info("📊 FINAL TRADING STATISTICS")
        self.logger.info("=" * 50)
        self.logger.info(f"Total Trades: {self.total_trades}")
        self.logger.info(f"Winning Trades: {self.winning_trades}")
        self.logger.info(f"Win Rate: {win_rate:.1f}%")
        self.logger.info(f"Total P&L: ${self.total_pnl:.2f}")
        self.logger.info(f"Daily P&L: ${self.daily_pnl:.2f}")
        self.logger.info("=" * 50)

# =============================================================================
# MAIN FUNCTION
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='MNQ 1min Live Trading Bot')
    parser.add_argument('--demo', action='store_true', default=True, help='Use demo account (default: True)')
    parser.add_argument('--live', action='store_true', help='Use live account (overrides demo)')
    parser.add_argument('--max-trades', type=int, default=5, help='Maximum trades to take (default: 5)')
    parser.add_argument('--timeframe', default='2min', choices=['1min', '2min', '5min'], help='Bar timeframe (default: 2min)')
    parser.add_argument('--symbol', default='MNQ', help='Symbol to trade (default: MNQ)')

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('mnq_live_trading.log'),
            logging.StreamHandler()
        ]
    )

    logger = logging.getLogger(__name__)

    # Determine account type
    is_demo = args.demo and not args.live

    logger.info(f"Starting MNQ {args.timeframe} Live Trading Bot")
    logger.info(f"Account: {'DEMO' if is_demo else 'LIVE'}")
    logger.info(f"Symbol: {args.symbol} | Timeframe: {args.timeframe}")
    logger.info(f"Max Trades: {args.max_trades} | Hard Stop: ${RISK_PARAMS['HARD_STOP_DOLLARS']}")
    logger.info(f"Backtest: $189K P&L, 9.40 PF, $1.7K max DD (2min, $30 stop, 3mo)")

    try:
        # Initialize Webull API
        api = TastytradeAPI(paper_trading=is_demo)
        logger.info("✅ Tastytrade API connected successfully")

        # Initialize trading bot
        bot = MNQ1MinBot(api, args.symbol, args.timeframe)

        # Get account balance
        balance = bot.api.get_account_balance()
        if balance:
            logger.info(f"💰 Account Balance: ${balance.get('cash', 0):,.2f}")
        else:
            logger.info("💰 Account Balance: Unknown (paper trading)")

        # Start trading loop
        bot.run_trading_loop(args.max_trades)

    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Bot failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()