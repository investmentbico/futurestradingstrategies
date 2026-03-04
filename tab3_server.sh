#!/bin/bash
# Tab 3: SERVER - Dashboard
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true

echo "📊 Starting Live Trading Dashboard..."
echo "Press 'q' to quit, Ctrl+C to force stop"
echo ""

# Check if we have a proper terminal for curses
if [ -t 0 ] && [ -t 1 ]; then
    python3 live_dashboard.py
else
    echo "❌ Dashboard requires a proper terminal (TTY)"
    echo "Falling back to log monitoring..."
    echo ""
    tail -f trading_session.log
fi
