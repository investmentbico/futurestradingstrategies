#!/usr/bin/env python3
"""
Quick live order test: Buy 1 contract MNQ, wait 5 seconds, exit.
Usage: python test_live_order.py
"""

import os
import sys
import time
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_live_order")

# Load .env BEFORE importing traderspost_webhook (which also loads it)
env_path = Path(__file__).resolve().parent / '.env'
try:
    from dotenv import load_dotenv
    load_dotenv(env_path, override=True)
except ImportError:
    # Manual fallback
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ[key.strip()] = val.strip()

# Debug: verify the env var loaded
url = os.getenv('TRADERSPOST_WEBHOOK_URL', '')
if url:
    logger.info(f"Webhook URL loaded: {url[:60]}...")
else:
    logger.error(f"TRADERSPOST_WEBHOOK_URL not found. Checked .env at: {env_path}")
    logger.error(f".env exists: {env_path.exists()}")
    if env_path.exists():
        # Show relevant lines (redacted)
        for line in env_path.read_text().splitlines():
            if 'TRADERSPOST' in line:
                key = line.split('=')[0]
                logger.error(f"  Found key: {key} (length of value: {len(line.split('=', 1)[1]) if '=' in line else 0})")
    sys.exit(1)

from traderspost_webhook import TradersPostClient

def main():
    tp = TradersPostClient()

    if not tp.webhook_url:
        logger.error("TradersPostClient has no webhook URL despite env var being set")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("TEST: Buy 1 contract MNQ, hold 5 seconds, then exit")
    logger.info("=" * 60)

    # Step 1: Send buy order (1 contract)
    logger.info(">> Sending BUY 1 contract...")
    success, result = tp.send_long(quantity=1)
    logger.info(f"<< BUY result: success={success}, response={result}")

    if not success:
        logger.error("BUY failed — skipping exit")
        sys.exit(1)

    # Step 2: Wait 5 seconds
    logger.info(">> Holding position for 5 seconds...")
    time.sleep(5)

    # Step 3: Exit position
    logger.info(">> Sending EXIT (flatten)...")
    success, result = tp.send_exit()
    logger.info(f"<< EXIT result: success={success}, response={result}")

    logger.info("=" * 60)
    if success:
        logger.info("TEST COMPLETE — Check Tradovate to confirm both orders executed")
    else:
        logger.error("EXIT failed — check Tradovate for open positions!")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()
