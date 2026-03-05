#!/usr/bin/env python3
"""
Rithmic/Apex Connection Test
=============================
Tests connectivity to your Apex Trader Funding account via Rithmic Protocol Buffer API.

Usage:
  python test_rithmic_connection.py
"""

import os
import sys
import asyncio
import logging

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Apply proxy patch if in proxied environment
try:
    from rithmic_proxy import patch_websockets_for_proxy
    patch_websockets_for_proxy()
except (ImportError, Exception):
    pass

from async_rithmic import RithmicClient, DataType, SysInfraType

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def test_connection():
    user = os.getenv('RITHMIC_USER')
    password = os.getenv('RITHMIC_PASSWORD')
    system_name = os.getenv('RITHMIC_SYSTEM_NAME', 'Apex')
    gateway_url = os.getenv('RITHMIC_GATEWAY_URL', 'rituz00100.rithmic.com:443')

    if not user or not password or user == 'your_rithmic_username':
        logger.error("Set RITHMIC_USER and RITHMIC_PASSWORD in .env")
        return False

    logger.info("=" * 60)
    logger.info("RITHMIC / APEX CONNECTION TEST")
    logger.info("=" * 60)
    logger.info(f"User: {user}")
    logger.info(f"System: {system_name}")
    logger.info(f"Gateway: {gateway_url}")
    logger.info("")

    client = RithmicClient(
        user=user, password=password,
        system_name=system_name,
        app_name="MNQ_Trading_Bot", app_version="1.0",
        url=gateway_url,
    )

    try:
        # Step 1: Connect
        logger.info("[1/5] Connecting to Rithmic...")
        await client.connect(plants=[
            SysInfraType.TICKER_PLANT,
            SysInfraType.ORDER_PLANT,
        ])
        logger.info("  Connected!")

        # Step 2: List accounts
        logger.info("[2/5] Listing accounts...")
        accounts = client.accounts or []
        for acc in accounts:
            logger.info(f"  Account: {acc}")
        if not accounts:
            logger.warning("  No accounts found")

        # Step 3: Front-month contract
        logger.info("[3/5] Looking up front-month MNQ...")
        try:
            contract = await client.get_front_month_contract("MNQ", "CME")
            logger.info(f"  Front-month: {contract}")
        except Exception as e:
            logger.warning(f"  Could not get contract: {e}")

        # Step 4: Stream quotes
        logger.info("[4/5] Streaming MNQ quotes (5 seconds)...")
        quotes = []

        def on_tick(data):
            quotes.append(data)
            if len(quotes) <= 3:
                logger.info(f"  Tick: {data}")

        client.on_tick += on_tick
        try:
            await client.subscribe_to_market_data(
                "MNQ", "CME", DataType.LAST_TRADE | DataType.BBO
            )
        except Exception as e:
            logger.warning(f"  Subscribe error: {e}")

        await asyncio.sleep(5)
        logger.info(f"  Received {len(quotes)} ticks")

        # Step 5: Trade routes
        logger.info("[5/5] Checking trade routes...")
        try:
            routes = client.plants["order"].trade_routes
            logger.info(f"  Routes: {len(routes)}")
            for r in routes[:5]:
                logger.info(f"    {r}")
        except Exception as e:
            logger.warning(f"  Route error: {e}")

        logger.info("")
        logger.info("=" * 60)
        logger.info("CONNECTION TEST COMPLETE")
        logger.info("=" * 60)

        if accounts and quotes:
            logger.info("RESULT: ALL SYSTEMS GO")
            return True
        elif accounts:
            logger.info("RESULT: Connected (no market data - may be outside trading hours)")
            return True
        else:
            logger.info("RESULT: Issues detected - check credentials")
            return False

    except Exception as e:
        logger.error(f"Connection failed: {e}")
        logger.info("")
        logger.info("Troubleshooting:")
        logger.info("  1. Verify credentials from Apex dashboard")
        logger.info("  2. System name should be 'Apex'")
        logger.info("  3. Get gateway URL from Rithmic: rapi@rithmic.com")
        logger.info("  4. Close any other Rithmic sessions (R|Trader, NinjaTrader)")
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)
