#!/usr/bin/env python3
"""
Futures Data Downloader - Free Historical Data Sources
======================================================

Downloads historical futures data from free sources for backtesting.
Supports multiple symbols and timeframes for the last 6 months.

Supported Symbols:
- MNQ: Micro E-mini Nasdaq-100 Futures
- NQ: E-mini Nasdaq-100 Futures
- ES: E-mini S&P 500 Futures
- MES: Micro E-mini S&P 500 Futures

Data Sources:
1. Yahoo Finance (daily data)
2. Alpha Vantage (intraday, requires API key)
3. Polygon.io (free tier available)

Usage:
    python data_downloader.py --symbols MNQ NQ ES MES --months 6
    python data_downloader.py --symbols MNQ --timeframes 1min 5min 15min
"""

import os
import sys
import pandas as pd
import numpy as np
import requests
import time
from datetime import datetime, timedelta
import argparse
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION
# =============================================================================

# Futures symbol mappings for different data sources
YAHOO_SYMBOLS = {
    'MNQ': 'MNQ=F',  # Micro E-mini Nasdaq
    'NQ': 'NQ=F',    # E-mini Nasdaq
    'ES': 'ES=F',    # E-mini S&P 500
    'MES': 'MES=F',  # Micro E-mini S&P 500
}

ALPHA_VANTAGE_SYMBOLS = {
    'MNQ': 'MNQ',
    'NQ': 'NQ',
    'ES': 'ES',
    'MES': 'MES'
}

# =============================================================================
# YAHOO FINANCE DOWNLOADER (Free, Daily Data)
# =============================================================================
def download_yahoo_data(symbol: str, months: int = 6) -> pd.DataFrame:
    """
    Download historical data from Yahoo Finance
    Note: Yahoo Finance provides daily bars, not intraday
    """
    try:
        import yfinance as yf
    except ImportError:
        print("❌ yfinance not installed. Install with: pip install yfinance")
        return None

    print(f"📥 Downloading {symbol} from Yahoo Finance (last {months} months)...")

    yahoo_symbol = YAHOO_SYMBOLS.get(symbol, symbol)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=months*30)

    try:
        ticker = yf.Ticker(yahoo_symbol)
        df = ticker.history(start=start_date, end=end_date, interval='1d')

        if df.empty:
            print(f"⚠️  No data found for {symbol}")
            return None

        # Convert to our format
        df = df.reset_index()
        df = df.rename(columns={
            'Date': 'timestamp',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })

        # Add time column (Unix timestamp)
        df['time'] = df['timestamp'].astype(np.int64) // 10**9

        # Select columns
        df = df[['time', 'open', 'high', 'low', 'close']]

        print(f"✅ Downloaded {len(df)} daily bars for {symbol}")
        return df

    except Exception as e:
        print(f"❌ Error downloading {symbol} from Yahoo: {e}")
        return None

