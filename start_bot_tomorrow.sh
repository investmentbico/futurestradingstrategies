#!/bin/bash
# Start MNQ bot at 9:45 AM ET tomorrow (March 6, 2026)
# Usage: nohup ./start_bot_tomorrow.sh &

cd /home/user/futurestradingstrategies
source venv/bin/activate 2>/dev/null || true

TARGET="2026-03-06 14:45:00"  # 9:45 AM ET = 14:45 UTC

echo "Waiting until $TARGET UTC (9:45 AM ET) to start bot..."

while true; do
    NOW=$(date -u +"%Y-%m-%d %H:%M:%S")
    if [[ "$NOW" > "$TARGET" ]] || [[ "$NOW" == "$TARGET" ]]; then
        break
    fi
    sleep 30
done

echo "Starting MNQ bot at $(date)..."
python mnq_live_bot.py --live --max-trades 10 --timeframe 2min --symbol MNQ > /tmp/bot_out.log 2>&1
echo "Bot exited at $(date)"
