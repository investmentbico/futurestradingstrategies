#!/bin/bash
# Tab 4: LOCAL - Log Monitor & Development
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true

echo "📋 Monitoring trading session log..."
echo "Press Ctrl+C to stop"
echo ""

# Create log file if it doesn't exist
touch trading_session.log

tail -f trading_session.log
