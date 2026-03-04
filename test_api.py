#!/usr/bin/env python3
"""
Test Webull API Connection
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webull_api import WebullAPI

def test_api_connection():
    print("🔧 Testing Webull API Connection")
    print("=" * 40)

    # Check environment variables
    app_key = os.getenv('WEBULL_APP_KEY')
    app_secret = os.getenv('WEBULL_APP_SECRET')

    print(f"APP_KEY: {'✅ Set' if app_key else '❌ Missing'}")
    print(f"APP_SECRET: {'✅ Set' if app_secret else '❌ Missing'}")

    if not app_key or not app_secret:
        print("❌ Environment variables not loaded properly")
        return

    # Mask credentials for display
    masked_key = app_key[:8] + "..." + app_key[-4:] if len(app_key) > 12 else app_key
    masked_secret = app_secret[:8] + "..." + app_secret[-4:] if len(app_secret) > 12 else app_secret

    print(f"APP_KEY: {masked_key}")
    print(f"APP_SECRET: {masked_secret}")

    try:
        print("\n🔌 Connecting to Webull API (Demo)...")
        api = WebullAPI(is_demo=True)
        print("✅ API connection successful!")

        # Test getting account info
        print("\n📊 Testing account info...")
        # Note: We can't easily test account balance without proper authentication
        print("⚠️  Account testing requires valid API credentials")

    except Exception as e:
        print(f"❌ API connection failed: {e}")
        print("\n🔍 Troubleshooting:")
        print("1. Verify API credentials are correct")
        print("2. Check if demo account is properly set up")
        print("3. Ensure API access is enabled in Webull developer portal")
        print("4. Check network connectivity")

if __name__ == "__main__":
    test_api_connection()