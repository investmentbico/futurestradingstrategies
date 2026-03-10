#!/usr/bin/env python3
"""
MT5 Cloud Connector — Connect to MetaTrader 5 via MetaApi (works on Mac/Linux/Windows).
========================================================================================
Uses MetaApi cloud REST API instead of the Windows-only MetaTrader5 Python package.

Setup (one-time):
  1. Sign up free at https://app.metaapi.cloud/sign-up
  2. Add your MT5 account: https://app.metaapi.cloud/accounts
     - Platform: MT5
     - Login: 1237378
     - Password: (your MT5 password)
     - Server: Upcomers
     - Name: anything (e.g. "Upcomers 500K")
  3. Copy your API token from: https://app.metaapi.cloud/token
  4. Copy the Account ID from the accounts page
  5. Add to .env:
     METAAPI_TOKEN=your_token_here
     METAAPI_ACCOUNT_ID=your_account_id_here

Usage:
  python mt5_cloud_connect.py                  # Full connection test
  python mt5_cloud_connect.py --balance        # Balance & PnL only
  python mt5_cloud_connect.py --prices         # Live prices only
  python mt5_cloud_connect.py --positions      # Open positions
  python mt5_cloud_connect.py --symbols        # List available symbols
  python mt5_cloud_connect.py --monitor        # Live monitor (refreshes every 5s)

Requirements:
  pip install metaapi-cloud-sdk python-dotenv
"""

import os
import sys
import asyncio
import logging
import argparse
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / '.env', override=True)
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger("mt5_cloud")

# Suppress noisy MetaApi SDK logs
logging.getLogger("MetaApi").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

METAAPI_TOKEN = os.getenv('METAAPI_TOKEN', '')
METAAPI_ACCOUNT_ID = os.getenv('METAAPI_ACCOUNT_ID', '')

# Key instruments to monitor
WATCH_SYMBOLS = ['XAUUSD', 'BTCUSD', 'US100', 'NAS100', 'USTEC', 'US500', 'US30', 'EURUSD']


async def get_api():
    """Initialize MetaApi connection."""
    try:
        from metaapi_cloud_sdk import MetaApi
    except ImportError:
        logger.error("metaapi-cloud-sdk not installed. Run:")
        logger.error("  pip install metaapi-cloud-sdk")
        sys.exit(1)

    if not METAAPI_TOKEN:
        logger.error("METAAPI_TOKEN not set in .env")
        logger.error("Get your free token at: https://app.metaapi.cloud/token")
        sys.exit(1)

    if not METAAPI_ACCOUNT_ID:
        logger.error("METAAPI_ACCOUNT_ID not set in .env")
        logger.error("Add your MT5 account at: https://app.metaapi.cloud/accounts")
        sys.exit(1)

    api = MetaApi(token=METAAPI_TOKEN)
    return api


async def connect(api):
    """Connect to MT5 account and return RPC connection."""
    try:
        account = await api.metatrader_account_api.get_account(METAAPI_ACCOUNT_ID)
    except Exception as e:
        logger.error(f"Failed to get account: {e}")
        sys.exit(1)

    # Deploy if not already deployed
    if account.state != 'DEPLOYED':
        logger.info("Deploying account (first time may take 1-2 minutes)...")
        await account.deploy()

    logger.info("Waiting for account connection...")
    await account.wait_connected()

    connection = account.get_rpc_connection()
    await connection.connect()
    await connection.wait_synchronized()

    logger.info("Connected to MT5 via MetaApi!")
    return connection


async def show_balance(connection):
    """Display account balance, equity, PnL."""
    info = await connection.get_account_information()

    print("\n" + "=" * 60)
    print("  MT5 ACCOUNT STATUS")
    print("=" * 60)
    print(f"  Server:       {info.get('server', 'N/A')}")
    print(f"  Login:        {info.get('login', 'N/A')}")
    print(f"  Name:         {info.get('name', 'N/A')}")
    print(f"  Currency:     {info.get('currency', 'USD')}")
    print(f"  Leverage:     1:{info.get('leverage', 'N/A')}")
    print(f"  Platform:     {info.get('platform', 'N/A')}")
    print("-" * 60)
    print(f"  Balance:      ${info.get('balance', 0):>12,.2f}")
    print(f"  Equity:       ${info.get('equity', 0):>12,.2f}")
    print(f"  Margin:       ${info.get('margin', 0):>12,.2f}")
    print(f"  Free Margin:  ${info.get('freeMargin', 0):>12,.2f}")
    print(f"  Profit (PnL): ${info.get('profit', 0):>12,.2f}")
    print(f"  Margin Level: {info.get('marginLevel', 0):>11,.1f}%")
    print("=" * 60)

    return info


