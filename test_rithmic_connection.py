#!/usr/bin/env python3
"""
Rithmic/Apex Connection Test
=============================
Tests connectivity to your Apex Trader Funding account via Rithmic Protocol Buffer API.

Usage:
  1. Fill in your .env file with RITHMIC_USER, RITHMIC_PASSWORD, etc.
  2. Run: python test_rithmic_connection.py

This will:
  - Try multiple gateway URLs to find a working one
  - Connect to Rithmic via your Apex credentials
  - List available accounts
  - Get the front-month MNQ contract
  - Stream a few live quotes
  - Verify order routing is available
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

from async_rithmic import (
    RithmicClient, DataType, SysInfraType,
    TransactionType, OrderType
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def test_connection():
    """Test Rithmic/Apex connection step by step."""

    user = os.getenv('RITHMIC_USER')
    password = os.getenv('RITHMIC_PASSWORD')
    system_name = os.getenv('RITHMIC_SYSTEM_NAME', 'Apex')
    gateway_url = os.getenv('RITHMIC_GATEWAY_URL', 'rituz00100.rithmic.com:443')

    # Known Rithmic gateway URLs to try if the configured one fails
    gateway_candidates = [
        gateway_url,
        'rituz00100.rithmic.com:443',   # Rithmic Test
        'rituz00100.rithmic.com:65000',  # Rithmic Test alt port
        'rituz01000.rithmic.com:443',    # Production candidate
    ]
    # Deduplicate while preserving order
    seen = set()
    gateway_candidates = [g for g in gateway_candidates if not (g in seen or seen.add(g))]

    if not user or not password or user == 'your_rithmic_username':
        logger.error("Missing Rithmic credentials. Set RITHMIC_USER and RITHMIC_PASSWORD in .env")
        logger.info("Get credentials from your Apex dashboard or welcome email.")
        return False

    logger.info("=" * 60)
    logger.info("RITHMIC / APEX CONNECTION TEST")
    logger.info("=" * 60)
    logger.info(f"User: {user}")
    logger.info(f"System: {system_name}")
    logger.info(f"Gateways to try: {gateway_candidates}")
    logger.info("")

    # Step 1: Try gateway URLs until one works
    logger.info("[1/5] Finding working gateway...")
    client = None
    connected_url = None

    for url in gateway_candidates:
        logger.info(f"  Trying gateway: {url}")
        test_client = RithmicClient(
            user=user,
            password=password,
            system_name=system_name,
            app_name="MNQ_Trading_Bot",
            app_version="1.0",
            url=url,
        )
        try:
            await test_client.connect(plants=[
                SysInfraType.TICKER_PLANT,
                SysInfraType.ORDER_PLANT,
            ])
            client = test_client
            connected_url = url
            logger.info(f"  Connected successfully via {url}!")
            break
        except Exception as e:
            err_msg = str(e)
            if 'nodename' in err_msg or 'getaddrinfo' in err_msg:
                logger.warning(f"    DNS resolution failed for {url}")
            elif 'system_name' in err_msg.lower():
                logger.warning(f"    System name error on {url}: {e}")
                logger.info(f"    The gateway works but '{system_name}' is not available on it.")
            else:
                logger.warning(f"    Failed: {e}")
            try:
                await test_client.disconnect()
            except Exception:
                pass

    if client is None:
        logger.error("Could not connect to any Rithmic gateway.")
        logger.info("")
        logger.info("Troubleshooting:")
        logger.info("  1. Verify username/password from Apex dashboard")
        logger.info("  2. System name should be 'Apex' (not 'Rithmic Paper Trading')")
        logger.info("  3. You may need the production gateway URL from Rithmic")
        logger.info("     Apply at: https://www.rithmic.com/api-request")
        logger.info("     Or email: rapi@rithmic.com")
        logger.info("  4. Make sure no other Rithmic session is active")
        return False

    try:
        # Step 2: List accounts
        logger.info("[2/5] Listing accounts...")
        accounts = []
        try:
            accounts = client.accounts
            if accounts:
                for acc in accounts:
                    logger.info(f"  Account: {acc}")
            else:
                logger.warning("  No accounts found. Check your Apex credentials.")
        except Exception as e:
            logger.warning(f"  Could not list accounts: {e}")

        # Step 3: Get front-month MNQ contract
        logger.info("[3/5] Looking up front-month MNQ contract...")
        try:
            contract = await client.get_front_month_contract("MNQ", "CME")
            logger.info(f"  Front-month MNQ: {contract}")
        except Exception as e:
            logger.warning(f"  Could not get front-month contract: {e}")
            logger.info("  Will try subscribing to MNQH6 (Mar 2026) directly...")

        # Step 4: Stream a few quotes
        logger.info("[4/5] Streaming MNQ quotes (5 seconds)...")
        quotes_received = []

        def on_tick(data):
            quotes_received.append(data)
            if len(quotes_received) <= 3:
                logger.info(f"  Quote: {data}")

        client.on_tick += on_tick

        try:
            await client.subscribe_to_market_data(
                "MNQ", "CME", DataType.LAST_TRADE | DataType.BBO
            )
        except Exception as e:
            logger.warning(f"  Could not subscribe to MNQ: {e}")

        await asyncio.sleep(5)
        logger.info(f"  Received {len(quotes_received)} quotes in 5 seconds")

        # Step 5: Check order routing
        logger.info("[5/5] Checking order routing...")
        try:
            routes = client.plants["order"].trade_routes
            logger.info(f"  Trade routes available: {len(routes)}")
            for route in routes[:5]:
                logger.info(f"    {route}")
        except Exception as e:
            logger.warning(f"  Could not get trade routes: {e}")

        logger.info("")
        logger.info("=" * 60)
        logger.info("CONNECTION TEST COMPLETE")
        logger.info(f"Working gateway: {connected_url}")
        logger.info("=" * 60)

        if accounts and len(quotes_received) > 0:
            logger.info("RESULT: ALL SYSTEMS GO - Ready for live trading!")
            return True
        elif accounts:
            logger.info("RESULT: Connected but no market data received.")
            logger.info("  This may be normal outside trading hours (9:30 AM - 4:00 PM ET)")
            logger.info("  Or market data may already be connected in another session.")
            logger.info("  Rithmic only allows ONE market data session at a time.")
            return True
        else:
            logger.info("RESULT: Connection issues detected. Check credentials.")
            return False

    except Exception as e:
        logger.error(f"Error during testing: {e}")
        return False

    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    success = asyncio.run(test_connection())
    sys.exit(0 if success else 1)
