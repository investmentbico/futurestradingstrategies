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

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

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
        # OAuth2 refresh token and provider secret
        self.provider_secret = os.getenv('TT_SECRET') or self.client_secret
        self.refresh_token = os.getenv('TT_REFRESH')
        self.paper_trading = paper_trading
        self.base_url = self.CERT_URL if paper_trading else self.BASE_URL
        self.session_token = None
        # Use account from env, or discover during auth
        self.account_number = os.getenv('TASTYTRADE_ACCOUNT') or None

        if not self.username or not self.password:
            print("❌ Tastytrade credentials not found. Set TASTYTRADE_USER and TASTYTRADE_PASSWORD")
            return

        # Try OAuth2 refresh token first (bypasses device challenge)
        if self.refresh_token and self.provider_secret:
            if self.authenticate_oauth():
                return

        # Fall back to session-based login
        self.authenticate()

    def authenticate_oauth(self) -> bool:
        """Authenticate using OAuth2 refresh token (bypasses device challenge)"""
        try:
            oauth_url = f"{self.base_url}/oauth/token"
            payload = {
                "grant_type": "refresh_token",
                "client_secret": self.provider_secret,
                "refresh_token": self.refresh_token,
            }

            print(f"🔐 Attempting OAuth2 refresh token authentication...")
            response = requests.post(oauth_url, json=payload)
            print(f"📡 OAuth response status: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                access_token = data.get('data', {}).get('access_token') or data.get('access_token')
                if access_token:
                    self.session_token = access_token
                    print(f"✅ OAuth2 access token obtained: {self.session_token[:20]}...")

                    # Get account number if not set
                    if not self.account_number:
                        self.account_number = self._get_account_number()

                    print(f"🎉 OAuth2 authenticated successfully - Account: {self.account_number}")
                    return True
                else:
                    print(f"⚠️ OAuth response missing access_token: {list(data.keys())}")
                    return False
            else:
                print(f"⚠️ OAuth2 auth failed ({response.status_code}): {response.text[:200]}")
                return False

        except Exception as e:
            print(f"⚠️ OAuth2 auth failed: {e}")
            return False

    def authenticate(self) -> bool:
        """Authenticate with Tastytrade API (session-based login)"""
        try:
            auth_url = f"{self.base_url}/sessions"
            payload = {
                "login": self.username,
                "password": self.password,
                "remember-me": True,
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }

            print(f"🔐 Attempting Tastytrade authentication for user: {self.username}")
            response = requests.post(auth_url, json=payload)
            print(f"📡 Auth response status: {response.status_code}")

            response.raise_for_status()

            data = response.json()
            print(f"📋 Auth response keys: {list(data.keys())}")

            if 'data' in data:
                self.session_token = data['data']['session-token']
                print(f"✅ Session token obtained: {self.session_token[:20]}...")

                if 'user' in data['data'] and 'accounts' in data['data']['user']:
                    # For live trading, get account from API
                    if not self.paper_trading:
                        self.account_number = data['data']['user']['accounts'][0]['account']['account-number']
                        print(f"✅ Account number found: {self.account_number}")
                    else:
                        print(f"✅ Using paper trading account: {self.account_number}")
                else:
                    # Try alternative structure for live trading
                    if not self.paper_trading:
                        accounts = data['data'].get('accounts', [])
                        if accounts:
                            self.account_number = accounts[0]['account']['account-number']
                            print(f"✅ Account number found (alt): {self.account_number}")
                        else:
                            # Get accounts separately
                            print("🔄 Getting account number separately...")
                            self.account_number = self._get_account_number()
                    else:
                        print(f"✅ Using paper trading account: {self.account_number}")
            else:
                # Direct response structure
                self.session_token = data.get('session-token')
                if not self.paper_trading:
                    accounts = data.get('accounts', [])
                    if accounts:
                        self.account_number = accounts[0]['account']['account-number']
                        print(f"✅ Account number found (direct): {self.account_number}")
                    else:
                        self.account_number = self._get_account_number()
                else:
                    print(f"✅ Using paper trading account: {self.account_number}")

            print(f"🎉 Tastytrade authenticated successfully - Account: {self.account_number}")
            return True

        except Exception as e:
            print(f"❌ Tastytrade authentication failed: {e}")
            print(f"🔍 Response content: {getattr(response, 'text', 'No response') if 'response' in locals() else 'No response'}")
            return False

    def _get_account_number(self) -> str:
        """Get account number from API"""
        try:
            url = f"{self.base_url}/customers/me/accounts"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()

            accounts = response.json()['data']['items']
            if accounts:
                return accounts[0]['account']['account-number']
            return "Unknown"
        except Exception as e:
            print(f"❌ Failed to get account number: {e}")
            return "Unknown"

    def _get_headers(self) -> Dict:
        """Get authorization headers - OAuth2 uses Bearer, session uses plain token"""
        token = self.session_token or ''
        # OAuth2 tokens need Bearer prefix; session tokens are sent as-is
        if token and not token.startswith('Bearer ') and len(token) > 100:
            # Long tokens are likely OAuth2 JWT access tokens
            auth_value = f"Bearer {token}"
        else:
            auth_value = token
        return {
            'Authorization': auth_value,
            'Content-Type': 'application/json'
        }

    def get_futures_symbol(self, product_code: str = "MNQ") -> Optional[str]:
        """Get the active front-month futures contract symbol (e.g. /MNQH6).
        Picks the nearest non-closing-only contract by expiration date."""
        try:
            url = f"{self.base_url}/instruments/futures"
            params = {'product-code': product_code}
            response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
            if response.status_code == 200:
                items = response.json().get('data', {}).get('items', [])
                # Filter to active contracts and sort by expiration (nearest first)
                active = [
                    item for item in items
                    if not item.get('is-closing-only', True) and item.get('symbol')
                ]
                active.sort(key=lambda x: x.get('expiration-date', '9999-12-31'))
                if active:
                    sym = active[0]['symbol']
                    print(f"  Resolved {product_code} -> {sym} (expires {active[0].get('expiration-date')})")
                    return sym
                # Fallback to first contract
                if items:
                    return items[0].get('symbol')
        except Exception as e:
            pass
        return None

    def get_current_price(self, symbol: str = "MNQ") -> Optional[float]:
        """Get current price — Tastytrade REST, then Yahoo Finance. All calls have 5s timeout."""
        # Try Tastytrade endpoints (usually 403 for futures quotes on this token)
        for endpoint in [
            f"{self.base_url}/quotes/futures/{symbol}",
            f"{self.base_url}/quotes/{symbol}",
            f"{self.base_url}/market-data/quotes/{symbol}",
        ]:
            try:
                response = requests.get(endpoint, headers=self._get_headers(), timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if 'data' in data and data['data']:
                        quote = data['data'][0] if isinstance(data['data'], list) else data['data']
                        price = float(quote.get('last-price', 0))
                        if price > 0:
                            return price
            except Exception:
                continue

        # Yahoo Finance fallback — use cached ticker to avoid repeated slow init
        try:
            import yfinance as yf
            if not hasattr(self, '_yf_ticker'):
                self._yf_ticker = yf.Ticker(f"{symbol}=F")
            data = self._yf_ticker.history(period='1d', interval='1m')
            if not data.empty:
                return float(data['Close'].iloc[-1])
        except Exception:
            pass

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

            response = requests.get(url, headers=self._get_headers(), params=params, timeout=10)
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
        """Place market order for futures. Resolves product code (MNQ) to active contract (/MNQH6)."""
        try:
            # Resolve to active futures contract symbol if needed
            futures_symbol = symbol if symbol.startswith('/') else self.get_futures_symbol(symbol)
            if not futures_symbol:
                futures_symbol = f"/{symbol}H6"  # Fallback to March 2026 contract
                print(f"  Using fallback symbol: {futures_symbol}")

            order_url = f"{self.base_url}/accounts/{self.account_number}/orders"

            # Tastytrade futures order format
            # BUY = open long, SELL = open short, BUY_CLOSE = cover short, SELL_CLOSE = close long
            side_upper = side.upper()
            if side_upper == 'BUY':
                action = 'Buy to Open'
            elif side_upper == 'SELL':
                action = 'Sell to Open'
            elif side_upper == 'BUY_CLOSE':
                action = 'Buy to Close'
            elif side_upper == 'SELL_CLOSE':
                action = 'Sell to Close'
            else:
                action = side

            order_data = {
                "order-type": "Market",
                "time-in-force": "Day",
                "legs": [{
                    "instrument-type": "Future",
                    "symbol": futures_symbol,
                    "quantity": quantity,
                    "action": action
                }]
            }

            print(f"  Placing order: {action} {quantity} {futures_symbol}")
            response = requests.post(order_url, headers=self._get_headers(), json=order_data, timeout=10)
            response.raise_for_status()

            order = response.json()
            order_id = order.get('data', {}).get('id', 'unknown')
            print(f"✅ Order placed: {action} {quantity} {futures_symbol} - ID: {order_id}")
            return order.get('data')

        except requests.exceptions.HTTPError as e:
            error_body = e.response.text if e.response else str(e)
            print(f"❌ Order failed ({e.response.status_code if e.response else '?'}): {error_body[:300]}")
            return None
        except Exception as e:
            print(f"❌ Failed to place order: {e}")
            return None

    def get_account_balance(self) -> Optional[Dict]:
        """Get real account balance from API"""
        try:
            url = f"{self.base_url}/accounts/{self.account_number}/balances"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()

            data = response.json().get('data', {})
            return {
                'cash': float(data.get('cash-balance', data.get('cash', 0))),
                'equity': float(data.get('net-liquidating-value', data.get('equity', 0))),
                'margin': float(data.get('maintenance-requirement', data.get('margin', 0))),
                'buying_power': float(data.get('derivative-buying-power', 0)),
                'account_number': self.account_number
            }

        except Exception as e:
            # Fallback for paper trading if API doesn't support balances
            if self.paper_trading:
                return {
                    'cash': 20000.0, 'equity': 20000.0,
                    'margin': 0.0, 'buying_power': 20000.0,
                    'account_number': self.account_number
                }
            return None

    def get_positions(self) -> List[Dict]:
        """Get current positions"""
        try:
            url = f"{self.base_url}/accounts/{self.account_number}/positions"
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()

            return response.json()['data']['items']

        except Exception as e:
            return []  # Silently return empty list on failure

    def get_position(self, symbol: str = "MNQ") -> Optional[Dict]:
        """Get position for specific symbol (matches product code like MNQ in /MNQH6)"""
        positions = self.get_positions()
        symbol_upper = symbol.upper()
        for pos in positions:
            pos_sym = (pos.get('symbol', '') or pos.get('instrument', {}).get('symbol', '')).upper()
            qty = int(pos.get('quantity', 0))
            # Match exact symbol or product code within contract symbol
            if qty != 0 and (pos_sym == symbol_upper or symbol_upper in pos_sym):
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

        # Resolve to active futures contract
        futures_symbol = position.get('symbol') or self.get_futures_symbol(symbol)
        if not futures_symbol:
            futures_symbol = f"/{symbol}H6"

        # Determine close action
        is_long = int(position.get('quantity', 0)) > 0
        action = 'Sell to Close' if is_long else 'Buy to Close'

        try:
            order_url = f"{self.base_url}/accounts/{self.account_number}/orders"
            order_data = {
                "order-type": "Market",
                "time-in-force": "Day",
                "legs": [{
                    "instrument-type": "Future",
                    "symbol": futures_symbol,
                    "quantity": quantity,
                    "action": action
                }]
            }
            print(f"  Closing: {action} {quantity} {futures_symbol}")
            response = requests.post(order_url, headers=self._get_headers(), json=order_data, timeout=10)
            response.raise_for_status()
            return response.json().get('data')
        except Exception as e:
            print(f"❌ Failed to close position: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            url = f"{self.base_url}/accounts/{self.account_number}/orders/{order_id}"
            response = requests.delete(url, headers=self._get_headers(), timeout=10)
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