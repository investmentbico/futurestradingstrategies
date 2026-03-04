# CLAUDE.md

## Project Overview

Algorithmic futures trading system optimized for CME Micro E-mini Nasdaq (MNQ) with support for NQ, ES, and MES. Implements mean-reversion strategies using EMA crossover + Stochastic Oscillator + ATR indicators. Supports live trading via multiple broker APIs (Tastytrade, AMP Futures/FIX, Webull, Rithmic) and comprehensive backtesting against historical data.

## Repository Structure

```
├── scripts/                    # TradingView Pine Script strategies
│   └── APEX_MNQ_Strategy.pine
├── backtests/                  # Backtest engines and optimization
│   ├── backtest.py
│   ├── modes.py
│   ├── optimize.py
│   ├── param_sweep.py
│   └── stochd_backtest.py
├── data/                       # Historical CSV data (time,open,high,low,close)
│   ├── mnq_*.csv              # MNQ at various timeframes
│   ├── nq_*.csv, es_*.csv, mes_*.csv
├── rithmic_sdk/                # Rithmic market data SDK
├── mt5_data/                   # MetaTrader 5 exported data
├── results/                    # Backtest output results
├── requirements.txt            # Core dependencies
├── requirements_data.txt       # Data download dependencies
├── requirements_amp.txt        # AMP Futures dependencies
├── setup.sh                    # Automated environment setup
├── .env.example                # Environment variable template
└── [Python scripts at root]    # Strategies, bots, tests, utilities
```

### Key Python Files

| File | Purpose |
|------|---------|
| `mnq_live_bot.py` | **Primary** live MNQ trading bot (Tastytrade) |
| `amp_mnq_strategy.py` | AMP Futures live trading via FIX protocol |
| `tastytrade_api.py` | Tastytrade OAuth2 API wrapper |
| `webull_api.py` | Webull futures API wrapper |
| `rithmic_api.py` | Rithmic market data API |
| `comprehensive_backtest.py` | Multi-symbol/timeframe backtester |
| `mnq_comprehensive_backtest.py` | MNQ-focused comprehensive backtest |
| `flexible_backtest.py` | Flexible parameter testing |
| `enhanced_strategy_optimizer.py` | Hyperparameter optimization |
| `parameter_optimizer.py` | Parameter grid search |
| `mnq_drawdown_analysis.py` | Daily drawdown analysis |
| `live_dashboard.py` | Real-time monitoring dashboard |
| `data_converter.py` | Data format conversion |
| `data_downloader.py` | Market data acquisition |

## Tech Stack

- **Python 3.x** — core language for all trading logic and backtesting
- **Pandas / NumPy** — data processing, vectorized indicator calculations
- **Matplotlib** — charting and visualization
- **Pine Script** — TradingView strategy implementations
- **FIX Protocol (quickfix)** — order execution for AMP Futures
- **python-dotenv** — environment variable management

## Setup

```bash
# Automated setup (creates venv, installs deps, creates .env)
./setup.sh

# Manual setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in broker credentials
```

## Environment Variables

Broker credentials and trading parameters are configured via `.env` (see `.env.example`):
- `TASTYTRADE_USER` / `TASTYTRADE_PASSWORD` — Tastytrade authentication
- `WEBULL_APP_KEY` / `WEBULL_APP_SECRET` — Webull API access
- `AMP_USERNAME` / `AMP_PASSWORD` / `AMP_ACCOUNT_ID` / `AMP_SENDER_ID` — AMP FIX connection
- `TRADING_SYMBOL` — target instrument (default: MNQ)
- `MAX_TRADES_PER_SESSION`, `ACCOUNT_RISK_PERCENT` — risk controls

**Never commit `.env` files or real credentials.**

## Running Backtests

```bash
# Comprehensive multi-symbol backtest
python comprehensive_backtest.py

# MNQ-specific backtest
python mnq_comprehensive_backtest.py

# Parameter sweep / optimization
python backtests/param_sweep.py
python enhanced_strategy_optimizer.py
```

Backtests read CSV data from `data/` (Unix timestamp, OHLC format) and output results to `results/`.

## Running Live Trading

```bash
# Tastytrade MNQ bot (primary)
python mnq_live_bot.py

# AMP Futures bot
python amp_mnq_strategy.py
```

Live bots run during NY session hours (9:30 AM – 4:00 PM ET).

## Testing

```bash
# Run specific test files
python test_indicators.py
python test_amp_strategy.py
python test_optimal_params.py
python test_mnq_data.py
```

Tests follow `test_*.py` naming and live at the project root. There is no formal test runner (no pytest/unittest configuration) — tests are run individually.

## Strategy Parameters

Two primary EMA configurations are used:

| Config | Fast EMA | Slow EMA | Use Case |
|--------|----------|----------|----------|
| AMP-optimized | 34 | 89 | AMP Futures |
| Webull-optimized | 21 | 55 | Tastytrade / Webull |

Common parameters: Stochastic thresholds (25/75), ATR period 9, hard stop $80, take-profit 1.8 R:R, 3–15 contracts.

Parameters are currently hardcoded in their respective Python files.

## Code Conventions

- **No formal linter or formatter** is configured (no flake8, black, pylint, or pre-commit hooks)
- Code generally follows PEP 8 style
- Mix of function-based and class-based patterns
- Type hints used inconsistently
- Docstrings present but format varies
- No CI/CD pipeline is actively configured

## Architecture Patterns

1. **Broker abstraction** — each broker has its own API wrapper class (`tastytrade_api.py`, `webull_api.py`, etc.) with a common interface pattern
2. **Vectorized indicators** — NumPy-based EMA, ATR, Stochastic calculations for performance
3. **Dual stop-loss system** — hard dollar stop + ATR-based dynamic stop on every trade
4. **Daily risk limits** — max daily loss caps and position size constraints
5. **Bar-by-bar backtesting** — simulates trades sequentially with fee/slippage modeling

## Data Format

Historical CSV files in `data/` use the format:
```
time,open,high,low,close
1697000000,15200.50,15210.25,15195.75,15205.00
```
- `time`: Unix timestamp (seconds)
- Timeframes available: 1min, 2min, 5min, 15min
- Instruments: MNQ, NQ, ES, MES

## Documentation

- `README.md` — project overview
- `AMP_MNQ_README.md` / `AMP_MNQ_COMPLETE_GUIDE.md` — AMP Futures setup and cost analysis
- `GITHUB_SETUP.md` — repository setup
- `MT5_Export_Guide.md` — MetaTrader 5 data export
- `TRADING_SYSTEM_RESULTS.md` / `MNQ_BACKTEST_RESULTS.md` — performance summaries

## Important Notes for AI Assistants

- **Security**: Never expose or log broker credentials. Always use environment variables.
- **Live trading risk**: Changes to strategy logic, position sizing, or stop-loss parameters directly affect real money. Test all changes via backtesting before any live deployment.
- **Data integrity**: CSV data files in `data/` are source-of-truth for backtests. Do not modify them without explicit instruction.
- **Parameter changes**: Strategy parameters are hardcoded — changing them in one file does not propagate to others. Verify which file is being used for the target broker.
- **Trading hours**: NY session 9:30 AM – 4:00 PM ET. Bots are designed for this window.
