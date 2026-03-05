#!/usr/bin/env python3
"""
MGC (Micro Gold Futures) Data Downloader
==========================================
Downloads gold futures data and resamples to multiple timeframes.

Sources:
  - Yahoo Finance (GC=F for Gold futures) — free, no API key needed
  - Generates: 1min, 2min, 3min, 5min, 10min, 15min CSVs

Output format matches existing data/ convention:
  time,open,high,low,close  (Unix timestamp in seconds)

Usage:
  pip install yfinance
  python mgc_data_downloader.py
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print("ERROR: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)


# =============================================================================
# CONFIGURATION
# =============================================================================

OUTPUT_DIR = "data"
SYMBOL = "GC=F"  # Gold futures on Yahoo Finance (proxy for MGC)
MONTHS_BACK = 3

# Yahoo Finance max intraday history:
#   - 1m data: last 30 days only
#   - 2m data: last 60 days
#   - 5m data: last 60 days
#   - 15m data: last 60 days
# For 3 months of 1m data, we download in chunks

TIMEFRAMES = {
    '1min': '1m',
    '2min': '2m',
    '5min': '5m',
    '15min': '15m',
}

# Additional timeframes we create by resampling from 1min
RESAMPLE_TIMEFRAMES = {
    '3min': '3min',
    '10min': '10min',
}


def download_intraday_data(symbol, interval, days_back):
    """Download intraday data from Yahoo Finance in chunks."""
    all_data = []

    if interval == '1m':
        # Yahoo limits 1m data to 7 days per request, 30 days total
        chunk_days = 7
        max_days = min(days_back, 30)
    elif interval in ('2m', '5m', '15m'):
        # Up to 60 days
        chunk_days = 30
        max_days = min(days_back, 60)
    else:
        chunk_days = 30
        max_days = days_back

    end_date = datetime.now()
    start_date = end_date - timedelta(days=max_days)
    current_end = end_date

    print(f"  Downloading {symbol} {interval} data ({max_days} days available)...")

    while current_end > start_date:
        current_start = max(current_end - timedelta(days=chunk_days), start_date)

        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(
                start=current_start.strftime('%Y-%m-%d'),
                end=current_end.strftime('%Y-%m-%d'),
                interval=interval,
                auto_adjust=True
            )

            if not df.empty:
                all_data.append(df)
                print(f"    Chunk: {current_start.date()} to {current_end.date()} — {len(df)} bars")

        except Exception as e:
            print(f"    WARNING: Failed chunk {current_start.date()}-{current_end.date()}: {e}")

        current_end = current_start
        time.sleep(0.5)  # Rate limiting

    if not all_data:
        return pd.DataFrame()

    combined = pd.concat(all_data)
    combined = combined[~combined.index.duplicated(keep='first')]
    combined = combined.sort_index()
    return combined


def convert_to_csv_format(df):
    """Convert Yahoo Finance DataFrame to our CSV format: time,open,high,low,close"""
    if df.empty:
        return pd.DataFrame()

    result = pd.DataFrame()
    # Convert DatetimeIndex to Unix seconds properly
    result['time'] = [int(ts.timestamp()) for ts in df.index]
    result['open'] = df['Open'].values
    result['high'] = df['High'].values
    result['low'] = df['Low'].values
    result['close'] = df['Close'].values

    # Remove any rows with NaN
    result = result.dropna()
    result = result[result['open'] > 0]  # Filter out zero-price bars

    return result


def resample_ohlc(df_1min, target_freq):
    """Resample 1-minute OHLC data to a larger timeframe."""
    if df_1min.empty:
        return pd.DataFrame()

    # Set timestamp as index for resampling
    df = df_1min.copy()
    df['datetime'] = pd.to_datetime(df['time'], unit='s')
    df = df.set_index('datetime')

    resampled = df.resample(target_freq).agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
    }).dropna()

    result = pd.DataFrame()
    result['time'] = [int(ts.timestamp()) for ts in resampled.index]
    result['open'] = resampled['open'].values
    result['high'] = resampled['high'].values
    result['low'] = resampled['low'].values
    result['close'] = resampled['close'].values

    return result


def main():
    print("=" * 60)
    print("MGC Data Downloader (Gold Futures via Yahoo Finance)")
    print("=" * 60)
    print(f"Symbol: {SYMBOL}")
    print(f"Target: Last {MONTHS_BACK} months")
    print(f"Output: {OUTPUT_DIR}/mgc_*.csv")
    print()

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    days_back = MONTHS_BACK * 30

    # Download each timeframe directly from Yahoo
    downloaded = {}
    for label, yf_interval in TIMEFRAMES.items():
        print(f"\n--- {label} ---")
        df = download_intraday_data(SYMBOL, yf_interval, days_back)

        if df.empty:
            print(f"  No data returned for {label}")
            continue

        csv_df = convert_to_csv_format(df)
        output_file = os.path.join(OUTPUT_DIR, f"mgc_{label}.csv")
        csv_df.to_csv(output_file, index=False)
        downloaded[label] = csv_df
        print(f"  Saved: {output_file} ({len(csv_df)} bars)")

    # Create resampled timeframes from 1min data
    if '1min' in downloaded and not downloaded['1min'].empty:
        for label, freq in RESAMPLE_TIMEFRAMES.items():
            print(f"\n--- {label} (resampled from 1min) ---")
            resampled = resample_ohlc(downloaded['1min'], freq)

            if resampled.empty:
                print(f"  No data after resampling for {label}")
                continue

            output_file = os.path.join(OUTPUT_DIR, f"mgc_{label}.csv")
            resampled.to_csv(output_file, index=False)
            print(f"  Saved: {output_file} ({len(resampled)} bars)")
    else:
        print("\n  WARNING: No 1min data available for resampling 3min/10min.")
        print("  Attempting to resample from 2min for 10min...")
        if '2min' in downloaded and not downloaded['2min'].empty:
            for label, freq in RESAMPLE_TIMEFRAMES.items():
                print(f"\n--- {label} (resampled from 2min) ---")
                resampled = resample_ohlc(downloaded['2min'], freq)
                if not resampled.empty:
                    output_file = os.path.join(OUTPUT_DIR, f"mgc_{label}.csv")
                    resampled.to_csv(output_file, index=False)
                    print(f"  Saved: {output_file} ({len(resampled)} bars)")

    # Summary
    print("\n" + "=" * 60)
    print("DOWNLOAD SUMMARY")
    print("=" * 60)

    for tf in ['1min', '2min', '3min', '5min', '10min', '15min']:
        filepath = os.path.join(OUTPUT_DIR, f"mgc_{tf}.csv")
        if os.path.exists(filepath):
            df = pd.read_csv(filepath)
            start_ts = pd.to_datetime(df['time'].iloc[0], unit='s')
            end_ts = pd.to_datetime(df['time'].iloc[-1], unit='s')
            print(f"  mgc_{tf}.csv: {len(df):>7} bars | {start_ts.date()} to {end_ts.date()}")
        else:
            print(f"  mgc_{tf}.csv: NOT AVAILABLE")

    print("\nDone! Data files ready for backtesting.")
    print("Next: python mgc_diamante_backtest.py")


if __name__ == "__main__":
    main()
