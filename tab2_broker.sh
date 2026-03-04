#!/bin/bash
# Tab 2: BROKER EXCHANGE - API Monitor
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true

echo "🔄 Starting Tastytrade API Monitor (updates every 30 seconds)..."
echo "Press Ctrl+C to stop"
echo ""

while true; do
    echo "📡 $(date '+%H:%M:%S') - Testing API connection..."
    python3 test_tastytrade.py
    echo ""
    sleep 30
done
