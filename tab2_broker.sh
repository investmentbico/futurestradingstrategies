#!/bin/bash
# Tab 2: BROKER EXCHANGE - API Monitor
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
[ -f .venv/bin/activate ] && source .venv/bin/activate

echo "🔄 Starting Tastytrade API Monitor (updates every 5 seconds)..."
echo "Press Ctrl+C to stop"
echo ""

while true; do
    echo "📡 $(date '+%H:%M:%S') - Testing API connection..."
    python3 test_tastytrade.py
    echo ""
    sleep 5
done