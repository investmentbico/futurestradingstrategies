#!/bin/bash
# Tab 1: REPOSITORY - Trading Bot
# Auto-detect project directory
cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || source .venv/bin/activate 2>/dev/null || true

echo "🚀 Starting MNQ Live Trading Bot (Demo Mode)..."
echo "   Strategy: EMA 21/55, Stoch 25/75, ATR 9"
echo "   Risk: 3 contracts, $25 hard stop"
echo "   Press Ctrl+C to stop"
echo ""

python3 mnq_live_bot.py --demo --max-trades 100
