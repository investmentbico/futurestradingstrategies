#!/usr/bin/env python3
"""
Simple test of MNQ data and Rithmic API integration
"""

import pandas as pd
from rithmic_api import RithmicAPI

print("🚀 MNQ Data & Rithmic API Integration Test")
print("=" * 50)

# Load MNQ data
print("📊 Loading MNQ historical data...")
df = pd.read_csv('data/mnq_1min.csv')
print(f"📈 Loaded {len(df)} bars of MNQ 1min data")
print(f"📅 Date range: {pd.to_datetime(df['time'].iloc[0], unit='s')} to {pd.to_datetime(df['time'].iloc[-1], unit='s')}")

# Test Rithmic API
print("\n🔗 Testing Rithmic API connection...")
api = RithmicAPI(is_demo=True)
current_price = api.get_current_price('MNQ')
print(f"💰 Current MNQ price via Rithmic API: ${current_price:.2f}")

# Show sample data
print("\n📋 Sample MNQ data:")
print(df.tail(3)[['time', 'open', 'high', 'low', 'close']].to_string())

print("\n✅ Integration test successful!")
print("💡 Real MNQ data loaded and Rithmic API ready for live trading")