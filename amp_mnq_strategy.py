#!/usr/bin/env python3
"""
AMPFUTURES MNQ Strategy Implementation
Optimized for AMP Trader platform with FIX API
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import quickfix as fix
from typing import Dict, List, Optional, Any
import threading
import signal

# =============================================================================
# AMPFUTURES CONNECTION CONFIGURATION
# =============================================================================

AMP_CONFIG = {
    "FIX_HOST": "fix-ampfutures.com",
    "FIX_PORT": 443,
    "SENDER_COMP_ID": "YOUR_SENDER_ID",  # Provided by AMP
    "TARGET_COMP_ID": "AMPFUTURES",
    "USERNAME": os.getenv("AMP_USERNAME"),
    "PASSWORD": os.getenv("AMP_PASSWORD"),
    "ACCOUNT_ID": os.getenv("AMP_ACCOUNT_ID"),
    "SYMBOL": "MNQ",
    "EXCHANGE": "CME"
}

# =============================================================================
# MNQ STRATEGY PARAMETERS (WINNER CONFIGURATION)
# =============================================================================

STRATEGY_PARAMS = {
    "EMA_FAST": 34, "EMA_SLOW": 89, "STOCH_K": 14, "STOCH_D": 3, "STOCH_SMT": 3,
    "ATR_LEN": 14, "STOCH_LO": 30, "STOCH_HI": 70, "SL_ATR_MULT": 1.0, "TP_RR": 1.5,
    "TRAIL_ATR_MULT": 0.5, "BE_POINTS": 8.0
}

RISK_PARAMS = {
    "CONTRACTS": 15,  # OPTIMIZED: 15 contracts
    "HARD_STOP_DOLLARS": 80.0,  # OPTIMIZED: $80 hard stop
    "MAX_LOSS_TRADE": 1200.0,  # 15 * 80
    "MAX_LOSS_DAY": 2400.0,
    "STARTING_EQUITY": 100000.0
}

# =============================================================================
# AMPFUTURES FIX API CLIENT
# =============================================================================

class AMPFuturesClient(fix.Application):
    """
    AMPFUTURES FIX API Client for MNQ trading
    """

    def __init__(self):
        super().__init__()
        self.session_id = None
        self.logged_on = False
        self.order_id = 1
        self.positions = {}
        self.orders = {}

        # Setup logging
        self.logger = logging.getLogger('AMPFutures')
        self.logger.setLevel(logging.INFO)
        handler = logging.FileHandler('amp_trading.log')
        self.logger.addHandler(handler)

    def onCreate(self, session_id):
        self.session_id = session_id
        self.logger.info(f"Session created: {session_id}")

    def onLogon(self, session_id):
        self.logged_on = True
        self.logger.info("Successfully logged on to AMPFUTURES")

    def onLogout(self, session_id):
        self.logged_on = False
        self.logger.warning("Logged out from AMPFUTURES")

    def toAdmin(self, message, session_id):
        """Handle admin messages (logon, heartbeat, etc)"""
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)

        if msg_type.getValue() == fix.MsgType_Logon:
            # Set credentials for logon
            message.setField(fix.Username(AMP_CONFIG["USERNAME"]))
            message.setField(fix.Password(AMP_CONFIG["PASSWORD"]))
            message.setField(fix.ResetSeqNumFlag(True))

        self.logger.debug(f"Admin message: {message}")

    def fromAdmin(self, message, session_id):
        """Handle incoming admin messages"""
        self.logger.debug(f"Admin response: {message}")

    def toApp(self, message, session_id):
        """Handle outgoing application messages"""
        self.logger.debug(f"App message: {message}")

    def fromApp(self, message, session_id):
        """Handle incoming application messages"""
        msg_type = fix.MsgType()
        message.getHeader().getField(msg_type)

        if msg_type.getValue() == fix.MsgType_ExecutionReport:
            self._handle_execution_report(message)
        elif msg_type.getValue() == fix.MsgType_OrderCancelReject:
            self.logger.warning("Order cancel rejected")

    def _handle_execution_report(self, message):
        """Process order execution reports"""
        order_id = fix.OrderID()
        exec_type = fix.ExecType()
        ord_status = fix.OrdStatus()
        symbol = fix.Symbol()
        side = fix.Side()
        qty = fix.OrderQty()
        price = fix.LastPx()
        cum_qty = fix.CumQty()

        message.getField(order_id)
        message.getField(exec_type)
        message.getField(ord_status)

        oid = order_id.getValue()
        status = ord_status.getValue()

        if status == fix.OrdStatus_FILLED:
            message.getField(symbol)
            message.getField(side)
            message.getField(qty)
            message.getField(price)

            self.logger.info(f"Order {oid} FILLED: {qty.getValue()} {symbol.getValue()} @ ${price.getValue()}")

            # Update positions
            symbol_name = symbol.getValue()
            if symbol_name not in self.positions:
                self.positions[symbol_name] = 0

            if side.getValue() == fix.Side_BUY:
                self.positions[symbol_name] += qty.getValue()
            else:
                self.positions[symbol_name] -= qty.getValue()

        elif status == fix.OrdStatus_REJECTED:
            self.logger.error(f"Order {oid} REJECTED")

    def place_order(self, symbol: str, side: str, qty: int, price: float = None, order_type: str = "MARKET") -> str:
        """Place an order via FIX"""
        if not self.logged_on:
            raise Exception("Not logged on to AMPFUTURES")

        # Create new order message
        order = fix.Message()
        order.getHeader().setField(fix.MsgType(fix.MsgType_NewOrderSingle))

        # Set order fields
        order.setField(fix.ClOrdID(f"AMP_MNQ_{self.order_id}"))
        order.setField(fix.Symbol(symbol))
        order.setField(fix.Side(fix.Side_BUY if side.upper() == "BUY" else fix.Side_SELL))
        order.setField(fix.OrderQty(qty))
        order.setField(fix.OrdType(fix.OrdType_MARKET if order_type == "MARKET" else fix.OrdType_LIMIT))

        if order_type == "LIMIT" and price:
            order.setField(fix.Price(price))

        # AMP specific fields
        order.setField(fix.Account(AMP_CONFIG["ACCOUNT_ID"]))
        order.setField(fix.ExDestination(AMP_CONFIG["EXCHANGE"]))
        order.setField(fix.TimeInForce(fix.TimeInForce_DAY))

        # Send order
        fix.Session.sendToTarget(order, self.session_id)

        order_ref = f"AMP_MNQ_{self.order_id}"
        self.order_id += 1

        self.logger.info(f"Placed {order_type} order: {side} {qty} {symbol} @ {price or 'MARKET'} (ID: {order_ref})")

        return order_ref

    def cancel_order(self, order_id: str):
        """Cancel an order"""
        cancel = fix.Message()
        cancel.getHeader().setField(fix.MsgType(fix.MsgType_OrderCancelRequest))

        cancel.setField(fix.OrigClOrdID(order_id))
        cancel.setField(fix.ClOrdID(f"CANCEL_{order_id}"))
        cancel.setField(fix.Symbol(AMP_CONFIG["SYMBOL"]))

        fix.Session.sendToTarget(cancel, self.session_id)
        self.logger.info(f"Cancel request sent for order: {order_id}")

# =============================================================================
# MNQ STRATEGY BOT (AMP OPTIMIZED)
# =============================================================================

class AMPMNQBot:
    """
    MNQ Strategy Bot optimized for AMPFUTURES platform
    """

    def __init__(self, api_client: AMPFuturesClient):
        self.api = api_client
        self.symbol = AMP_CONFIG["SYMBOL"]

        # Strategy state
        self.position = 0
        self.entry_price = 0
        self.hard_stop_price = 0
        self.atr_stop_price = 0
        self.take_profit_price = 0

        # Data storage (rolling window for indicators)
        self.price_data = []
        self.max_data_points = 200

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
        self.logger = logging.getLogger('AMPMNQBot')
        self.logger.setLevel(logging.INFO)
        handler = logging.FileHandler('amp_mnq_strategy.log')
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

    def add_price_data(self, timestamp: datetime, open_price: float, high: float,
                      low: float, close: float, volume: int = 0):
        """Add new price data point"""
        self.price_data.append({
            'timestamp': timestamp,
            'open': open_price,
            'high': high,
            'low': low,
            'close': close,
            'volume': volume
        })

        # Maintain rolling window
        if len(self.price_data) > self.max_data_points:
            self.price_data.pop(0)

    def calculate_indicators(self) -> Dict[str, float]:
        """Calculate technical indicators"""
        if len(self.price_data) < 50:
            return {}

        try:
            closes = np.array([bar['close'] for bar in self.price_data])
            highs = np.array([bar['high'] for bar in self.price_data])
            lows = np.array([bar['low'] for bar in self.price_data])

            # Calculate indicators (same as backtest)
            ema_fast = self.calc_ema(closes, STRATEGY_PARAMS["EMA_FAST"])
            ema_slow = self.calc_ema(closes, STRATEGY_PARAMS["EMA_SLOW"])
            atr = self.calc_atr(highs, lows, closes, STRATEGY_PARAMS["ATR_LEN"])
            k_sm, d_sm = self.calc_stoch(highs, lows, closes, STRATEGY_PARAMS["STOCH_K"],
                                       STRATEGY_PARAMS["STOCH_D"], STRATEGY_PARAMS["STOCH_SMT"])

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

    # Indicator calculation methods (same as backtest)
    def calc_ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        k = 2.0 / (period + 1)
        out = np.empty_like(prices, dtype=float)
        out[0] = prices[0]
        for i in range(1, len(prices)):
            out[i] = prices[i] * k + out[i-1] * (1 - k)
        return out

    def calc_atr(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
        tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
        tr[0] = high[0] - low[0]
        out = np.empty(len(tr), dtype=float)
        out[:period] = np.nan
        out[period-1] = tr[:period].mean()
        for i in range(period, len(tr)):
            out[i] = (out[i-1] * (period - 1) + tr[i]) / period
        return out

    def calc_stoch(self, high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
        lowest_low = np.array([np.min(low[i-k_period+1:i+1]) for i in range(k_period-1, len(low))])
        highest_high = np.array([np.max(high[i-k_period+1:i+1]) for i in range(k_period-1, len(high))])
        k = 100 * (close[k_period-1:] - lowest_low) / (highest_high - lowest_low)
        k = np.concatenate([np.full(k_period-1, np.nan), k])
        d = self.calc_ema(k[~np.isnan(k)], d_period)
        d_full = np.full(len(k), np.nan)
        d_full[~np.isnan(k)] = d
        d_smooth = self.calc_ema(d_full[~np.isnan(d_full)], smooth)
        d_smooth_full = np.full(len(d_full), np.nan)
        d_smooth_full[~np.isnan(d_full)] = d_smooth
        return k, d_smooth_full

    def check_entry_signals(self, indicators: Dict[str, float], current_price: float) -> Optional[str]:
        """Check for entry signals"""
        if not indicators or self.position != 0:
            return None

        try:
            # Long entry - WINNER LOGIC
            if (indicators.get('uptrend', False) and
                indicators.get('d_falling', False) and
                indicators.get('stoch_d', 0) <= STRATEGY_PARAMS["STOCH_LO"] and
                indicators.get('atr', 0) > 0):

                return 'long'

            # Short entry - WINNER LOGIC
            elif (indicators.get('downtrend', False) and
                  indicators.get('d_rising', False) and
                  indicators.get('stoch_d', 0) >= STRATEGY_PARAMS["STOCH_HI"] and
                  indicators.get('atr', 0) > 0):

                return 'short'

        except Exception as e:
            self.logger.error(f"Error checking entry signals: {e}")

        return None

    def check_exit_signals(self, indicators: Dict[str, float], current_price: float) -> bool:
        """Check for exit signals - DUAL STOP SYSTEM"""
        if self.position == 0:
            return False

        try:
            # DUAL STOP LOSS SYSTEM - WINNER APPROACH
            if self.position > 0:  # Long position
                # PRIMARY: Hard stop loss ($80)
                if current_price <= self.hard_stop_price:
                    self.logger.info(f"🚨 HARD STOP HIT: ${current_price:.2f} <= ${self.hard_stop_price:.2f}")
                    return True
                # SECONDARY: ATR stop loss
                elif current_price <= self.atr_stop_price:
                    self.logger.info(f"📉 ATR STOP HIT: ${current_price:.2f} <= ${self.atr_stop_price:.2f}")
                    return True
                # Take profit
                elif current_price >= self.take_profit_price:
                    self.logger.info(f"💰 TAKE PROFIT HIT: ${current_price:.2f} >= ${self.take_profit_price:.2f}")
                    return True

            else:  # Short position
                # PRIMARY: Hard stop loss ($80)
                if current_price >= self.hard_stop_price:
                    self.logger.info(f"🚨 HARD STOP HIT: ${current_price:.2f} >= ${self.hard_stop_price:.2f}")
                    return True
                # SECONDARY: ATR stop loss
                elif current_price >= self.atr_stop_price:
                    self.logger.info(f"📈 ATR STOP HIT: ${current_price:.2f} >= ${self.atr_stop_price:.2f}")
                    return True
                # Take profit
                elif current_price <= self.take_profit_price:
                    self.logger.info(f"💰 TAKE PROFIT HIT: ${current_price:.2f} <= ${self.take_profit_price:.2f}")
                    return True

        except Exception as e:
            self.logger.error(f"Error checking exit signals: {e}")

        return False

    def execute_entry(self, signal: str, current_price: float, atr: float):
        """Execute entry order via AMP FIX API"""
        try:
            quantity = RISK_PARAMS["CONTRACTS"]

            if signal == 'long':
                self.position = quantity
                self.entry_price = current_price

                # DUAL STOP SYSTEM - WINNER APPROACH
                self.hard_stop_price = current_price - (RISK_PARAMS["HARD_STOP_DOLLARS"] / (quantity * 20))
                self.atr_stop_price = current_price - (atr * STRATEGY_PARAMS["SL_ATR_MULT"])
                self.take_profit_price = current_price + (atr * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

                # Place BUY order via AMP FIX
                order_id = self.api.place_order(self.symbol, "BUY", quantity)

                self.logger.info(f"📈 LONG ENTRY: {quantity} MNQ @ ${current_price:.2f}")
                self.logger.info(f"🎯 Stops: Hard ${self.hard_stop_price:.2f}, ATR ${self.atr_stop_price:.2f}, TP ${self.take_profit_price:.2f}")

            elif signal == 'short':
                self.position = -quantity
                self.entry_price = current_price

                # DUAL STOP SYSTEM - WINNER APPROACH
                self.hard_stop_price = current_price + (RISK_PARAMS["HARD_STOP_DOLLARS"] / (quantity * 20))
                self.atr_stop_price = current_price + (atr * STRATEGY_PARAMS["SL_ATR_MULT"])
                self.take_profit_price = current_price - (atr * STRATEGY_PARAMS["SL_ATR_MULT"] * STRATEGY_PARAMS["TP_RR"])

                # Place SELL order via AMP FIX
                order_id = self.api.place_order(self.symbol, "SELL", quantity)

                self.logger.info(f"📉 SHORT ENTRY: {quantity} MNQ @ ${current_price:.2f}")
                self.logger.info(f"🎯 Stops: Hard ${self.hard_stop_price:.2f}, ATR ${self.atr_stop_price:.2f}, TP ${self.take_profit_price:.2f}")

        except Exception as e:
            self.logger.error(f"Failed to execute entry: {e}")
            self.position = 0

    def execute_exit(self, current_price: float):
        """Execute exit order via AMP FIX API"""
        try:
            if self.position > 0:
                # Exit long position - SELL
                order_id = self.api.place_order(self.symbol, "SELL", abs(self.position))
                pnl = (current_price - self.entry_price) * abs(self.position) * 20
                self.logger.info(f"📈 LONG EXIT: @ ${current_price:.2f}, P&L: ${pnl:.2f}")
            else:
                # Exit short position - BUY
                order_id = self.api.place_order(self.symbol, "BUY", abs(self.position))
                pnl = (self.entry_price - current_price) * abs(self.position) * 20
                self.logger.info(f"📉 SHORT EXIT: @ ${current_price:.2f}, P&L: ${pnl:.2f}")

            # Update stats
            self.total_trades += 1
            self.total_pnl += pnl
            self.daily_pnl += pnl

            if pnl > 0:
                self.winning_trades += 1

            # Reset position
            self.position = 0
            self.entry_price = 0
            self.hard_stop_price = 0
            self.atr_stop_price = 0
            self.take_profit_price = 0

        except Exception as e:
            self.logger.error(f"Failed to execute exit: {e}")

    def run_strategy(self, price_feed):
        """Main strategy loop"""
        self.logger.info("🏆 Starting AMP MNQ Winner Strategy")
        self.logger.info(f"Parameters: 15 contracts, $80 hard stop, 64.22 PF")

        while self.running and not self.emergency_stop:
            try:
                # Get latest price data
                latest_bar = price_feed.get_latest_bar()
                if latest_bar:
                    self.add_price_data(
                        latest_bar['timestamp'],
                        latest_bar['open'],
                        latest_bar['high'],
                        latest_bar['low'],
                        latest_bar['close'],
                        latest_bar.get('volume', 0)
                    )

                    # Calculate indicators
                    indicators = self.calculate_indicators()
                    current_price = latest_bar['close']

                    # Check for entry signals
                    entry_signal = self.check_entry_signals(indicators, current_price)
                    if entry_signal:
                        atr_value = indicators.get('atr', 0)
                        if atr_value > 0:
                            self.execute_entry(entry_signal, current_price, atr_value)

                    # Check for exit signals
                    if self.check_exit_signals(indicators, current_price):
                        self.execute_exit(current_price)

                # Small delay to prevent excessive CPU usage
                time.sleep(1)

            except Exception as e:
                self.logger.error(f"Strategy loop error: {e}")
                time.sleep(5)

        self.logger.info("Strategy stopped")

# =============================================================================
# MAIN EXECUTION
# =============================================================================

def main():
    """Main function to run AMP MNQ strategy"""
    parser = argparse.ArgumentParser(description='AMP MNQ Winner Strategy')
    parser.add_argument('--config', help='FIX config file path')
    parser.add_argument('--demo', action='store_true', help='Run in demo mode')
    args = parser.parse_args()

    print("🏆 AMP FUTURES MNQ WINNER STRATEGY")
    print("=" * 50)
    print("15 contracts | $80 hard stop | 64.22 PF")
    print()

    # Initialize AMP FIX client
    client = AMPFuturesClient()

    # Load FIX configuration
    if args.config:
        settings = fix.SessionSettings(args.config)
    else:
        # Default configuration
        settings = fix.SessionSettings()
        session_id = fix.SessionID("FIX.4.4", AMP_CONFIG["SENDER_COMP_ID"], AMP_CONFIG["TARGET_COMP_ID"])
        settings.set(session_id, fix.ConnectionType("initiator"))
        settings.set(session_id, fix.SocketConnectHost(AMP_CONFIG["FIX_HOST"]))
        settings.set(session_id, fix.SocketConnectPort(AMP_CONFIG["FIX_PORT"]))
        settings.set(session_id, fix.StartTime("07:00:00"))
        settings.set(session_id, fix.EndTime("16:00:00"))

    # Initialize FIX engine
    store_factory = fix.FileStoreFactory(settings)
    log_factory = fix.FileLogFactory(settings)
    initiator = fix.SocketInitiator(client, store_factory, settings, log_factory)

    try:
        initiator.start()
        self.logger.info("AMP FIX connection started")

        # Wait for logon
        timeout = 30
        while not client.logged_on and timeout > 0:
            time.sleep(1)
            timeout -= 1

        if not client.logged_on:
            raise Exception("Failed to logon to AMPFUTURES")

        # Initialize strategy bot
        bot = AMPMNQBot(client)

        # TODO: Initialize price feed (would need AMP market data API)
        # For now, this is a template structure

        print("✅ Connected to AMP FUTURES")
        print("🏆 MNQ Winner Strategy Ready")
        print("Press Ctrl+C to stop")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n🛑 Stopping strategy...")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        initiator.stop()
        print("✅ AMP connection closed")

if __name__ == "__main__":
    main()