#!/usr/bin/env python3
"""
Quick test of Tastytrade API authentication and price fetching
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from tastytrade_api import TastytradeAPI

def test_api():
    print("🔍 Testing Tastytrade API...")

    # Initialize API
    api = TastytradeAPI(paper_trading=True)

    if not api.session_token:
        print("❌ Authentication failed")
        return

    print("✅ Authentication successful")
    print(f"Account: {api.account_number}")

    # Test price fetching
    print("\n📈 Testing price fetching...")
    price = api.get_current_price("MNQ")

    if price:
        print(f"✅ Got MNQ price: ${price}")
    else:
        print("❌ Price fetching failed")

if __name__ == "__main__":
    test_api()