#!/bin/bash
# Tab 2: BROKER EXCHANGE - API Monitor
cd /Users/sunflowerhd/Desktop/FUTURE
source .venv/bin/activate

echo "🔄 Starting Tastytrade API Monitor (updates every 5 seconds)..."
echo "Press Ctrl+C to stop"
echo ""

while true; do
    echo "📡 $(date '+%H:%M:%S') - Testing API connection..."
    python3 test_tastytrade.py
    echo ""
    sleep 5
done