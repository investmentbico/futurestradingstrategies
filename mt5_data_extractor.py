#!/usr/bin/env python3
"""
MT5 Data Extractor for MNQ Strategy
Extracts historical data from MetaTrader 5 demo account
"""

import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MT5DataExtractor:
    """
    Extract and process MT5 historical data for MNQ
    """

    def __init__(self):
        self.data_dir = "mt5_data"
        os.makedirs(self.data_dir, exist_ok=True)

    def extract_from_mt5_export(self, csv_file_path: str) -> pd.DataFrame:
        """
        Process data exported from MT5 platform
        Expected format: MT5 CSV export with headers
        """
        try:
            logger.info(f"Loading MT5 data from: {csv_file_path}")

            # MT5 typically exports with semicolon or tab separators
            # Try different separators
            for sep in [';', '\t', ',']:
                try:
                    df = pd.read_csv(csv_file_path, sep=sep, header=0)
                    if len(df.columns) >= 5:  # At least timestamp, OHLC
                        break
                except:
                    continue
            else:
                raise ValueError("Could not parse MT5 CSV file")

            logger.info(f"Loaded {len(df)} rows with columns: {list(df.columns)}")

            # MT5 column names vary, let's detect them
            df = self._standardize_mt5_columns(df)

            # Convert timestamp if needed
            df = self._process_timestamps(df)

            # Filter to last 4 months
            df = self._filter_recent_data(df)

            # Validate data
            df = self._validate_data(df)

            logger.info(f"Processed {len(df)} MNQ bars")
            return df

        except Exception as e:
            logger.error(f"Error processing MT5 data: {e}")
            raise

    def _standardize_mt5_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Standardize MT5 column names to our format
        """
        # MT5 typical column names (may vary)
        mt5_columns = {
            '<DATE>': 'date',
            '<TIME>': 'time',
            '<OPEN>': 'open',
            '<HIGH>': 'high',
            '<LOW>': 'low',
            '<CLOSE>': 'close',
            '<TICKVOL>': 'volume',
            '<VOL>': 'volume',
            '<SPREAD>': 'spread',
            # Handle uppercase without brackets (common in some MT5 exports)
            'DATE': 'date',
            'TIME': 'time',
            'OPEN': 'open',
            'HIGH': 'high',
            'LOW': 'low',
            'CLOSE': 'close',
            'TICKVOL': 'volume',
            'VOL': 'volume',
            'SPREAD': 'spread',
            # Handle lowercase variations
            'date': 'date',
            'time': 'time',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'tickvol': 'volume',
            'vol': 'volume',
            'spread': 'spread'
        }

        # Rename columns
        df = df.rename(columns=mt5_columns)

        # If we have separate date/time columns, combine them
        if 'date' in df.columns and 'time' in df.columns:
            df['timestamp'] = pd.to_datetime(df['date'] + ' ' + df['time'])
            df = df.drop(['date', 'time'], axis=1)
        elif 'timestamp' not in df.columns:
            # Assume first column is timestamp
            timestamp_col = df.columns[0]
            df = df.rename(columns={timestamp_col: 'timestamp'})
            df['timestamp'] = pd.to_datetime(df['timestamp'])

        # Ensure we have OHLC
        required_cols = ['open', 'high', 'low', 'close']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            raise ValueError(f"Missing required columns: {missing_cols}")

        # Select and reorder columns
        cols = ['timestamp', 'open', 'high', 'low', 'close']
        if 'volume' in df.columns:
            cols.append('volume')

        return df[cols]

    def _process_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Process and validate timestamps
        """
        # Ensure timestamp column exists
        if 'timestamp' not in df.columns:
            raise ValueError("No timestamp column found")

        # Convert to datetime if needed
        df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')

        # Remove invalid timestamps
        df = df.dropna(subset=['timestamp'])

        # Sort by timestamp
        df = df.sort_values('timestamp').reset_index(drop=True)

        return df

    def _filter_recent_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Filter to last 4 months of data
        """
        current_date = pd.Timestamp('2026-02-26')
        four_months_ago = current_date - pd.Timedelta(days=120)

        mask = df['timestamp'] >= four_months_ago
        df_filtered = df[mask].copy()

        logger.info(f"Filtered from {len(df)} to {len(df_filtered)} rows (last 4 months)")
        return df_filtered

    def _validate_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Validate and clean the data
        """
        # Remove rows with invalid OHLC
        for col in ['open', 'high', 'low', 'close']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        # Drop rows with NaN in OHLC
        df = df.dropna(subset=['open', 'high', 'low', 'close'])

        # Ensure high >= max(open, close) and low <= min(open, close)
        df = df[
            (df['high'] >= df[['open', 'close']].max(axis=1)) &
            (df['low'] <= df[['open', 'close']].min(axis=1))
        ]

        # Remove zero volume bars (if volume column exists)
        if 'volume' in df.columns:
            df = df[df['volume'] > 0]

        return df.reset_index(drop=True)

    def save_processed_data(self, df: pd.DataFrame, filename: str = "mnq_mt5_processed.csv"):
        """
        Save processed data to CSV
        """
        output_path = os.path.join(self.data_dir, filename)
        df.to_csv(output_path, index=False)
        logger.info(f"Saved processed data to: {output_path}")
        return output_path

    def convert_to_unix_timestamp(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Convert timestamps to Unix format for compatibility
        """
        df_copy = df.copy()
        df_copy['time'] = df_copy['timestamp'].astype(int) // 10**9
        df_copy = df_copy[['time', 'open', 'high', 'low', 'close']]
        if 'volume' in df_copy.columns:
            df_copy = df_copy[['time', 'open', 'high', 'low', 'close', 'volume']]
        return df_copy

def analyze_mt5_fees():
    """
    Analyze typical MT5 demo account fees
    """
    print("💰 MT5 DEMO ACCOUNT FEE ANALYSIS")
    print("=" * 50)

    # MT5 Demo Account Fee Structure
    mt5_fees = {
        'commission_per_lot': 0.0,  # Demo accounts usually no commission
        'spread_mnq': 0.5,          # Typical MNQ spread in points
        'swap_long': -0.5,          # Daily swap for long positions
        'swap_short': 0.2,          # Daily swap for short positions
        'minimum_lot': 0.01,        # Micro lots
        'contract_size': 20,        # MNQ contract size
        'leverage': 100,            # Typical leverage
        'margin_requirement': 0.01  # 1% margin for demo
    }

    print("📊 MT5 Demo Account Fee Structure:")
    print(f"   Commission per Lot: ${mt5_fees['commission_per_lot']:.2f}")
    print(f"   MNQ Spread: {mt5_fees['spread_mnq']} points")
    print(f"   Long Swap: ${mt5_fees['swap_long']:.2f}/lot/day")
    print(f"   Short Swap: ${mt5_fees['swap_short']:.2f}/lot/day")
    print(f"   Contract Size: {mt5_fees['contract_size']} points")
    print(f"   Leverage: {mt5_fees['leverage']}:1")
    print()

    # Calculate trading costs for our strategy
    contracts = 15
    avg_daily_roundtrips = 10  # Estimate based on backtest

    daily_commission = mt5_fees['commission_per_lot'] * contracts * avg_daily_roundtrips
    daily_spread_cost = mt5_fees['spread_mnq'] * mt5_fees['contract_size'] * contracts * avg_daily_roundtrips
    daily_swap = mt5_fees['swap_long'] * contracts  # Assuming mostly long positions

    total_daily_cost = daily_commission + daily_spread_cost + daily_swap

    print("💸 Daily Cost Estimate (15 contracts, 10 round trips):")
    print(f"   Commission: ${daily_commission:.2f}")
    print(f"   Spread Cost: ${daily_spread_cost:.2f}")
    print(f"   Swap Cost: ${daily_swap:.2f}")
    print(f"   Total Daily Cost: ${total_daily_cost:.2f}")
    print()

    print("📈 Comparison with Live Broker Fees:")
    print("   MT5 Demo: $0.00 commission (unrealistic)")
    print("   AMP Futures: $0.015/contract ($0.03 round trip)")
    print("   Webull: $0.02/contract ($0.04 round trip)")
    print()

    print("⚠️  IMPORTANT NOTES:")
    print("   • Demo accounts have NO commissions (not realistic)")
    print("   • Spreads may be wider than live accounts")
    print("   • No slippage in demo (unrealistic execution)")
    print("   • Use live account data for accurate backtesting")

def create_mt5_export_instructions():
    """
    Create instructions for exporting data from MT5
    """
    instructions = """
📋 MT5 DATA EXPORT INSTRUCTIONS
================================

1. OPEN MT5 PLATFORM:
   - Make sure MT5 is running and logged into demo account

2. OPEN CHART:
   - Search for MNQ symbol (Micro E-mini Nasdaq)
   - Open 1-minute timeframe chart

3. EXPORT HISTORICAL DATA:
   - Right-click on chart
   - Select "Export" → "Export to CSV"
   - Choose date range (last 4 months)
   - Select format: "All ticks" or "1 minute OHLC"
   - Save file as: mnq_mt5_export.csv

4. ALTERNATIVE METHOD:
   - Go to Tools → History Center
   - Select MNQ symbol
   - Right-click → Export to CSV

5. EXPECTED FORMAT:
   <DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<TICKVOL>
   2026.02.26,10:30,24950.5,24955.2,24948.1,24952.8,1250

6. RUN PROCESSING SCRIPT:
   python3 mt5_data_extractor.py --input mnq_mt5_export.csv
"""

    print(instructions)

    # Save instructions to file
    with open("MT5_EXPORT_INSTRUCTIONS.txt", "w") as f:
        f.write(instructions)
    print("💾 Instructions saved to: MT5_EXPORT_INSTRUCTIONS.txt")

def main():
    """
    Main function to extract and process MT5 data
    """
    import argparse

    parser = argparse.ArgumentParser(description='MT5 Data Extractor for MNQ')
    parser.add_argument('--input', help='Path to MT5 exported CSV file')
    parser.add_argument('--analyze-fees', action='store_true', help='Analyze MT5 fee structure')
    parser.add_argument('--instructions', action='store_true', help='Show export instructions')

    args = parser.parse_args()

    if args.instructions:
        create_mt5_export_instructions()
        return

    if args.analyze_fees:
        analyze_mt5_fees()
        return

    if not args.input:
        print("❌ Please provide MT5 CSV file path with --input")
        print("💡 Run with --instructions for export guide")
        return

    # Process MT5 data
    extractor = MT5DataExtractor()

    try:
        # Extract and process data
        df = extractor.extract_from_mt5_export(args.input)

        # Save processed data
        processed_file = extractor.save_processed_data(df)

        # Convert to Unix timestamp format
        df_unix = extractor.convert_to_unix_timestamp(df)
        unix_file = extractor.save_processed_data(df_unix, "mnq_mt5_unix.csv")

        print("✅ MT5 Data Processing Complete!")
        print(f"📊 Processed: {len(df)} bars")
        print(f"📁 Files saved in: {extractor.data_dir}/")
        print(f"   • {processed_file}")
        print(f"   • {unix_file}")

        # Quick stats
        print("\n📈 Data Summary:")
        print(f"   Start: {df['timestamp'].min()}")
        print(f"   End: {df['timestamp'].max()}")
        print(f"   Avg Range: {((df['high'] - df['low']) * 20).mean():.2f} points")
        print(f"   Avg Volume: {df.get('volume', pd.Series([0])).mean():.0f}")

    except Exception as e:
        logger.error(f"Processing failed: {e}")
        print("💡 Try running with --instructions for export help")

if __name__ == "__main__":
    main()