#!/usr/bin/env python3
"""
MT5 Connector — Connect to MetaTrader 5, list tickers, download data, trade live.
=================================================================================
Designed for Upcomers prop firm challenge on MT5.
Run this on your LOCAL machine (iTerm/Mac/Windows) where MT5 terminal is installed.

Account: 500K | Max DD: 1.5%/day ($7,500) | Target: $10,100/day | 5 days to pass

Usage:
  # 1. Test connection and list tickers
  python mt5_connector.py --test

  # 2. Download historical data for backtesting
  python mt5_connector.py --download XAUUSD --timeframe M1 --days 60
  python mt5_connector.py --download BTCUSD --timeframe M1 --days 60

  # 3. List all available symbols
  python mt5_connector.py --list-symbols

  # 4. Run live bot
  python mt5_connector.py --live XAUUSD

Requirements:
  pip install MetaTrader5 pandas numpy
  MT5 terminal must be installed and running.
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("mt5")

# ── MT5 Credentials ─────────────────────────────────────────────────
MT5_LOGIN = 1237326
MT5_PASSWORD = "kzC{9]2o;LU2"
MT5_SERVER = "Upcomers"

# ── Prop Firm Rules ──────────────────────────────────────────────────
ACCOUNT_BALANCE = 500_000
MAX_DAILY_DD_PCT = 1.5   # 1.5% of account
MAX_DAILY_LOSS = ACCOUNT_BALANCE * MAX_DAILY_DD_PCT / 100  # $7,500
DAILY_PROFIT_TARGET = 10_100
DAYS_TO_PASS = 5


def connect_mt5():
    """Initialize and login to MT5 terminal."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        logger.error("MetaTrader5 package not installed. Run: pip install MetaTrader5")
        logger.error("NOTE: MT5 Python package only works on Windows or Mac with MT5 terminal installed.")
        sys.exit(1)

    if not mt5.initialize():
        logger.error(f"MT5 initialize() failed: {mt5.last_error()}")
        sys.exit(1)

    authorized = mt5.login(
        login=MT5_LOGIN,
        password=MT5_PASSWORD,
        server=MT5_SERVER,
    )
    if not authorized:
        logger.error(f"MT5 login failed: {mt5.last_error()}")
        mt5.shutdown()
        sys.exit(1)

    account_info = mt5.account_info()
    logger.info("=" * 60)
    logger.info("MT5 CONNECTION SUCCESSFUL")
    logger.info(f"  Server:   {MT5_SERVER}")
    logger.info(f"  Login:    {MT5_LOGIN}")
    logger.info(f"  Name:     {account_info.name}")
    logger.info(f"  Balance:  ${account_info.balance:,.2f}")
    logger.info(f"  Equity:   ${account_info.equity:,.2f}")
    logger.info(f"  Leverage: 1:{account_info.leverage}")
    logger.info(f"  Currency: {account_info.currency}")
    logger.info("=" * 60)
    return mt5, account_info


def list_symbols(mt5):
    """List all available trading symbols."""
    symbols = mt5.symbols_get()
    if not symbols:
        logger.error("No symbols found")
        return []

    logger.info(f"\nTotal symbols: {len(symbols)}")

    # Categorize symbols
    categories = {}
    for s in symbols:
        cat = s.path.split('\\')[0] if '\\' in s.path else 'Other'
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(s)

    # Print by category
    for cat, syms in sorted(categories.items()):
        logger.info(f"\n{'=' * 40}")
        logger.info(f"Category: {cat} ({len(syms)} symbols)")
        logger.info(f"{'=' * 40}")
        for s in sorted(syms, key=lambda x: x.name):
            spread_pts = s.spread
            logger.info(f"  {s.name:20s} | Spread: {spread_pts:5d} | Digits: {s.digits} | {s.description}")

    # Highlight NASDAQ / SP500 / Index equivalents
    logger.info("\n" + "=" * 60)
    logger.info("INDEX INSTRUMENTS (NASDAQ/SP500 equivalents):")
    logger.info("=" * 60)
    index_keywords = ['NAS', 'US100', 'USTEC', 'NDX', 'US500', 'SP500', 'SPX',
                      'US30', 'DJ30', 'DOW', 'DAX', 'FTSE', 'NIK', 'INDEX']
    for s in symbols:
        name_upper = s.name.upper()
        desc_upper = s.description.upper() if s.description else ''
        if any(kw in name_upper or kw in desc_upper for kw in index_keywords):
            logger.info(f"  {s.name:20s} | {s.description}")

    # Highlight crypto
    logger.info("\nCRYPTO INSTRUMENTS:")
    crypto_kw = ['BTC', 'ETH', 'CRYPTO', 'BITCOIN']
    for s in symbols:
        name_upper = s.name.upper()
        if any(kw in name_upper for kw in crypto_kw):
            logger.info(f"  {s.name:20s} | {s.description}")

    # Highlight gold/metals
    logger.info("\nMETALS:")
    metal_kw = ['XAU', 'GOLD', 'XAG', 'SILVER']
    for s in symbols:
        name_upper = s.name.upper()
        if any(kw in name_upper for kw in metal_kw):
            logger.info(f"  {s.name:20s} | {s.description}")

    return symbols


