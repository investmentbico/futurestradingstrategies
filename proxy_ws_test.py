#!/usr/bin/env python3
"""Test async_rithmic connection via HTTP CONNECT proxy."""
import asyncio
import os
import logging

# Apply proxy patch BEFORE importing async_rithmic
from rithmic_proxy import patch_websockets_for_proxy
patch_websockets_for_proxy()

from dotenv import load_dotenv
load_dotenv()

from async_rithmic import RithmicClient, SysInfraType

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    user = os.getenv('RITHMIC_USER')
    password = os.getenv('RITHMIC_PASSWORD')
    system_name = os.getenv('RITHMIC_SYSTEM_NAME', 'Apex')
    gateway_url = os.getenv('RITHMIC_GATEWAY_URL', 'rituz00100.rithmic.com:443')

    logger.info(f"Connecting as {user} to {gateway_url} (system: {system_name})")

    client = RithmicClient(
        user=user,
        password=password,
        system_name=system_name,
        app_name="MNQ_Trading_Bot",
        app_version="1.0",
        url=gateway_url,
    )

    try:
        await client.connect(plants=[SysInfraType.TICKER_PLANT])
        logger.info("CONNECTED SUCCESSFULLY!")

        # List available info
        logger.info(f"Accounts: {client.accounts}")
        await client.disconnect()
    except Exception as e:
        logger.error(f"Connection failed: {e}")
        try:
            await client.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
