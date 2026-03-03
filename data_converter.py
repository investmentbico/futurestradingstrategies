#!/usr/bin/env python3
"""
Data Converter - Generate Intraday Data from Daily Data
=======================================================

Converts daily futures data to intraday timeframes using resampling.
This creates synthetic intraday data for backtesting when real intraday data is unavailable.

Supported conversions:
- Daily → 1min, 2min, 5min, 15min, 30min, 1h

Usage:
    python data_converter.py --input mnq_1d.csv --output-dir data --timeframes 1min 5min 15min
    python data_converter.py --convert-all --timeframes 1min 5min 15min
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import argparse
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# DATA CONVERTER FUNCTIONS
# =============================================================================

def load_daily_data(filepath: str) -> pd.DataFrame:
    """Load daily data from CSV file"""
    try:
        df = pd.read_csv(filepath)

        # Convert time column to datetime
        if 'time' in df.columns:
            df['timestamp'] = pd.to_datetime(df['time'], unit='s')
        elif 'timestamp' in df.columns:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        else:
            print(f"❌ No timestamp column found in {filepath}")
            return None

        # Ensure we have OHLC columns
        required_cols = ['open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            print(f"❌ Missing OHLC columns in {filepath}")
            return None

        # Set timestamp as index
        df = df.set_index('timestamp').sort_index()

        print(f"✅ Loaded {len(df)} daily bars from {filepath}")
        return df

    except Exception as e:
        print(f"❌ Error loading {filepath}: {e}")
        return None

def generate_intraday_from_daily(df_daily: pd.DataFrame, timeframe: str,
                                trading_hours: dict = None) -> pd.DataFrame:
    """
    Generate intraday data from daily data using resampling

    Args:
        df_daily: Daily OHLC DataFrame
        timeframe: Target timeframe ('1min', '5min', etc.)
        trading_hours: Dict with 'start' and 'end' times for filtering
    """
    if df_daily is None or df_daily.empty:
        return None

    # Default CME futures trading hours (NY time)
    if trading_hours is None:
        trading_hours = {
            'start': '09:30',  # 9:30 AM ET
            'end': '16:00'     # 4:00 PM ET
        }

    print(f"🔄 Converting daily data to {timeframe} intraday...")

    # Convert timeframe to pandas frequency
    freq_map = {
        '1min': '1min',
        '2min': '2min',
        '5min': '5min',
        '15min': '15min',
        '30min': '30min',
        '1h': '1H'
    }

    if timeframe not in freq_map:
        print(f"❌ Unsupported timeframe: {timeframe}")
        return None

    freq = freq_map[timeframe]

    # For each trading day, generate intraday bars
    intraday_bars = []

    for date, daily_bar in df_daily.iterrows():
        try:
            # Create intraday timestamps for this day
            start_time = pd.Timestamp.combine(date.date(), pd.Timestamp(trading_hours['start']).time())
            end_time = pd.Timestamp.combine(date.date(), pd.Timestamp(trading_hours['end']).time())

            # Generate timestamps at target frequency
            timestamps = pd.date_range(start=start_time, end=end_time, freq=freq)

            if len(timestamps) == 0:
                continue

            # Generate OHLC data using random walk within daily range
            daily_open = daily_bar['open']
            daily_high = daily_bar['high']
            daily_low = daily_bar['low']
            daily_close = daily_bar['close']

            # Calculate daily range and volatility
            daily_range = daily_high - daily_low
            volatility = daily_range / daily_open  # Rough volatility measure

            # Generate synthetic intraday prices
            np.random.seed(int(date.timestamp()))  # Reproducible results

            # Start from daily open
            current_price = daily_open

            for i, ts in enumerate(timestamps):
                # Random walk with mean reversion to daily close
                progress = i / len(timestamps)  # 0 to 1 through the day

                # Mean reversion factor (tend toward daily close as day progresses)
                target_price = daily_open + (daily_close - daily_open) * progress

                # Random movement based on volatility
                move = np.random.normal(0, volatility * 0.1 * daily_open)

                # Apply mean reversion
                current_price += move + 0.1 * (target_price - current_price)

                # Ensure price stays within daily range with some tolerance
                tolerance = daily_range * 0.1
                current_price = np.clip(current_price,
                                      daily_low - tolerance,
                                      daily_high + tolerance)

                # Generate OHLC for this bar
                bar_volatility = volatility * 0.05 * daily_open
                bar_open = current_price
                bar_close = current_price + np.random.normal(0, bar_volatility)
                bar_high = max(bar_open, bar_close) + abs(np.random.normal(0, bar_volatility))
                bar_low = min(bar_open, bar_close) - abs(np.random.normal(0, bar_volatility))

                # Ensure OHLC relationships
                bar_high = max(bar_open, bar_high, bar_close)
                bar_low = min(bar_open, bar_low, bar_close)

                intraday_bars.append({
                    'timestamp': ts,
                    'open': round(bar_open, 2),
                    'high': round(bar_high, 2),
                    'low': round(bar_low, 2),
                    'close': round(bar_close, 2),
                    'volume': np.random.randint(100, 1000)  # Random volume
                })

        except Exception as e:
            print(f"⚠️  Error processing {date}: {e}")
            continue

    if not intraday_bars:
        print("❌ No intraday bars generated")
        return None

    # Create DataFrame
    df_intraday = pd.DataFrame(intraday_bars)
    df_intraday = df_intraday.sort_values('timestamp').reset_index(drop=True)

    # Add time column (Unix timestamp)
    df_intraday['time'] = df_intraday['timestamp'].astype(np.int64) // 10**9

    # Select final columns
    df_intraday = df_intraday[['time', 'open', 'high', 'low', 'close']]

    print(f"✅ Generated {len(df_intraday)} {timeframe} bars")
    return df_intraday

def convert_file(input_file: str, timeframes: list, output_dir: str = 'data') -> None:
    """Convert a single daily data file to multiple intraday timeframes"""
    if not os.path.exists(input_file):
        print(f"❌ Input file not found: {input_file}")
        return

    # Load daily data
    df_daily = load_daily_data(input_file)
    if df_daily is None:
        return

    # Extract symbol from filename
    basename = os.path.basename(input_file)
    symbol = basename.split('_')[0].upper()

    print(f"🔄 Converting {symbol} daily data to intraday...")

    # Convert to each timeframe
    for timeframe in timeframes:
        try:
            df_intraday = generate_intraday_from_daily(df_daily, timeframe)

            if df_intraday is not None:
                # Save to file
                output_file = os.path.join(output_dir, f"{symbol.lower()}_{timeframe}.csv")
                df_intraday.to_csv(output_file, index=False)
                print(f"💾 Saved {len(df_intraday)} bars to {output_file}")

        except Exception as e:
            print(f"❌ Error converting to {timeframe}: {e}")

def convert_all_daily_files(timeframes: list, output_dir: str = 'data') -> None:
    """Convert all daily data files in the output directory"""
    if not os.path.exists(output_dir):
        print(f"❌ Output directory not found: {output_dir}")
        return

    # Find all daily CSV files
    daily_files = []
    for file in os.listdir(output_dir):
        if file.endswith('_1d.csv') or file.endswith('_daily.csv'):
            daily_files.append(os.path.join(output_dir, file))

    if not daily_files:
        print(f"❌ No daily data files found in {output_dir}")
        return

    print(f"📊 Found {len(daily_files)} daily data files to convert")

    for daily_file in daily_files:
        convert_file(daily_file, timeframes, output_dir)

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def validate_data_file(filepath: str) -> bool:
    """Validate that a data file has the correct format"""
    try:
        df = pd.read_csv(filepath)

        required_cols = ['time', 'open', 'high', 'low', 'close']
        if not all(col in df.columns for col in required_cols):
            print(f"❌ Missing required columns in {filepath}")
            return False

        if len(df) == 0:
            print(f"❌ Empty data file: {filepath}")
            return False

        # Check for reasonable OHLC values
        for col in ['open', 'high', 'low', 'close']:
            if df[col].isnull().any():
                print(f"❌ Null values in {col} column: {filepath}")
                return False

        print(f"✅ Valid data file: {filepath} ({len(df)} bars)")
        return True

    except Exception as e:
        print(f"❌ Error validating {filepath}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description='Convert daily futures data to intraday')
    parser.add_argument('--input', help='Input daily CSV file')
    parser.add_argument('--output-dir', default='data', help='Output directory')
    parser.add_argument('--timeframes', nargs='+', default=['1min', '5min', '15min'],
                       help='Target timeframes (default: 1min 5min 15min)')
    parser.add_argument('--convert-all', action='store_true',
                       help='Convert all daily files in output directory')
    parser.add_argument('--validate', nargs='+',
                       help='Validate data files')

    args = parser.parse_args()

    if args.validate:
        for filepath in args.validate:
            validate_data_file(filepath)
        return

    if args.convert_all:
        convert_all_daily_files(args.timeframes, args.output_dir)
    elif args.input:
        convert_file(args.input, args.timeframes, args.output_dir)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()