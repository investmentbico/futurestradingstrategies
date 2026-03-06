#!/usr/bin/env python3
"""
Monday Auto-Start Launcher
===========================
Automatically launches mnq_live_bot.py at 9:29 AM ET on Monday 2026-03-09.
No user interaction required - fully autonomous.

Usage:
    python monday_autostart.py          # Wait for Monday 9:29 AM ET, then launch
    python monday_autostart.py --now    # Launch immediately (skip wait)
"""

import subprocess
import sys
import os
import time
from datetime import datetime

# Target: Monday March 9, 2026 at 9:29:00 AM ET
TARGET_HOUR = 9
TARGET_MINUTE = 29
TARGET_WEEKDAY = 0  # Monday

BOT_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mnq_live_bot.py")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mnq_live_trading.log")


def get_et_now():
    """Get current time in ET (UTC-5 EST / UTC-4 EDT).
    March 9 2026 is after spring-forward (Mar 8), so EDT = UTC-4."""
    import calendar
    utc_now = datetime.utcnow()
    # March 9, 2026 is EDT (UTC-4)
    from datetime import timedelta
    return utc_now - timedelta(hours=4)


def wait_for_market_open():
    """Sleep until Monday 9:29 AM ET."""
    while True:
        now_et = get_et_now()
        print(f"[AUTOSTART] Current ET time: {now_et.strftime('%Y-%m-%d %H:%M:%S')} (weekday={now_et.weekday()})")

        # Check if it's Monday and past 9:29 AM
        if now_et.weekday() == TARGET_WEEKDAY:
            if now_et.hour > TARGET_HOUR or (now_et.hour == TARGET_HOUR and now_et.minute >= TARGET_MINUTE):
                print(f"[AUTOSTART] Market window OPEN - launching bot!")
                return
            else:
                # Monday but before 9:29 - calculate seconds to wait
                from datetime import timedelta
                target = now_et.replace(hour=TARGET_HOUR, minute=TARGET_MINUTE, second=0, microsecond=0)
                wait_secs = (target - now_et).total_seconds()
                print(f"[AUTOSTART] Monday detected - waiting {wait_secs:.0f}s until 9:29 AM ET")
                if wait_secs > 60:
                    time.sleep(60)  # Check every minute
                else:
                    time.sleep(max(1, wait_secs))
        else:
            # Not Monday - sleep longer
            print(f"[AUTOSTART] Not Monday yet. Sleeping 5 minutes...")
            time.sleep(300)


def launch_bot():
    """Launch the trading bot as a subprocess."""
    print(f"[AUTOSTART] Launching: python {BOT_SCRIPT} --live --max-trades 10")
    print(f"[AUTOSTART] Config: 5 contracts, $50 stop, $13,310 target, PF 8.40")
    print(f"[AUTOSTART] Polling: 0.5 sec intervals")
    print(f"[AUTOSTART] Log: {LOG_FILE}")
    print("=" * 60)

    # Launch bot - inherit stdout/stderr so we see output
    process = subprocess.Popen(
        [sys.executable, BOT_SCRIPT, "--live", "--max-trades", "10"],
        cwd=os.path.dirname(os.path.abspath(__file__)),
        stdout=sys.stdout,
        stderr=sys.stderr
    )

    print(f"[AUTOSTART] Bot PID: {process.pid}")

    try:
        process.wait()
    except KeyboardInterrupt:
        print("\n[AUTOSTART] Shutting down bot...")
        process.terminate()
        process.wait(timeout=10)

    return process.returncode


def main():
    print("=" * 60)
    print("  MNQ MONDAY AUTO-START LAUNCHER")
    print("  5 contracts | $50 stop | $13,310 target | PF 8.40")
    print("  Target: Monday 9:29 AM ET - Fully Autonomous")
    print("=" * 60)

    if "--now" in sys.argv:
        print("[AUTOSTART] --now flag: launching immediately")
    else:
        wait_for_market_open()

    rc = launch_bot()
    print(f"[AUTOSTART] Bot exited with code: {rc}")
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
