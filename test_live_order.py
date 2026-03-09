#!/usr/bin/env python3
"""
Quick live order test: Buy 1 contract MNQ, wait 5 seconds, exit.
Usage: python test_live_order.py
"""

import sys
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_live_order")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from traderspost_webhook import TradersPostClient

def main():
    tp = TradersPostClient()

    if not tp.webhook_url:
        logger.error("TRADERSPOST_WEBHOOK_URL not set in .env — aborting")
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