# =============================================================================
# ALPHA VANTAGE DOWNLOADER (Free API, Intraday Data)
# =============================================================================
def download_alpha_vantage_data(symbol: str, timeframe: str = '5min', months: int = 6) -> pd.DataFrame:
    """
    Download intraday data from Alpha Vantage
    Requires free API key: https://www.alphavantage.co/support/#api-key
    """
    api_key = os.getenv('ALPHA_VANTAGE_API_KEY')
    if not api_key:
        print("⚠️  Alpha Vantage API key not found. Set ALPHA_VANTAGE_API_KEY environment variable")
        print("   Get free key at: https://www.alphavantage.co/support/#api-key")
        return None

    print(f"📥 Downloading {symbol} {timeframe} from Alpha Vantage...")

    # Map timeframe to Alpha Vantage interval
    interval_map = {
        '1min': '1min',
        '5min': '5min',
        '15min': '15min',
        '30min': '30min',
        '1h': '60min'
    }

    interval = interval_map.get(timeframe, '5min')
    av_symbol = ALPHA_VANTAGE_SYMBOLS.get(symbol, symbol)

    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_INTRADAY",
        "symbol": av_symbol,
        "interval": interval,
        "apikey": api_key,
        "outputsize": "full"
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if "Error Message" in data:
            print(f"❌ Alpha Vantage error: {data['Error Message']}")
            return None

        # Extract time series data
        time_series_key = f"Time Series ({interval})"
        if time_series_key not in data:
            print(f"⚠️  No {time_series_key} data in response")
            return None

        time_series = data[time_series_key]

        # Convert to DataFrame
        records = []
        for timestamp_str, values in time_series.items():
            record = {
                'timestamp': pd.to_datetime(timestamp_str),
                'open': float(values['1. open']),
                'high': float(values['2. high']),
                'low': float(values['3. low']),
                'close': float(values['4. close']),
                'volume': int(values['5. volume'])
            }
            records.append(record)

        df = pd.DataFrame(records)

        # Filter for last N months
        end_date = datetime.now()
        start_date = end_date - timedelta(days=months*30)
        df = df[df['timestamp'] >= start_date]

        # Sort by timestamp
        df = df.sort_values('timestamp').reset_index(drop=True)

        # Add time column (Unix timestamp)
        df['time'] = df['timestamp'].astype(np.int64) // 10**9

        # Select columns
        df = df[['time', 'open', 'high', 'low', 'close']]

        print(f"✅ Downloaded {len(df)} {timeframe} bars for {symbol}")
        return df

    except Exception as e:
        print(f"❌ Error downloading {symbol} from Alpha Vantage: {e}")
        return None

