# MT5 Data Export Instructions for MNQ Strategy Backtesting

## Step-by-Step Guide to Export MNQ Data from MT5 Demo Account

### 1. Open MT5 Platform

- Launch your MT5 trading platform
- Ensure you're logged into your demo account

### 2. Navigate to Historical Data

- Go to **Tools** → **History Center** (or press F2)
- In the History Center window, expand **Forex** or **Futures**
- Look for **MNQ** (Micro E-mini Nasdaq-100 Futures)
- Select the **1 Minute** timeframe

### 3. Export the Data

- Right-click on **MNQ** in the History Center
- Select **Export to CSV**
- Choose a save location (desktop recommended)
- Name the file something like `MT5_MNQ_1min_export.csv`
- Click **Save**

### 4. Verify Export Settings

Make sure the export includes:

- ✅ Date/Time
- ✅ Open/High/Low/Close prices
- ✅ Volume
- ✅ All data points for your desired time range

### 5. Process the Data

Once exported, run this command in your terminal:

```bash
cd /Users/sunflowerhd/Desktop/FUTURE
python3 mt5_data_extractor.py --input MT5_MNQ_1min_export.csv --output mt5_processed_data.csv
```

### 6. Analyze the Results

The script will:

- Convert MT5 timestamps to standard format
- Validate data integrity
- Show summary statistics
- Export clean CSV for backtesting

### 7. Run Backtest on MT5 Data

After processing, you can run your AMP backtest on the MT5 data:

```bash
python3 amp_mnq_backtest.py --data mt5_processed_data.csv --compare
```

## Troubleshooting

### If Export Fails

- Make sure you have sufficient historical data downloaded
- Check that MNQ is available in your demo account
- Try exporting smaller time ranges first

### If Processing Fails

- Verify the CSV file isn't corrupted
- Check that all required columns are present
- Ensure the file path is correct

### Common MT5 Export Issues

- MT5 sometimes exports with different column names
- Date formats may vary by region
- Some versions export volume as 'tick_volume' instead of 'volume'

The `mt5_data_extractor.py` script handles these variations automatically.

## Expected Output

After successful processing, you should see:

- Data validation summary
- Timestamp conversion confirmation
- Statistical overview of the data
- Clean CSV file ready for backtesting

## Next Steps

Once you have the MT5 data processed, we can:

1. Compare it with your existing CME data
2. Run the same backtest strategy on both datasets
3. Validate consistency across platforms
4. Analyze any discrepancies in performance
