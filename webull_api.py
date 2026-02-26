#!/usr/bin/env python3
"""
Webull API Integration for Futures Trading
==========================================

This module provides integration with Webull's REST API for futures trading.
Supports demo and live accounts with order management, position tracking,
and real-time data streaming.

API Documentation References:
- https://developer.webull.com/apis/docs/reference/common-order-preview
- https://developer.webull.com/apis/docs/reference/common-order-place
- https://developer.webull.com/apis/docs/reference/order-batch-place
- https://developer.webull.com/apis/docs/reference/common-order-replace
- https://developer.webull.com/apis/docs/reference/common-order-cancel
- https://developer.webull.com/apis/docs/reference/futures-products
- https://developer.webull.com/apis/docs/reference/futures-instrument-list

Setup:
1. Get API credentials from Webull developer portal
2. Set environment variables: WEBULL_APP_KEY, WEBULL_APP_SECRET
3. For demo trading, use demo account credentials
"""

import os
import json
import time
import requests
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import hmac
import hashlib
import base64

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WebullAPI:
    """
    Webull API client for futures trading
    """

    def __init__(self, app_key: str = None, app_secret: str = None, is_demo: bool = True):
        """
        Initialize Webull API client

        Args:
            app_key: Webull app key (from env WEBULL_APP_KEY if not provided)
            app_secret: Webull app secret (from env WEBULL_APP_SECRET if not provided)
            is_demo: Use demo environment if True, live if False
        """
        self.app_key = app_key or os.getenv('WEBULL_APP_KEY')
        self.app_secret = app_secret or os.getenv('WEBULL_APP_SECRET')

        if not self.app_key or not self.app_secret:
            raise ValueError("Webull API credentials not found. Set WEBULL_APP_KEY and WEBULL_APP_SECRET environment variables.")

        # API endpoints
        if is_demo:
            self.base_url = "https://api-demo.webull.com"
        else:
            self.base_url = "https://api.webull.com"

        self.session = requests.Session()
        self.access_token = None
        self.refresh_token = None
        self.token_expires = None

        # Authenticate on init
        self._authenticate()

    def _authenticate(self) -> None:
        """
        Authenticate with Webull API and get access token
        """
        try:
            # Webull uses OAuth2 flow - this is a simplified version
            # In production, implement proper OAuth2 flow
            auth_url = f"{self.base_url}/oauth/token"

            # Create signature for authentication
            timestamp = str(int(time.time() * 1000))
            message = f"{self.app_key}{timestamp}"
            signature = base64.b64encode(
                hmac.new(self.app_secret.encode(), message.encode(), hashlib.sha256).digest()
            ).decode()

            headers = {
                'Content-Type': 'application/json',
                'WB-API-KEY': self.app_key,
                'WB-API-SIGNATURE': signature,
                'WB-API-TIMESTAMP': timestamp
            }

            data = {
                'grant_type': 'client_credentials',
                'scope': 'trade'
            }

            response = self.session.post(auth_url, headers=headers, json=data)
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data['access_token']
            self.refresh_token = token_data.get('refresh_token')
            self.token_expires = datetime.now() + timedelta(seconds=token_data['expires_in'])

            # Set authorization header for future requests
            self.session.headers.update({
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            })

            logger.info("Successfully authenticated with Webull API")

        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise

    def _refresh_token_if_needed(self) -> None:
        """
        Refresh access token if expired
        """
        if self.token_expires and datetime.now() >= self.token_expires:
            self._authenticate()

    def _make_request(self, method: str, endpoint: str, data: Dict = None, params: Dict = None) -> Dict:
        """
        Make authenticated API request

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without base URL)
            data: Request body data
            params: Query parameters

        Returns:
            API response as dict
        """
        self._refresh_token_if_needed()

        url = f"{self.base_url}{endpoint}"

        try:
            if method.upper() == 'GET':
                response = self.session.get(url, params=params)
            elif method.upper() == 'POST':
                response = self.session.post(url, json=data, params=params)
            elif method.upper() == 'PUT':
                response = self.session.put(url, json=data, params=params)
            elif method.upper() == 'DELETE':
                response = self.session.delete(url, params=params)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.HTTPError as e:
            logger.error(f"API request failed: {e}")
            logger.error(f"Response: {response.text if 'response' in locals() else 'No response'}")
            raise
        except Exception as e:
            logger.error(f"Request error: {e}")
            raise

    # =========================================================================
    # ACCOUNT METHODS
    # =========================================================================

    def get_account_info(self) -> Dict:
        """
        Get account information

        Returns:
            Account details including balance, positions, etc.
        """
        return self._make_request('GET', '/v1/account')

    def get_positions(self) -> List[Dict]:
        """
        Get current positions

        Returns:
            List of position objects
        """
        response = self._make_request('GET', '/v1/positions')
        return response.get('positions', [])

    def get_orders(self, status: str = None) -> List[Dict]:
        """
        Get orders

        Args:
            status: Filter by order status (pending, filled, cancelled, etc.)

        Returns:
            List of order objects
        """
        params = {}
        if status:
            params['status'] = status

        response = self._make_request('GET', '/v1/orders', params=params)
        return response.get('orders', [])

    # =========================================================================
    # ORDER METHODS
    # =========================================================================

    def preview_order(self, order_data: Dict) -> Dict:
        """
        Preview order before placing

        Args:
            order_data: Order parameters

        Returns:
            Order preview with fees, margin requirements, etc.
        """
        return self._make_request('POST', '/v1/orders/preview', data=order_data)

    def place_order(self, order_data: Dict) -> Dict:
        """
        Place a single order

        Args:
            order_data: Order parameters including:
                - symbol: Futures symbol (e.g., 'MNQ')
                - side: 'BUY' or 'SELL'
                - quantity: Number of contracts
                - order_type: 'MARKET', 'LIMIT', 'STOP', etc.
                - price: Limit price (for limit orders)
                - time_in_force: 'GTC', 'IOC', 'FOK'

        Returns:
            Order confirmation
        """
        return self._make_request('POST', '/v1/orders', data=order_data)

    def place_batch_orders(self, orders: List[Dict]) -> Dict:
        """
        Place multiple orders in batch

        Args:
            orders: List of order dictionaries

        Returns:
            Batch order confirmation
        """
        return self._make_request('POST', '/v1/orders/batch', data={'orders': orders})

    def replace_order(self, order_id: str, order_data: Dict) -> Dict:
        """
        Replace/modify an existing order

        Args:
            order_id: ID of order to replace
            order_data: New order parameters

        Returns:
            Updated order confirmation
        """
        return self._make_request('PUT', f'/v1/orders/{order_id}', data=order_data)

    def cancel_order(self, order_id: str) -> Dict:
        """
        Cancel an order

        Args:
            order_id: ID of order to cancel

        Returns:
            Cancellation confirmation
        """
        return self._make_request('DELETE', f'/v1/orders/{order_id}')

    def cancel_all_orders(self, symbol: str = None) -> Dict:
        """
        Cancel all orders, optionally filtered by symbol

        Args:
            symbol: Symbol to filter orders (optional)

        Returns:
            Cancellation confirmation
        """
        params = {}
        if symbol:
            params['symbol'] = symbol

        return self._make_request('DELETE', '/v1/orders', params=params)

    # =========================================================================
    # MARKET DATA METHODS
    # =========================================================================

    def get_futures_instruments(self) -> List[Dict]:
        """
        Get list of available futures instruments

        Returns:
            List of futures instruments
        """
        response = self._make_request('GET', '/v1/futures/instruments')
        return response.get('instruments', [])

    def get_futures_products(self) -> List[Dict]:
        """
        Get futures products information

        Returns:
            List of futures products
        """
        response = self._make_request('GET', '/v1/futures/products')
        return response.get('products', [])

    def get_market_data(self, symbol: str) -> Dict:
        """
        Get current market data for a symbol

        Args:
            symbol: Futures symbol

        Returns:
            Market data including price, volume, etc.
        """
        return self._make_request('GET', f'/v1/market-data/{symbol}')

    def get_historical_data(self, symbol: str, interval: str = '1m',
                          start_time: int = None, end_time: int = None,
                          limit: int = 1000) -> List[Dict]:
        """
        Get historical market data

        Args:
            symbol: Futures symbol
            interval: Time interval (1m, 5m, 15m, 1h, 1d, etc.)
            start_time: Start time (Unix timestamp)
            end_time: End time (Unix timestamp)
            limit: Maximum number of bars

        Returns:
            Historical price data
        """
        params = {
            'interval': interval,
            'limit': limit
        }
        if start_time:
            params['start_time'] = start_time
        if end_time:
            params['end_time'] = end_time

        response = self._make_request('GET', f'/v1/market-data/{symbol}/history', params=params)
        return response.get('bars', [])

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    def get_commission_rates(self, symbol: str) -> Dict:
        """
        Get commission rates for a symbol

        Args:
            symbol: Futures symbol

        Returns:
            Commission rate information
        """
        return self._make_request('GET', f'/v1/fees/commissions/{symbol}')

    def calculate_pnl(self, position: Dict, current_price: float) -> float:
        """
        Calculate P&L for a position

        Args:
            position: Position data from API
            current_price: Current market price

        Returns:
            Current P&L in dollars
        """
        entry_price = position['average_cost']
        quantity = position['quantity']
        side = position['side']

        if side.upper() == 'LONG':
            return (current_price - entry_price) * quantity * position.get('multiplier', 1)
        else:
            return (entry_price - current_price) * quantity * position.get('multiplier', 1)

