#!/bin/bash
# Tab 4: LOCAL - Development/Monitoring
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
[ -f .venv/bin/activate ] && source .venv/bin/activate
tail -f trading_session.log