def download_data(mt5_mod, symbol, timeframe_str, days):
    """Download historical data from MT5."""
    tf_map = {
        'M1': mt5_mod.TIMEFRAME_M1,
        'M2': mt5_mod.TIMEFRAME_M2,
        'M3': mt5_mod.TIMEFRAME_M3,
        'M5': mt5_mod.TIMEFRAME_M5,
        'M15': mt5_mod.TIMEFRAME_M15,
        'M30': mt5_mod.TIMEFRAME_M30,
        'H1': mt5_mod.TIMEFRAME_H1,
        'H4': mt5_mod.TIMEFRAME_H4,
        'D1': mt5_mod.TIMEFRAME_D1,
    }

    if timeframe_str.upper() not in tf_map:
        logger.error(f"Invalid timeframe: {timeframe_str}. Use: {list(tf_map.keys())}")
        return None

    tf = tf_map[timeframe_str.upper()]
    utc_to = datetime.utcnow()
    utc_from = utc_to - timedelta(days=days)

    logger.info(f"Downloading {symbol} {timeframe_str} from {utc_from} to {utc_to}...")

    # Enable symbol if needed
    symbol_info = mt5_mod.symbol_info(symbol)
    if symbol_info is None:
        logger.error(f"Symbol {symbol} not found")
        return None
    if not symbol_info.visible:
        mt5_mod.symbol_select(symbol, True)

    rates = mt5_mod.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        logger.error(f"No data for {symbol}")
        return None

    df = pd.DataFrame(rates)
    df['timestamp'] = pd.to_datetime(df['time'], unit='s')

    logger.info(f"Downloaded {len(df)} bars")
    logger.info(f"  From: {df['timestamp'].min()}")
    logger.info(f"  To:   {df['timestamp'].max()}")
    logger.info(f"  Avg spread: {df['spread'].mean():.1f} pts")

    # Save to CSV (compatible with our backtest format)
    os.makedirs('data', exist_ok=True)
    clean_name = symbol.lower().replace('/', '')
    tf_name = timeframe_str.lower().replace('m', 'min')
    filename = f"data/{clean_name}_{tf_name}.csv"

    out = pd.DataFrame({
        'time': df['time'].values,
        'open': df['open'].values,
        'high': df['high'].values,
        'low': df['low'].values,
        'close': df['close'].values,
    })
    out.to_csv(filename, index=False)
    logger.info(f"Saved: {filename}")

    # Also save with spread info for realistic backtesting
    filename_full = f"data/{clean_name}_{tf_name}_full.csv"
    save_df = df[['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume']]
    save_df.to_csv(filename_full, index=False)
    logger.info(f"Saved (with spread): {filename_full}")

    return df


def get_live_price(mt5_mod, symbol):
    """Get real-time bid/ask/last price."""
    tick = mt5_mod.symbol_info_tick(symbol)
    if tick is None:
        return None
    return {
        'bid': tick.bid,
        'ask': tick.ask,
        'last': tick.last,
        'time': datetime.fromtimestamp(tick.time),
        'spread': tick.ask - tick.bid,
    }


def get_account_info(mt5_mod):
    """Get current account balance and equity."""
    info = mt5_mod.account_info()
    return {
        'balance': info.balance,
        'equity': info.equity,
        'margin': info.margin,
        'free_margin': info.margin_free,
        'profit': info.profit,
        'leverage': info.leverage,
    }