# =========================================================================
# FUTURES TRADING INTEGRATION
# =========================================================================

class FuturesTrader:
    """
    High-level futures trading interface using Webull API
    """

    def __init__(self, api: WebullAPI, symbol: str = 'MNQ'):
        """
        Initialize futures trader

        Args:
            api: WebullAPI instance
            symbol: Futures symbol to trade
        """
        self.api = api
        self.symbol = symbol
        self.position = None
        self.pending_orders = []

        # Get contract specifications
        self.contract_specs = self._get_contract_specs()

    def _get_contract_specs(self) -> Dict:
        """
        Get contract specifications for the symbol

        Returns:
            Contract details including tick size, point value, etc.
        """
        instruments = self.api.get_futures_instruments()
        for instrument in instruments:
            if instrument['symbol'] == self.symbol:
                return instrument

        # Default specs for MNQ if not found
        return {
            'tick_size': 0.25,
            'point_value': 20.0,
            'multiplier': 1
        }

    def get_position(self) -> Optional[Dict]:
        """
        Get current position for the symbol

        Returns:
            Position data or None if no position
        """
        positions = self.api.get_positions()
        for pos in positions:
            if pos['symbol'] == self.symbol:
                return pos
        return None

    def get_current_price(self) -> float:
        """
        Get current market price

        Returns:
            Current price
        """
        market_data = self.api.get_market_data(self.symbol)
        return market_data.get('last_price', 0.0)

    def place_market_order(self, side: str, quantity: int) -> Dict:
        """
        Place market order

        Args:
            side: 'BUY' or 'SELL'
            quantity: Number of contracts

        Returns:
            Order confirmation
        """
        order_data = {
            'symbol': self.symbol,
            'side': side.upper(),
            'quantity': quantity,
            'order_type': 'MARKET',
            'time_in_force': 'GTC'
        }

        return self.api.place_order(order_data)

    def place_limit_order(self, side: str, quantity: int, price: float) -> Dict:
        """
        Place limit order

        Args:
            side: 'BUY' or 'SELL'
            quantity: Number of contracts
            price: Limit price

        Returns:
            Order confirmation
        """
        order_data = {
            'symbol': self.symbol,
            'side': side.upper(),
            'quantity': quantity,
            'order_type': 'LIMIT',
            'price': price,
            'time_in_force': 'GTC'
        }

        return self.api.place_order(order_data)

    def place_stop_order(self, side: str, quantity: int, stop_price: float) -> Dict:
        """
        Place stop order

        Args:
            side: 'BUY' or 'SELL'
            quantity: quantity: Number of contracts
            stop_price: Stop price

        Returns:
            Order confirmation
        """
        order_data = {
            'symbol': self.symbol,
            'side': side.upper(),
            'quantity': quantity,
            'order_type': 'STOP',
            'stop_price': stop_price,
            'time_in_force': 'GTC'
        }

        return self.api.place_order(order_data)

    def close_position(self) -> Optional[Dict]:
        """
        Close current position with market order

        Returns:
            Order confirmation or None if no position
        """
        position = self.get_position()
        if not position:
            return None

        side = 'SELL' if position['side'].upper() == 'LONG' else 'BUY'
        quantity = abs(position['quantity'])

        return self.place_market_order(side, quantity)

    def cancel_all_orders(self) -> Dict:
        """
        Cancel all pending orders for the symbol

        Returns:
            Cancellation confirmation
        """
        return self.api.cancel_all_orders(self.symbol)

    def get_account_balance(self) -> float:
        """
        Get account balance

        Returns:
            Account balance in dollars
        """
        account = self.api.get_account_info()
        return account.get('cash_balance', 0.0)

    def get_daily_pnl(self) -> float:
        """
        Get daily P&L

        Returns:
            Daily P&L in dollars
        """
        account = self.api.get_account_info()
        return account.get('daily_pnl', 0.0)

# =========================================================================
# DEMO TRADING EXAMPLE
# =========================================================================

def demo_trading():
    """
    Example usage of the Webull API for futures trading
    """
    # Initialize API (set environment variables first)
    api = WebullAPI(is_demo=True)

    # Create trader instance
    trader = FuturesTrader(api, 'MNQ')

    # Get account info
    balance = trader.get_account_balance()
    print(f"Account Balance: ${balance}")

    # Get current price
    price = trader.get_current_price()
    print(f"Current MNQ Price: ${price}")

    # Get position
    position = trader.get_position()
    if position:
        print(f"Current Position: {position}")
    else:
        print("No open position")

    # Example: Place a small market order (uncomment to test)
    # order = trader.place_market_order('BUY', 1)
    # print(f"Order placed: {order}")

if __name__ == "__main__":
    demo_trading()