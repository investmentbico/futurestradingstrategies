#!/bin/bash
# iTerm 4-Tab Trading System Setup
# ==================================
# Open 4 tabs in iTerm and run one script per tab.
#
# Quick setup: In each iTerm tab, cd to this project folder and run:

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== MNQ 4-Tab Trading System ==="
echo "Project: $PROJECT_DIR"
echo ""
echo "Tab 1 - REPOSITORY (Trading Bot):"
echo "  bash $PROJECT_DIR/tab1_repository.sh"
echo ""
echo "Tab 2 - BROKER (API Monitor):"
echo "  bash $PROJECT_DIR/tab2_broker.sh"
echo ""
echo "Tab 3 - SERVER (Dashboard):"
echo "  bash $PROJECT_DIR/tab3_server.sh"
echo ""
echo "Tab 4 - LOCAL (Log Monitor):"
echo "  bash $PROJECT_DIR/tab4_local.sh"
echo ""
echo "=== Quick Commands ==="
echo "Run backtest:  cd $PROJECT_DIR && source venv/bin/activate && python3 comprehensive_backtest.py"
echo "Test API:      cd $PROJECT_DIR && source venv/bin/activate && python3 test_tastytrade.py"
echo "Kill all:      pkill -f 'python3.*mnq_live_bot\|live_dashboard'"
