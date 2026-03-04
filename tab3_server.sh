#!/bin/bash
# Tab 3: SERVER - Dashboard
cd /Users/sunflowerhd/Desktop/FUTURE
source .venv/bin/activate

echo "📊 Starting Live Trading Dashboard..."
echo "Press Ctrl+C to stop"
echo ""

# Check if we have a proper terminal for curses
if [ -t 0 ] && [ -t 1 ]; then
    python3 live_dashboard.py
else
    echo "❌ Dashboard requires a proper terminal (TTY)"
    echo "Please run this in an interactive iTerm tab"
    echo ""
    echo "Alternative: Monitor logs instead"
    tail -f trading_session.log
fi