async def show_prices(connection):
    """Display live prices for watched symbols."""
    print("\n" + "=" * 60)
    print("  LIVE PRICES")
    print("=" * 60)
    print(f"  {'Symbol':<12} {'Bid':>12} {'Ask':>12} {'Spread':>10}")
    print("-" * 60)

    for sym in WATCH_SYMBOLS:
        try:
            price = await connection.get_symbol_price(symbol=sym)
            bid = price.get('bid', 0)
            ask = price.get('ask', 0)
            spread = ask - bid
            print(f"  {sym:<12} {bid:>12.2f} {ask:>12.2f} {spread:>10.2f}")
        except Exception:
            print(f"  {sym:<12} {'N/A':>12} {'N/A':>12} {'N/A':>10}")

    print("=" * 60)
    print(f"  Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


async def show_positions(connection):
    """Display open positions."""
    positions = await connection.get_positions()

    print("\n" + "=" * 60)
    print("  OPEN POSITIONS")
    print("=" * 60)

    if not positions:
        print("  No open positions")
    else:
        print(f"  {'Symbol':<12} {'Type':<6} {'Volume':>8} {'Price':>10} {'PnL':>12}")
        print("-" * 60)
        total_pnl = 0
        for p in positions:
            pnl = p.get('profit', 0) + p.get('swap', 0) + p.get('commission', 0)
            total_pnl += pnl
            print(f"  {p.get('symbol', '?'):<12} "
                  f"{p.get('type', '?'):<6} "
                  f"{p.get('volume', 0):>8.2f} "
                  f"{p.get('openPrice', 0):>10.2f} "
                  f"${pnl:>11,.2f}")
        print("-" * 60)
        print(f"  {'Total PnL:':>40} ${total_pnl:>11,.2f}")

    print("=" * 60)


async def show_symbols(connection):
    """List available trading symbols."""
    symbols = await connection.get_symbols()

    print("\n" + "=" * 60)
    print(f"  AVAILABLE SYMBOLS ({len(symbols)} total)")
    print("=" * 60)

    # Group by category keywords
    categories = {
        'Indices': ['US100', 'NAS', 'USTEC', 'US500', 'US30', 'DAX', 'FTSE', 'NIK', 'JP225'],
        'Metals': ['XAU', 'XAG', 'GOLD', 'SILVER'],
        'Crypto': ['BTC', 'ETH', 'LTC', 'XRP', 'DOGE'],
        'Forex Major': ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'AUDUSD', 'USDCAD', 'NZDUSD'],
    }

    shown = set()
    for cat_name, keywords in categories.items():
        matches = [s for s in symbols if any(kw in s.upper() for kw in keywords)]
        if matches:
            print(f"\n  {cat_name}:")
            for s in sorted(matches):
                print(f"    {s}")
                shown.add(s)

    # Show remaining
    remaining = sorted(set(symbols) - shown)
    if remaining:
        print(f"\n  Other ({len(remaining)}):")
        for s in remaining[:50]:
            print(f"    {s}")
        if len(remaining) > 50:
            print(f"    ... and {len(remaining) - 50} more")

    print("=" * 60)


async def live_monitor(connection, interval=5):
    """Continuous monitoring — balance + prices + positions, refreshes every N seconds."""
    print("Live monitor started. Press Ctrl+C to stop.\n")
    try:
        while True:
            # Clear screen
            print("\033[2J\033[H", end="")
            print(f"MT5 LIVE MONITOR | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | Refresh: {interval}s")

            await show_balance(connection)
            await show_positions(connection)
            await show_prices(connection)

            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


async def full_test(connection):
    """Run full connection test — balance, positions, prices."""
    await show_balance(connection)
    await show_positions(connection)
    await show_prices(connection)


async def main():
    parser = argparse.ArgumentParser(description='MT5 Cloud Connector (Mac/Linux/Windows)')
    parser.add_argument('--balance', action='store_true', help='Show balance & PnL')
    parser.add_argument('--prices', action='store_true', help='Show live prices')
    parser.add_argument('--positions', action='store_true', help='Show open positions')
    parser.add_argument('--symbols', action='store_true', help='List available symbols')
    parser.add_argument('--monitor', action='store_true', help='Live monitor (refreshes every 5s)')
    parser.add_argument('--interval', type=int, default=5, help='Monitor refresh interval in seconds')
    args = parser.parse_args()

    api = await get_api()
    connection = await connect(api)

    try:
        if args.balance:
            await show_balance(connection)
        elif args.prices:
            await show_prices(connection)
        elif args.positions:
            await show_positions(connection)
        elif args.symbols:
            await show_symbols(connection)
        elif args.monitor:
            await live_monitor(connection, args.interval)
        else:
            # Default: full test
            await full_test(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    asyncio.run(main())
