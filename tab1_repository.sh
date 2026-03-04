#!/bin/bash
# Tab 1: REPOSITORY - Trading Bot
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
[ -f .venv/bin/activate ] && source .venv/bin/activate
python3 mnq_live_bot.py --demo --max-trades 100