# =============================================================================
# POLYGON.IO DOWNLOADER (Free Tier)
# =============================================================================
def download_polygon_data(symbol: str, timeframe: str = '5min', months: int = 6) -> pd.DataFrame:
    """
    Download data from Polygon.io free tier
    Limited to 5 API calls per minute, 2 years of historical data
    """
    api_key = os.getenv('POLYGON_API_KEY')
    if not api_key:
        print("⚠️  Polygon.io API key not found. Set POLYGON_API_KEY environment variable")
        print("   Get free key at: https://polygon.io/")
        return None

    print(f"📥 Downloading {symbol} {timeframe} from Polygon.io...")

    # Map timeframe to Polygon multiplier/unit
    timeframe_map = {
        '1min': ('1', 'minute'),
        '5min': ('5', 'minute'),
        '15min': ('15', 'minute'),
        '1h': ('1', 'hour'),
        '1d': ('1', 'day')
    }

    multiplier, unit = timeframe_map.get(timeframe, ('5', 'minute'))

    # Polygon symbols (futures)
    polygon_symbol = f"I:{symbol}"  # CME futures format

    end_date = datetime.now()
    start_date = end_date - timedelta(days=months*30)

    url = f"https://api.polygon.io/v2/aggs/ticker/{polygon_symbol}/range/{multiplier}/{unit}/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"

    params = {
        'apiKey': api_key,
        'limit': 50000  # Max per request
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()

        if data.get('status') != 'OK':
            print(f"❌ Polygon.io error: {data}")
            return None

        # Convert to DataFrame
        records = []
        for bar in data.get('results', []):
            record = {
                'timestamp': pd.to_datetime(bar['t'], unit='ms'),
                'open': bar['o'],
                'high': bar['h'],
                'low': bar['l'],
                'close': bar['c'],
                'volume': bar['v']
            }
            records.append(record)

        df = pd.DataFrame(records)

        if df.empty:
            print(f"⚠️  No data found for {symbol}")
            return None

        # Add time column (Unix timestamp)
        df['time'] = df['timestamp'].astype(np.int64) // 10**9

        # Select columns
        df = df[['time', 'open', 'high', 'low', 'close']]

        print(f"✅ Downloaded {len(df)} {timeframe} bars for {symbol}")
        return df

    except Exception as e:
        print(f"❌ Error downloading {symbol} from Polygon.io: {e}")
        return None

# =============================================================================
# MAIN DOWNLOADER
# =============================================================================
def download_data(symbols: list, timeframes: list = None, months: int = 6,
                 source: str = 'yahoo', output_dir: str = 'data') -> None:
    """
    Download historical data for specified symbols and timeframes
    """
    os.makedirs(output_dir, exist_ok=True)

    if timeframes is None:
        timeframes = ['1d']  # Default to daily for Yahoo

    total_downloads = len(symbols) * len(timeframes)
    completed = 0

    print(f"🚀 Starting download of {total_downloads} datasets from {source.upper()}")
    print(f"   Symbols: {', '.join(symbols)}")
    print(f"   Timeframes: {', '.join(timeframes)}")
    print(f"   Period: Last {months} months")
    print()

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                if source.lower() == 'yahoo':
                    df = download_yahoo_data(symbol, months)
                    filename = f"{symbol.lower()}_{timeframe}.csv"

                elif source.lower() == 'alpha_vantage':
                    df = download_alpha_vantage_data(symbol, timeframe, months)
                    filename = f"{symbol.lower()}_{timeframe}.csv"

                elif source.lower() == 'polygon':
                    df = download_polygon_data(symbol, timeframe, months)
                    filename = f"{symbol.lower()}_{timeframe}.csv"

                else:
                    print(f"❌ Unknown source: {source}")
                    continue

                if df is not None and not df.empty:
                    filepath = os.path.join(output_dir, filename)
                    df.to_csv(filepath, index=False)
                    print(f"💾 Saved {len(df)} bars to {filepath}")
                else:
                    print(f"⚠️  No data to save for {symbol} {timeframe}")

            except Exception as e:
                print(f"❌ Failed to download {symbol} {timeframe}: {e}")

            completed += 1
            if completed < total_downloads:
                time.sleep(1)  # Rate limiting

    print(f"\n✅ Download complete! Check {output_dir}/ folder for data files")

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================
def check_data_availability(output_dir: str = 'data') -> None:
    """Check what data files are available"""
    if not os.path.exists(output_dir):
        print(f"❌ Data directory {output_dir} does not exist")
        return

    files = os.listdir(output_dir)
    csv_files = [f for f in files if f.endswith('.csv')]

    if not csv_files:
        print(f"❌ No CSV files found in {output_dir}")
        return

    print(f"📊 Available data files in {output_dir}:")
    print("-" * 50)

    for file in sorted(csv_files):
        filepath = os.path.join(output_dir, file)
        try:
            df = pd.read_csv(filepath)
            bars = len(df)
            if 'time' in df.columns and len(df) > 0:
                start_date = pd.to_datetime(df['time'].iloc[0], unit='s')
                end_date = pd.to_datetime(df['time'].iloc[-1], unit='s')
                print("20")
            else:
                print("20")
        except Exception as e:
            print("20")

def main():
    parser = argparse.ArgumentParser(description='Download historical futures data')
    parser.add_argument('--symbols', nargs='+', default=['MNQ'],
                       help='Symbols to download (default: MNQ)')
    parser.add_argument('--timeframes', nargs='+', default=['1d'],
                       help='Timeframes to download (default: 1d for Yahoo)')
    parser.add_argument('--months', type=int, default=6,
                       help='Months of historical data (default: 6)')
    parser.add_argument('--source', choices=['yahoo', 'alpha_vantage', 'polygon'],
                       default='yahoo', help='Data source (default: yahoo)')
    parser.add_argument('--output-dir', default='data',
                       help='Output directory (default: data)')
    parser.add_argument('--check', action='store_true',
                       help='Check available data files')

    args = parser.parse_args()

    if args.check:
        check_data_availability(args.output_dir)
        return

    # Validate requirements
    if args.source == 'alpha_vantage' and not os.getenv('ALPHA_VANTAGE_API_KEY'):
        print("❌ Alpha Vantage requires API key. Set ALPHA_VANTAGE_API_KEY environment variable")
        print("   Get free key at: https://www.alphavantage.co/support/#api-key")
        return

    if args.source == 'polygon' and not os.getenv('POLYGON_API_KEY'):
        print("❌ Polygon.io requires API key. Set POLYGON_API_KEY environment variable")
        print("   Get free key at: https://polygon.io/")
        return

    download_data(args.symbols, args.timeframes, args.months, args.source, args.output_dir)

if __name__ == "__main__":
    main()