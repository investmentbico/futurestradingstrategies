#!/usr/bin/env python3
"""
TEST LIVE ORDER - 1 MNQ Contract with $25 Stop Loss
=====================================================
Sends a single test trade on your REAL Tastytrade account:
  - BUY 1 MNQ at market
  - Stop loss: $25 max loss (12.5 points x $2/point)
  - Auto-close when in profit (any profit > $2 after commissions)
  - Auto-close on stop loss hit
  - 5-minute timeout safety net

Usage:
  python test_live_order.py          # Dry run (no real orders)
  python test_live_order.py --exec   # Execute real orders on live account
"""

import os
import sys
import time
import argparse
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tastytrade_api import TastytradeAPI

# =============================================================================
# TEST PARAMETERS
# =============================================================================
CONTRACTS = 1
POINT_VALUE = 2.0          # MNQ = $2 per point
MAX_LOSS_DOLLARS = 25.0    # $25 max loss
STOP_POINTS = MAX_LOSS_DOLLARS / (CONTRACTS * POINT_VALUE)  # 12.5 points
MIN_PROFIT_DOLLARS = 3.0   # Close when profit > $3 (covers commission)
COMMISSION_RT = 2.52       # Tastytrade round-trip per contract
TIMEOUT_SECONDS = 300      # 5-minute safety timeout
POLL_INTERVAL = 1.0        # Check price every 1 second


