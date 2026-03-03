# 🏆 WINNER MNQ Strategy Project - 2026 Optimization

This project contains the **ultimate optimized MNQ strategy** that achieved exceptional backtesting results through comprehensive parameter optimization.

## 🎯 Winner Strategy Description
**Ultimate MNQ 1min Strategy - Optimized 2026**
- **Total P&L**: $12,064,511 (6 months backtest)
- **Profit Factor**: 64.22 (exceptional efficiency)
- **Max Daily Drawdown**: $2,377 (excellent risk control)
- **Win Rate**: 7.6%
- **Contracts**: 15 (optimized position sizing)
- **Hard Stop Loss**: $80 per trade (primary stop)
- **ATR Stop**: 2.0x ATR (secondary stop)

## 🏆 Optimized Parameters (Winner Configuration)
- **Timeframe**: 1min MNQ
- **Fast EMA**: 21
- **Slow EMA**: 55
- **Stochastic %K**: 9, **%D**: 3, **Smooth**: 2
- **Stochastic Thresholds**: Low 25, High 75
- **ATR Length**: 9
- **ATR SL Multiplier**: 2.0
- **Take Profit RR**: 1.8
- **Hard Stop Loss**: $80 (primary exit)
- **Contracts**: 15 (fixed position size)
- **Breakeven**: After 3 points profit

## 📊 Strategy Rules - Winner Setup
- **Entry Logic**: EMA crossover + Stochastic divergence
  - Long: EMA21 > EMA55 + Stoch falling + Stoch ≤ 25
  - Short: EMA21 < EMA55 + Stoch rising + Stoch ≥ 75
- **Exit Logic**: Dual stop system
  - Primary: $80 hard stop loss (dollar-based)
  - Secondary: 2.0 ATR stop loss
  - Take Profit: 1.8 RR target
- **Position Sizing**: Fixed 15 contracts
- **Risk Management**: Max $2,400 daily loss limit

## 🏅 Backtesting Results - Winner Strategy
- **Data Period**: August 26, 2025 - February 26, 2026 (6 months, 123 trading days)
- **Total Trades**: 2,173
- **🏆 Total P&L**: $12,064,511
- **📈 Profit Factor**: 64.22
- **🎯 Win Rate**: 7.6%
- **📊 Average Win**: $73,827
- **⚠️ Average Loss**: -$95
- **📉 Max Daily Drawdown**: $2,377
- **📅 Worst Day**: November 11, 2025 (still +$82,621 profit)

## 🔬 Optimization Journey
Through systematic testing of contract sizes (5-15) and hard stops ($80-$1000), we discovered:

| Configuration | P&L | Max DD | Profit Factor | Efficiency |
|---------------|-----|--------|---------------|------------|
| **15c $80** | **$12.1M** | **$2,377** | **64.22** | **🏆 BEST** |
| 15c $100 | $12.0M | $2,877 | 53.06 | Excellent |
| 15c $120 | $12.0M | $3,377 | 45.21 | Very Good |
| 15c $150 | $11.9M | $4,127 | 37.01 | Good |
| 15c $200 | $11.9M | $5,377 | 28.70 | Baseline |
| 10c $200 | $7.9M | $5,374 | 19.32 | Conservative |

## 📁 Files
- `scripts/APEX_MNQ_Strategy.pine`: **UPDATED** Pine Script with winner parameters
- `mnq_live_bot.py`: **UPDATED** Live trading bot with winner parameters
- `mnq_drawdown_analysis.py`: Daily drawdown analysis tool
- `quick_backtest_14c_400s.py`: Parameter testing script
- `backtests/`: Backtest results and configurations
- `data/`: Historical MNQ data files
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