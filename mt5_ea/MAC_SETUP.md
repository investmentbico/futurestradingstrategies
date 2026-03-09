# MT5 PropFirmBot — Mac Setup Guide

## Step 1: Download MT5 for Mac

MT5 has a native Mac version (runs via Wine wrapper built-in):

1. Go to your broker's website (Upcomers) and download MT5 for Mac
   - OR download from: https://www.metatrader5.com/en/download
2. Open the `.dmg` file and drag to Applications
3. Launch MetaTrader 5 from Applications
4. Login with your credentials:
   - Server: `Upcomers`
   - Login: Your account number
   - Password: Your password

## Step 2: Install the Expert Advisor

1. In MT5, go to **File → Open Data Folder**
2. Navigate to `MQL5/Experts/`
3. Copy `PropFirmBot.mq5` into that folder
4. Copy the `.set` preset files into `MQL5/Presets/`

**From Terminal (after finding your MT5 data folder):**
```bash
# Find your MT5 data folder (usually something like):
MT5_DATA="$HOME/Library/Application Support/MetaTrader 5/Bottles/metatrader5/drive_c/Program Files/MetaTrader 5/MQL5"

# Copy EA
cp PropFirmBot.mq5 "$MT5_DATA/Experts/"

# Copy presets
mkdir -p "$MT5_DATA/Presets/"
cp PropFirmBot_XAUUSD.set "$MT5_DATA/Presets/"
cp PropFirmBot_US100.set "$MT5_DATA/Presets/"
```

## Step 3: Compile the EA

1. In MT5, open **MetaEditor** (press F4 or Tools → MetaQuotes Language Editor)
2. Open `PropFirmBot.mq5` from the Experts folder
3. Press **Compile** (F7)
4. Should show "0 errors, 0 warnings"

## Step 4: Run the EA

### For XAUUSD (Gold) — Primary:
1. Open a **XAUUSD M2** (2-minute) chart
2. Drag `PropFirmBot` from the Navigator panel onto the chart
3. In the settings dialog, click **Load** and select `PropFirmBot_XAUUSD.set`
4. Check "Allow Algo Trading" and click OK
5. Make sure the **AutoTrading** button (top toolbar) is ON (green)

### For US100 (NASDAQ) — Secondary:
1. Open a **US100 M2** chart (or NAS100/USTEC depending on your broker)
2. Drag `PropFirmBot` onto the chart
3. Load `PropFirmBot_US100.set` preset
4. Enable AutoTrading

## Step 5: Verify It's Working

- Check the **Experts** tab at the bottom of MT5
- You should see initialization messages:
  ```
  PROP FIRM CHALLENGE BOT — INITIALIZED
  Symbol: XAUUSD
  Lots: 2.0
  Strategy: EMA 13/89 Stoch 25/80 ...
  ```
- Status updates will appear every ~30 ticks showing prices, indicators, and P&L

## Important Notes

- **Always test with a demo account first** before going live
- The EA enforces the **61-second minimum hold time** automatically
- Daily loss limit ($7,500) and profit target ($10,100) auto-stop trading
- Maximum 6 trades per day, 3 consecutive loss pause
- The EA syncs with existing positions on restart (won't double-enter)
- To stop: right-click the chart → Expert Advisors → Remove, or turn off AutoTrading

## Running Both Symbols Simultaneously

You can run XAUUSD and US100 at the same time:
1. Open two separate charts (XAUUSD M2 and US100 M2)
2. Attach the EA to each with its respective preset
3. Use different MagicNumbers (123789 for XAUUSD, 123790 for US100)

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "Trade not allowed" | Enable AutoTrading button + check EA properties |
| Symbol not found | Try NAS100, USTEC, or US100 — varies by broker |
| No signals | Wait for stochastic to reach oversold/overbought zones |
| "OrderSend failed" | Check account has sufficient margin for lot size |
| EA not visible | Recompile in MetaEditor, check for compile errors |
