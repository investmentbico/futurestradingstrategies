@echo off
REM ═══════════════════════════════════════════════════════════
REM  PropFirmBot — Windows VM Quick Setup
REM  Run this once to install everything and start trading
REM ═══════════════════════════════════════════════════════════
setlocal

echo.
echo ============================================================
echo   PROP FIRM BOT — WINDOWS SETUP
echo ============================================================
echo.

REM ── Check Python ──────────────────────────────────────────
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [!] Python not found. Installing via winget...
    winget install Python.Python.3.11 --accept-source-agreements --accept-package-agreements
    if %ERRORLEVEL% neq 0 (
        echo [!] winget failed. Download Python manually from python.org
        echo     Make sure to check "Add to PATH" during install
        pause
        exit /b 1
    )
    echo [OK] Python installed. Restart this script after opening a new terminal.
    pause
    exit /b 0
)
echo [OK] Python found:
python --version

REM ── Check MT5 Terminal ────────────────────────────────────
set MT5_EXE=
if exist "C:\Program Files\MetaTrader 5\terminal64.exe" set MT5_EXE=C:\Program Files\MetaTrader 5\terminal64.exe
if exist "%APPDATA%\MetaTrader 5\terminal64.exe" set MT5_EXE=%APPDATA%\MetaTrader 5\terminal64.exe
if exist "C:\Program Files (x86)\MetaTrader 5\terminal64.exe" set MT5_EXE=C:\Program Files (x86)\MetaTrader 5\terminal64.exe

if "%MT5_EXE%"=="" (
    echo.
    echo [!] MT5 Terminal not found.
    echo     Download from your broker (Upcomers) or https://www.metatrader5.com/en/download
    echo     Install it, log in with your credentials, then re-run this script.
    echo.
    pause
    exit /b 1
)
echo [OK] MT5 found: %MT5_EXE%

REM ── Install Python dependencies ───────────────────────────
echo.
echo [*] Installing Python packages...
pip install MetaTrader5 pandas numpy --quiet
if %ERRORLEVEL% neq 0 (
    echo [!] pip install failed. Trying with --user flag...
    pip install MetaTrader5 pandas numpy --user --quiet
)
echo [OK] Dependencies installed

REM ── Verify MT5 connection ─────────────────────────────────
echo.
echo [*] Testing MT5 connection...
python -c "import MetaTrader5 as mt5; mt5.initialize(); info=mt5.account_info(); print(f'  Account: {info.login} | Balance: ${info.balance:,.2f} | Server: {info.server}') if info else print('  Not logged in — log in via MT5 terminal first'); mt5.shutdown()"

REM ── Create start scripts ──────────────────────────────────
echo.
echo [*] Creating launcher scripts...

REM XAUUSD launcher
(
echo @echo off
echo echo Starting PropFirmBot — XAUUSD ^(Gold^)...
echo echo.
echo python "%~dp0mt5_live_bot.py" --symbol XAUUSD
echo pause
) > "%~dp0START_XAUUSD.bat"

REM US100 launcher
(
echo @echo off
echo echo Starting PropFirmBot — US100 ^(NASDAQ^)...
echo echo.
echo python "%~dp0mt5_live_bot.py" --symbol US100
echo pause
) > "%~dp0START_US100.bat"

REM Dry run launcher
(
echo @echo off
echo echo Starting PropFirmBot — DRY RUN ^(paper trading^)...
echo echo.
echo python "%~dp0mt5_live_bot.py" --symbol XAUUSD --dry-run
echo pause
) > "%~dp0START_DRYRUN.bat"

echo [OK] Created:
echo       START_XAUUSD.bat   — Live gold trading
echo       START_US100.bat    — Live NASDAQ trading
echo       START_DRYRUN.bat   — Paper trading (test first!)

REM ── Done ──────────────────────────────────────────────────
echo.
echo ============================================================
echo   SETUP COMPLETE
echo ============================================================
echo.
echo   NEXT STEPS:
echo   1. Make sure MT5 is running and logged in to Upcomers
echo   2. Double-click START_DRYRUN.bat to paper trade first
echo   3. When ready, use START_XAUUSD.bat for live trading
echo.
echo   The bot will:
echo     - Trade XAUUSD on 2-min bars (EMA 13/89 + Stoch + ATR)
echo     - Enforce 61-second minimum hold time
echo     - Stop at $7,500 daily loss or $10,100 profit target
echo     - Max 6 trades/day, 3 consecutive loss pause
echo     - Log everything to the logs/ folder
echo.
pause
