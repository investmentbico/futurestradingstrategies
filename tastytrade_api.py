#!/usr/bin/env python3
"""
Tastytrade API Wrapper for MNQ Trading
OAuth2 authentication with REST API
"""

import os
import sys
import requests
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import warnings
warnings.filterwarnings('ignore')

class TastytradeAPI:
    """Tastytrade API wrapper for futures trading"""

    BASE_URL = "https://api.tastyworks.com"
    CERT_URL = "https://api.cert.tastyworks.com"  # For paper trading

    def __init__(self, username: str = None, password: str = None, client_id: str = None, client_secret: str = None, paper_trading: bool = True):
        """Initialize Tastytrade API connection"""
        self.username = username or os.getenv('TASTYTRADE_USER')
        self.password = password or os.getenv('TASTYTRADE_PASSWORD')
        self.client_id = client_id or os.getenv('TASTYTRADE_CLIENT_ID') or "18932fb1-8eb9-42c9-b96e-511fa936a42d"
        self.client_secret = client_secret or os.getenv('TASTYTRADE_CLIENT_SECRET') or "d68f7eae08499c6ca92d0f7a7e443b7de04e2d0d"
        self.paper_trading = paper_trading
        self.base_url = self.CERT_URL if paper_trading else self.BASE_URL
        self.session_token = None
        self.account_number = None

        if not self.username or not self.password:
            print("❌ Tastytrade credentials not found. Set TASTYTRADE_USER and TASTYTRADE_PASSWORD")
            return

        self.authenticate()

    def authenticate(self) -> bool:
        """Authenticate with Tastytrade API"""
        try:
            auth_url = f"{self.base_url}/sessions"
            payload = {
                "login": self.username,
                "password": self.password,
                "remember-me": True,
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }

            response = requests.post(auth_url, json=payload)
            response.raise_for_status()

            data = response.json()
            print(f"Debug: Auth response keys: {data.keys()}")  # Debug
            
            if 'data' in data:
                self.session_token = data['data']['session-token']
                if 'user' in data['data'] and 'accounts' in data['data']['user']:
                    self.account_number = data['data']['user']['accounts'][0]['account']['account-number']
                else:
                    # Try alternative structure
                    accounts = data['data'].get('accounts', [])
                    if accounts:
                        self.account_number = accounts[0]['account']['account-number']
                    else:
                        # Get accounts separately
                        self.account_number = self._get_account_number()
            else:
                # Direct response structure
                self.session_token = data.get('session-token')
                accounts = data.get('accounts', [])
                if accounts:
                    self.account_number = accounts[0]['account']['account-number']
                else:
                    self.account_number = self._get_account_number()

            print(f"✅ Tastytrade authenticated - Account: {self.account_number}")
            return True

        except Exception as e:
            print(f"❌ Tastytrade authentication failed: {e}")
            return False

    def _get_account_number(self) -> str:
        """Get account number from API"""
        try:
            url = f"{self.base_url}/customers/me/accounts"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()

            accounts = response.json()['data']['items']
            if accounts:
                return accounts[0]['account']['account-number']
            return "Unknown"
        except Exception as e:
            print(f"❌ Failed to get account number: {e}")
            return "Unknown"

    def _get_headers(self) -> Dict:
        """Get authorization headers"""
        return {
            'Authorization': f'Bearer {self.session_token}',
            'Content-Type': 'application/json'
        }

    def get_current_price(self, symbol: str = "MNQ") -> Optional[float]:
        """Get current price for MNQ"""
        try:
            # Try quotes endpoint for futures
            url = f"{self.base_url}/quotes/{symbol}"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()

            data = response.json()
            if 'data' in data and data['data']:
                quote = data['data'][0] if isinstance(data['data'], list) else data['data']
                return float(quote.get('last-price', 0))

        except Exception as e:
            print(f"❌ Failed to get {symbol} price via quotes endpoint: {e}")

        # Try market data endpoint
        try:
            url = f"{self.base_url}/market-data/quotes/{symbol}"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()

            data = response.json()
            if 'data' in data and data['data']:
                return float(data['data'].get('last-price', 0))

        except Exception as e:
            print(f"❌ Failed to get {symbol} price via market-data endpoint: {e}")

        return None

    def get_market_data(self, symbol: str = "MNQ", days: int = 1) -> List[Dict]:
        """Get historical market data"""
        # Tastytrade has limited historical data, use for recent data only
        try:
            # For backtesting, we'd need to use their bar data endpoint
            url = f"{self.base_url}/market-metrics/historicals/futures/{symbol}"
            params = {
                'interval': '1m',
                'days': days
            }

            response = requests.get(url, headers=self._get_headers(), params=params)
            response.raise_for_status()

            data = response.json()
            bars = []

            for item in data.get('data', []):
                bars.append({
                    'timestamp': item['time'],
                    'open': float(item['open']),
                    'high': float(item['high']),
                    'low': float(item['low']),
                    'close': float(item['close']),
                    'volume': int(item['volume'])
                })

            return bars

        except Exception as e:
            print(f"❌ Failed to get market data: {e}")
            return []

    def place_market_order(self, symbol: str, side: str, quantity: int) -> Optional[Dict]:
        """Place market order for MNQ"""
        try:
            order_url = f"{self.base_url}/accounts/{self.account_number}/orders"

            # MNQ futures order
            order_data = {
                "type": "Market",
                "time-in-force": "Day",
                "legs": [{
                    "instrument-type": "Future",
                    "symbol": symbol,
                    "quantity": quantity,
                    "action": side.upper()  # BUY or SELL
                }]
            }

            response = requests.post(order_url, headers=self._get_headers(), json=order_data)
            response.raise_for_status()

            order = response.json()
            print(f"✅ Order placed: {side} {quantity} {symbol} - ID: {order['data']['id']}")
            return order['data']

        except Exception as e:
            print(f"❌ Failed to place order: {e}")
            return None

    def get_account_balance(self) -> Optional[Dict]:
        """Get account balance"""
        try:
            url = f"{self.base_url}/accounts/{self.account_number}/balances"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()

            return response.json()['data']

        except Exception as e:
            print(f"❌ Failed to get balance: {e}")
            return None

    def get_positions(self) -> List[Dict]:
        """Get current positions"""
        try:
            url = f"{self.base_url}/accounts/{self.account_number}/positions"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()

            return response.json()['data']['items']

        except Exception as e:
            print(f"❌ Failed to get positions: {e}")
            return []

    def get_position(self, symbol: str = "MNQ") -> Optional[Dict]:
        """Get position for specific symbol"""
        positions = self.get_positions()
        for pos in positions:
            if pos.get('symbol') == symbol or pos.get('instrument', {}).get('symbol') == symbol:
                return pos
        return None

    def close_position(self, symbol: str = "MNQ") -> Optional[Dict]:
        """Close position for symbol"""
        position = self.get_position(symbol)
        if not position:
            return None

        quantity = abs(int(position.get('quantity', 0)))
        if quantity == 0:
            return None

        side = 'SELL' if position.get('quantity', 0) > 0 else 'BUY'
        return self.place_market_order(symbol, side, quantity)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            url = f"{self.base_url}/accounts/{self.account_number}/orders/{order_id}"
            response = requests.delete(url, headers=self._get_headers())
            response.raise_for_status()

            print(f"✅ Order {order_id} cancelled")
            return True

        except Exception as e:
            print(f"❌ Failed to cancel order: {e}")
            return False

    # Mock methods for backtesting compatibility
    def mock_get_current_price(self, symbol: str = "MNQ") -> float:
        """Mock price for testing"""
        # Return a reasonable MNQ price
        return 18000.0 + (time.time() % 1000) * 0.1

    def mock_get_market_data(self, symbol: str = "MNQ", days: int = 1) -> List[Dict]:
        """Mock market data"""
        return []

    def mock_place_market_order(self, symbol: str, side: str, quantity: int) -> Dict:
        """Mock order placement"""
        return {
            'id': f'mock_{int(time.time())}',
            'status': 'filled',
            'symbol': symbol,
            'side': side,
            'quantity': quantity
        }

# Global instance
tastytrade_api = None

def get_tastytrade_api(paper_trading: bool = True) -> TastytradeAPI:
    """Get or create Tastytrade API instance"""
    global tastytrade_api
    if tastytrade_api is None:
        tastytrade_api = TastytradeAPI(paper_trading=paper_trading)
    return tastytrade_api

if __name__ == "__main__":
    # Test connection
    api = get_tastytrade_api()
    if api.session_token:
        print("✅ Tastytrade API authenticated successfully!")
        print(f"📊 Account: {api.account_number}")
        print("📈 Note: Price endpoints may not work in paper trading environment")
        print("🚀 Ready for live trading with: python3 mnq_live_bot.py --live --max-trades 5")
        
        # Try to get price anyway
        price = api.get_current_price("MNQ")
        if price:
            print(f"📊 MNQ Price: ${price}")
        else:
            print("⚠️  MNQ price not available in paper environment (normal)")
    else:
        print("❌ Tastytrade API connection failed")