def main():
    parser = argparse.ArgumentParser(description='Test live MNQ order')
    parser.add_argument('--exec', action='store_true',
                        help='Actually execute orders (default: dry run)')
    args = parser.parse_args()

    dry_run = not args.exec

    print("=" * 70)
    print("  MNQ LIVE ORDER TEST - 1 Contract")
    print("=" * 70)
    print(f"  Mode:          {'DRY RUN (no real orders)' if dry_run else 'LIVE EXECUTION'}")
    print(f"  Contracts:     {CONTRACTS}")
    print(f"  Max Loss:      ${MAX_LOSS_DOLLARS:.2f} ({STOP_POINTS:.1f} points)")
    print(f"  Min Profit:    ${MIN_PROFIT_DOLLARS:.2f}")
    print(f"  Commission:    ${COMMISSION_RT:.2f} per RT")
    print(f"  Timeout:       {TIMEOUT_SECONDS}s")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # Step 1: Connect to Tastytrade LIVE
    # -------------------------------------------------------------------------
    print("\n[1/6] Connecting to Tastytrade LIVE account...")
    api = TastytradeAPI(paper_trading=False)

    if not api.session_token:
        print("FAILED: Could not authenticate. Check TASTYTRADE_USER/PASSWORD in .env")
        return 1

    print(f"  OK - Authenticated, account: {api.account_number}")

    # -------------------------------------------------------------------------
    # Step 2: Get account balance
    # -------------------------------------------------------------------------
    print("\n[2/6] Checking account balance...")
    balance = api.get_account_balance()
    if balance:
        print(f"  Cash:         ${balance.get('cash', 0):,.2f}")
        print(f"  Equity:       ${balance.get('equity', 0):,.2f}")
        print(f"  Buying Power: ${balance.get('buying_power', 0):,.2f}")
    else:
        print("  WARNING: Could not fetch balance (continuing anyway)")

    # -------------------------------------------------------------------------
    # Step 3: Check existing positions
    # -------------------------------------------------------------------------
    print("\n[3/6] Checking existing positions...")
    positions = api.get_positions()
    if positions:
        print(f"  WARNING: You have {len(positions)} open position(s):")
        for p in positions:
            sym = p.get('symbol', p.get('instrument', {}).get('symbol', '?'))
            qty = p.get('quantity', '?')
            print(f"    {sym}: {qty} contracts")
        print("  The test will proceed but be aware of existing positions.")
    else:
        print("  OK - No open positions")

    # -------------------------------------------------------------------------
    # Step 4: Resolve MNQ futures symbol
    # -------------------------------------------------------------------------
    print("\n[4/6] Resolving active MNQ contract...")
    futures_symbol = api.get_futures_symbol("MNQ")
    if futures_symbol:
        print(f"  Active contract: {futures_symbol}")
    else:
        futures_symbol = "/MNQH6"
        print(f"  Could not resolve, using fallback: {futures_symbol}")

    # -------------------------------------------------------------------------
    # Step 5: Get current price
    # -------------------------------------------------------------------------
    print("\n[5/6] Getting current MNQ price...")
    entry_price = api.get_current_price(futures_symbol)
    if not entry_price or entry_price < 10000:
        # Try with just MNQ
        entry_price = api.get_current_price("MNQ")

    if not entry_price or entry_price < 10000:
        print("  FAILED: Could not get a valid MNQ price.")
        print("  Market may be closed. MNQ trades Sun-Fri, check trading hours.")
        return 1

    stop_price = entry_price - STOP_POINTS
    target_profit_points = (MIN_PROFIT_DOLLARS + COMMISSION_RT) / POINT_VALUE

    print(f"  Current Price: ${entry_price:,.2f}")
    print(f"  Stop Loss:     ${stop_price:,.2f} (-{STOP_POINTS:.1f} pts = -${MAX_LOSS_DOLLARS:.2f})")
    print(f"  Profit Target: ${entry_price + target_profit_points:,.2f} (+{target_profit_points:.1f} pts = +${MIN_PROFIT_DOLLARS:.2f} after comm)")

    # -------------------------------------------------------------------------
    # Step 6: Execute test trade
    # -------------------------------------------------------------------------
    if dry_run:
        print("\n[6/6] DRY RUN - Skipping order execution")
        print("=" * 70)
        print("  Everything looks good! To execute for real, run:")
        print("  python test_live_order.py --exec")
        print("=" * 70)
        return 0

    print(f"\n[6/6] PLACING LIVE ORDER: BUY {CONTRACTS} MNQ @ MARKET")
    print("-" * 70)

    # Place entry order
    order_result = api.place_market_order("MNQ", "BUY", CONTRACTS)

    if not order_result:
        print("  FAILED: Order was not placed. Check API logs above.")
        return 1

    order_id = order_result.get('id', 'unknown')
    fill_price = entry_price  # Approximate, actual fill may differ
    print(f"  ORDER PLACED - ID: {order_id}")
    print(f"  Approximate fill: ${fill_price:,.2f}")

    # Recalculate stops based on approximate fill
    stop_price = fill_price - STOP_POINTS
    tp_price = fill_price + target_profit_points

    print(f"\n  MONITORING POSITION...")
    print(f"  Stop Loss:     ${stop_price:,.2f}")
    print(f"  Profit Target: ${tp_price:,.2f}")
    print(f"  Timeout:       {TIMEOUT_SECONDS}s")
    print("-" * 70)

    # Monitor loop
    start_time = time.time()
    last_print = 0

    while True:
        elapsed = time.time() - start_time

        # Safety timeout
        if elapsed >= TIMEOUT_SECONDS:
            print(f"\n  TIMEOUT ({TIMEOUT_SECONDS}s) - Closing position at market")
            close_result = api.close_position("MNQ")
            current = api.get_current_price(futures_symbol) or api.get_current_price("MNQ")
            if current:
                pnl = (current - fill_price) * CONTRACTS * POINT_VALUE - COMMISSION_RT
                print(f"  Exit price: ~${current:,.2f}, P&L: ~${pnl:,.2f}")
            print("  RESULT: TIMEOUT EXIT")
            break

        # Get current price
        current = api.get_current_price(futures_symbol) or api.get_current_price("MNQ")
        if not current or current < 10000:
            time.sleep(POLL_INTERVAL)
            continue

        pnl_points = current - fill_price
        pnl_dollars = pnl_points * CONTRACTS * POINT_VALUE - COMMISSION_RT
        unrealized = pnl_points * CONTRACTS * POINT_VALUE  # Before commission

        # Print status every 5 seconds
        if elapsed - last_print >= 5:
            arrow = "+" if pnl_points >= 0 else ""
            print(f"  [{elapsed:5.0f}s] Price: ${current:,.2f} | {arrow}{pnl_points:+.2f} pts | Unrealized: ${unrealized:+,.2f} | Net: ${pnl_dollars:+,.2f}")
            last_print = elapsed

        # Check stop loss
        if current <= stop_price:
            print(f"\n  STOP LOSS HIT at ${current:,.2f}")
            close_result = api.close_position("MNQ")
            pnl = (current - fill_price) * CONTRACTS * POINT_VALUE - COMMISSION_RT
            print(f"  P&L: ${pnl:,.2f}")
            print("  RESULT: STOP LOSS")
            break

        # Check profit target
        if pnl_dollars >= MIN_PROFIT_DOLLARS:
            print(f"\n  PROFIT TARGET HIT at ${current:,.2f}")
            close_result = api.close_position("MNQ")
            pnl = (current - fill_price) * CONTRACTS * POINT_VALUE - COMMISSION_RT
            print(f"  P&L: ${pnl:,.2f} (after ${COMMISSION_RT:.2f} commission)")
            print("  RESULT: PROFIT EXIT")
            break

        time.sleep(POLL_INTERVAL)

    # Final summary
    print("\n" + "=" * 70)
    print("  TEST COMPLETE")
    print("=" * 70)

    # Verify no open positions remain
    time.sleep(2)
    remaining = api.get_positions()
    if remaining:
        mnq_pos = [p for p in remaining if 'MNQ' in str(p.get('symbol', '')) or 'MNQ' in str(p.get('instrument', {}).get('symbol', ''))]
        if mnq_pos:
            print("  WARNING: MNQ position may still be open. Check your account!")
        else:
            print("  OK - No MNQ positions remaining")
    else:
        print("  OK - All positions closed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
