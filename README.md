# APEX MNQ Strategy Project

This project contains the Pine Script strategy for trading MNQ futures, focused on backtesting with real data to achieve closest to real results.

## Strategy Description
Conservative Trend-Momentum ATR Strategy with Risk % sizing:
- EMA crossover (fast/slow) for trend
- RSI filter for momentum
- ATR-based stop loss
- Dynamic position sizing based on risk % of equity
- Take profit at 2:1 RR
- ATR trailing stop
- Breakeven after $100 profit
- Daily drawdown lock at 1%

## Optimized Parameters (Alpha Mode - Momentum Filter)
- Timeframe: 2min
- Fast EMA: 21
- Slow EMA: 50
- RSI Length: 14
- RSI Threshold: Longs >60, Shorts <40
- ATR Length: 14
- ATR SL Multiplier: 2.0
- Risk % per trade: 0.5
- Take Profit RR: 2.0
- Trailing ATR Mult: 1.0
- Breakeven after $100 profit
- Momentum Filter: Close > Close[5] for longs, opposite for shorts

## Backtesting Results (Top 3 Modes)
- Data period: Sep 11, 2025 to Feb 24, 2026 (~5.5 months)
- **Top 1: Alpha Mode** - PNL $168,238, Win Rate 37.77%, Profit Factor 2.10, Max Daily DD $3,763
- Top 2: Beta Mode (Volatility) - PNL $141,634, Win Rate 35.11%, Profit Factor 2.12, Max Daily DD $3,977
- Top 3: Gamma Mode (Trend) - PNL $110,872, Win Rate 35.16%, Profit Factor 1.75, Max Daily DD $4,178
- Other timeframes tested: 1min (-32.69%), 5min (-36.86%), 15min (-43.38%)

## Strategy Rules
- Prop firm account: 300K
- Daily drawdown limit: 1% (cannot go under 1% drawdown daily)
- Trading hours: 7am to 3:45pm NY time
- Instrument: MNQ

## Files
- `scripts/APEX_MNQ_Strategy.pine`: Main Pine Script strategy
- `backtests/backtest.py`: Python script for backtesting with real data
- `backtest_nq.py`: Enhanced backtesting script with live trading support
- `webull_api.py`: Webull API integration for live trading
- `backtests/`: Directory for backtest results and configurations
- `data/`: Directory for historical data files (CSV format)

## Live Trading Setup

1. **Get Webull API Credentials**:
   - Visit https://developer.webull.com/
   - Create a developer account
   - Generate API key and secret

2. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your API credentials
   ```

4. **Run Live Trading**:
   ```bash
   # Demo trading (recommended first)
   python backtest_nq.py --live --demo

   # Live trading (use real money)
   python backtest_nq.py --live --no-demo

   # Limited trades for testing
   python backtest_nq.py --live --demo --max-trades 5
   ```

## Backtesting with Real Data
1. Place your MNQ historical data CSV in `data/` with columns: time (unix), open, high, low, close
2. Adjust parameters in `backtests/backtest.py` for different timeframes/data
3. Run the backtest: `python3 backtests/backtest.py`
4. Results will be saved to `results.csv` and equity curve can be plotted

## Usage
Load the Pine Script in TradingView on MNQ 2min chart, use the optimized inputs for live/paper trading.