# ── Instrument Config ────────────────────────────────────────────────
INSTRUMENT_CONFIG = {
    'XAUUSD': {
        'point_value': 1.0,    # $1 per 0.01 move per 0.01 lot
        'lot_size': 100,       # 1 lot = 100 oz
        'tick_size': 0.01,
        'description': 'Gold',
        'typical_spread': 0.30,
    },
    'BTCUSD': {
        'point_value': 1.0,
        'lot_size': 1,
        'tick_size': 0.01,
        'description': 'Bitcoin',
        'typical_spread': 50.0,
    },
    'US100': {
        'point_value': 1.0,
        'lot_size': 1,
        'tick_size': 0.01,
        'description': 'NASDAQ 100 (MNQ equivalent)',
        'typical_spread': 1.0,
    },
    'NAS100': {
        'point_value': 1.0,
        'lot_size': 1,
        'tick_size': 0.01,
        'description': 'NASDAQ 100 (MNQ equivalent)',
        'typical_spread': 1.0,
    },
    'USTEC': {
        'point_value': 1.0,
        'lot_size': 1,
        'tick_size': 0.01,
        'description': 'US Tech 100 (NASDAQ)',
        'typical_spread': 1.0,
    },
    'US500': {
        'point_value': 1.0,
        'lot_size': 1,
        'tick_size': 0.01,
        'description': 'S&P 500 (ES equivalent)',
        'typical_spread': 0.5,
    },
}


def test_connection():
    """Full connection test — connect, show account, list symbols, get prices."""
    mt5_mod, account = connect_mt5()

    logger.info("\n--- ACCOUNT STATUS ---")
    logger.info(f"Balance:     ${account.balance:,.2f}")
    logger.info(f"Equity:      ${account.equity:,.2f}")
    logger.info(f"Max DD/day:  ${MAX_DAILY_LOSS:,.2f} (1.5%)")
    logger.info(f"Target/day:  ${DAILY_PROFIT_TARGET:,.2f}")
    logger.info(f"Total goal:  ${DAILY_PROFIT_TARGET * DAYS_TO_PASS:,.2f} in {DAYS_TO_PASS} days")

    # List all symbols
    symbols = list_symbols(mt5_mod)

    # Try to get live prices for key instruments
    logger.info("\n--- LIVE PRICES ---")
    for sym in ['XAUUSD', 'BTCUSD', 'US100', 'NAS100', 'USTEC', 'US500', 'US30']:
        price = get_live_price(mt5_mod, sym)
        if price:
            logger.info(f"  {sym:12s} | Bid: {price['bid']:.2f} | Ask: {price['ask']:.2f} | Spread: {price['spread']:.2f}")
        else:
            logger.info(f"  {sym:12s} | NOT AVAILABLE")

    mt5_mod.shutdown()
    logger.info("\nConnection test complete!")


def main():
    parser = argparse.ArgumentParser(description='MT5 Connector for Prop Firm Challenge')
    parser.add_argument('--test', action='store_true', help='Test connection and list symbols')
    parser.add_argument('--list-symbols', action='store_true', help='List all available symbols')
    parser.add_argument('--download', type=str, help='Download data for symbol (e.g., XAUUSD)')
    parser.add_argument('--timeframe', type=str, default='M1', help='Timeframe: M1, M2, M3, M5, M15, H1')
    parser.add_argument('--days', type=int, default=60, help='Days of history to download')
    parser.add_argument('--live', type=str, help='Run live bot on symbol')
    parser.add_argument('--download-all', action='store_true', help='Download all instruments for backtesting')

    args = parser.parse_args()

    if args.test:
        test_connection()
    elif args.list_symbols:
        mt5_mod, _ = connect_mt5()
        list_symbols(mt5_mod)
        mt5_mod.shutdown()
    elif args.download:
        mt5_mod, _ = connect_mt5()
        download_data(mt5_mod, args.download, args.timeframe, args.days)
        mt5_mod.shutdown()
    elif args.download_all:
        mt5_mod, _ = connect_mt5()
        for sym in ['XAUUSD', 'BTCUSD', 'US100', 'NAS100', 'USTEC', 'US500']:
            for tf in ['M1', 'M2', 'M3', 'M5']:
                try:
                    download_data(mt5_mod, sym, tf, 60)
                except Exception as e:
                    logger.error(f"Failed {sym} {tf}: {e}")
        mt5_mod.shutdown()
    elif args.live:
        logger.info("Live trading — use mt5_live_bot.py instead")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
