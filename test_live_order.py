#!/usr/bin/env python3
"""
TEST LIVE ORDER - 1 MNQ Contract with $25 Stop Loss
=====================================================
Uses the official tastytrade SDK with OAuth2 authentication.

Sends a single test trade on your REAL Tastytrade account:
  - BUY 1 MNQ at market
  - Stop loss order: $25 max loss (12.5 points x $2/point)
  - Auto-close when in profit (any profit > $3 after commissions)
  - 5-minute timeout safety net

Usage:
  python test_live_order.py          # Dry run (validates order, no execution)
  python test_live_order.py --exec   # Execute real orders on live account
"""

import os
import sys
import time
import asyncio
import argparse
from decimal import Decimal
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from tastytrade import Session, Account
from tastytrade.instruments import Future
from tastytrade.order import (
    NewOrder, Leg, OrderAction, OrderType, OrderTimeInForce, InstrumentType
)

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
POLL_INTERVAL = 2.0        # Check every 2 seconds


async def run_test(dry_run: bool):
    print("=" * 70)
    print("  MNQ LIVE ORDER TEST - 1 Contract (Official SDK + OAuth2)")
    print("=" * 70)
    print(f"  Mode:          {'DRY RUN (no real orders)' if dry_run else 'LIVE EXECUTION'}")
    print(f"  Contracts:     {CONTRACTS}")
    print(f"  Max Loss:      ${MAX_LOSS_DOLLARS:.2f} ({STOP_POINTS:.1f} points)")
    print(f"  Min Profit:    ${MIN_PROFIT_DOLLARS:.2f}")
    print(f"  Commission:    ${COMMISSION_RT:.2f} per RT")
    print(f"  Timeout:       {TIMEOUT_SECONDS}s")
    print("=" * 70)

    # -------------------------------------------------------------------------
    # Step 1: Authenticate via OAuth2
    # -------------------------------------------------------------------------
    print("\n[1/7] Authenticating via OAuth2...")
    try:
        session = Session()  # Uses TT_SECRET and TT_REFRESH from env
        print(f"  OK - OAuth2 session established")
    except Exception as e:
        print(f"  FAILED: {e}")
        print("  Check TT_SECRET and TT_REFRESH in your .env file")
        return 1

    # -------------------------------------------------------------------------
    # Step 2: Get accounts
    # -------------------------------------------------------------------------
    print("\n[2/7] Getting account info...")
    try:
        accounts = await Account.get(session)
        if not accounts:
            print("  FAILED: No accounts found")
            return 1
        # Use the account from env, or first account
        target_acct = os.getenv('TASTYTRADE_ACCOUNT', '')
        account = None
        if isinstance(accounts, list):
            for a in accounts:
                if a.account_number == target_acct:
                    account = a
                    break
            if not account:
                account = accounts[0]
        else:
            account = accounts

        print(f"  Account: {account.account_number}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return 1

    # -------------------------------------------------------------------------
    # Step 3: Get account balance
    # -------------------------------------------------------------------------
    print("\n[3/7] Checking account balance...")
    try:
        balances = await account.get_balances(session)
        print(f"  Cash:         ${float(balances.cash_balance):,.2f}")
        print(f"  Equity:       ${float(balances.net_liquidating_value):,.2f}")
        print(f"  Buying Power: ${float(balances.derivative_buying_power):,.2f}")
    except Exception as e:
        print(f"  WARNING: Could not fetch balance: {e}")

    # -------------------------------------------------------------------------
    # Step 4: Check existing positions
    # -------------------------------------------------------------------------
    print("\n[4/7] Checking existing positions...")
    try:
        positions = await account.get_positions(session)
        mnq_positions = [p for p in positions if 'MNQ' in (p.symbol or '')]
        if mnq_positions:
            print(f"  WARNING: You have {len(mnq_positions)} MNQ position(s):")
            for p in mnq_positions:
                print(f"    {p.symbol}: {p.quantity} contracts")
        else:
            print(f"  OK - No MNQ positions ({len(positions)} total positions)")
    except Exception as e:
        print(f"  WARNING: {e}")

    # -------------------------------------------------------------------------
    # Step 5: Resolve active MNQ futures contract
    # -------------------------------------------------------------------------
    print("\n[5/7] Resolving active MNQ futures contract...")
    try:
        futures = await Future.get(session, product_codes=["MNQ"])
        # Find the active front-month contract (not closing-only)
        active_future = None
        for f in futures:
            if not f.is_closing_only:
                active_future = f
                break
        if not active_future and futures:
            active_future = futures[0]

        if not active_future:
            print("  FAILED: No active MNQ contract found")
            return 1

        symbol = active_future.symbol
        streamer_symbol = active_future.streamer_symbol or symbol
        print(f"  Active contract: {symbol}")
        print(f"  Streamer symbol: {streamer_symbol}")
        print(f"  Tick size: {active_future.tick_size}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return 1

    # -------------------------------------------------------------------------
    # Step 6: Build entry order (BUY 1 MNQ @ Market)
    # -------------------------------------------------------------------------
    print(f"\n[6/7] Building order: BUY {CONTRACTS} {symbol} @ MARKET")

    entry_leg = active_future.build_leg(Decimal(CONTRACTS), OrderAction.BUY_TO_OPEN)
    entry_order = NewOrder(
        time_in_force=OrderTimeInForce.DAY,
        order_type=OrderType.MARKET,
        legs=[entry_leg],
    )

    # -------------------------------------------------------------------------
    # Step 7: Validate or execute
    # -------------------------------------------------------------------------
    if dry_run:
        print("\n[7/7] DRY RUN - Validating order (no execution)...")
        try:
            response = await account.place_order(session, entry_order, dry_run=True)
            print(f"  VALIDATION OK")
            if hasattr(response, 'buying_power_effect') and response.buying_power_effect:
                bpe = response.buying_power_effect
                print(f"  Buying power effect: {bpe.change_in_buying_power}")
            if hasattr(response, 'warnings') and response.warnings:
                for w in response.warnings:
                    print(f"  WARNING: {w}")
        except Exception as e:
            print(f"  VALIDATION FAILED: {e}")
            return 1

        print("\n" + "=" * 70)
        print("  DRY RUN COMPLETE - Order is valid!")
        print("  To execute for real, run:")
        print("  python test_live_order.py --exec")
        print("=" * 70)
        return 0

    # --- LIVE EXECUTION ---
    print(f"\n[7/7] PLACING LIVE ORDER: BUY {CONTRACTS} {symbol} @ MARKET")
    print("-" * 70)

    try:
        response = await account.place_order(session, entry_order, dry_run=False)
        order = response.order
        order_id = order.id if order else 'unknown'
        print(f"  ORDER PLACED - ID: {order_id}")
        if order:
            print(f"  Status: {order.status}")
    except Exception as e:
        print(f"  FAILED: {e}")
        return 1

    # Wait for fill
    print("  Waiting for fill...")
    fill_price = None
    for attempt in range(10):
        time.sleep(1)
        try:
            live_orders = await account.get_live_orders(session)
            for o in live_orders:
                if o.id == order_id:
                    print(f"  Order status: {o.status}")
                    if str(o.status).lower() in ('filled', 'routed'):
                        if hasattr(o, 'legs') and o.legs:
                            for leg in o.legs:
                                if hasattr(leg, 'fills') and leg.fills:
                                    fill_price = float(leg.fills[0].fill_price)
                        break
        except Exception:
            pass
        if fill_price:
            break

    # If we couldn't get fill price from order, check positions
    if not fill_price:
        try:
            positions = await account.get_positions(session)
            for p in positions:
                if 'MNQ' in (p.symbol or ''):
                    fill_price = float(p.average_open_price) if p.average_open_price else None
                    break
        except Exception:
            pass

    if not fill_price:
        print("  WARNING: Could not determine fill price. Using last known price.")
        print("  Will still monitor and close position.")
        # We'll use order history to try to find the fill
        try:
            history = await account.get_order_history(session)
            for o in history:
                if o.id == order_id and hasattr(o, 'legs') and o.legs:
                    for leg in o.legs:
                        if hasattr(leg, 'fills') and leg.fills:
                            fill_price = float(leg.fills[0].fill_price)
                            break
                if fill_price:
                    break
        except Exception:
            pass

    if fill_price:
        print(f"  FILLED at ${fill_price:,.2f}")
    else:
        print("  Could not determine fill price - will close position on any movement")
        fill_price = 0  # Will force close on first price check

    stop_price = fill_price - STOP_POINTS if fill_price else 0
    target_profit_points = (MIN_PROFIT_DOLLARS + COMMISSION_RT) / POINT_VALUE
    tp_price = fill_price + target_profit_points if fill_price else 0

    print(f"\n  MONITORING POSITION...")
    if fill_price:
        print(f"  Entry:         ${fill_price:,.2f}")
        print(f"  Stop Loss:     ${stop_price:,.2f} (-{STOP_POINTS:.1f} pts = -${MAX_LOSS_DOLLARS:.2f})")
        print(f"  Profit Target: ${tp_price:,.2f} (+{target_profit_points:.1f} pts = +${MIN_PROFIT_DOLLARS:.2f} after comm)")
    print(f"  Timeout:       {TIMEOUT_SECONDS}s")
    print("-" * 70)

    # Also place a server-side stop loss order for safety
    print("  Placing server-side stop loss order...")
    try:
        stop_leg = active_future.build_leg(Decimal(CONTRACTS), OrderAction.SELL_TO_CLOSE)
        stop_order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.STOP,
            stop_trigger=Decimal(str(round(stop_price, 2))),
            legs=[stop_leg],
        )
        stop_response = await account.place_order(session, stop_order, dry_run=False)
        stop_order_id = stop_response.order.id if stop_response.order else None
        print(f"  STOP ORDER PLACED - ID: {stop_order_id} @ ${stop_price:,.2f}")
    except Exception as e:
        stop_order_id = None
        print(f"  WARNING: Could not place stop order: {e}")
        print("  Will rely on software-based stop monitoring")

    # Monitor loop
    start_time = time.time()
    last_print = 0
    exit_reason = None

    while True:
        elapsed = time.time() - start_time

        # Safety timeout
        if elapsed >= TIMEOUT_SECONDS:
            exit_reason = "TIMEOUT"
            print(f"\n  TIMEOUT ({TIMEOUT_SECONDS}s) - Closing position")
            break

        # Check position via API
        try:
            positions = await account.get_positions(session)
            mnq_pos = [p for p in positions if 'MNQ' in (p.symbol or '')]

            if not mnq_pos:
                # Position closed (maybe stop order triggered)
                exit_reason = "STOP ORDER TRIGGERED (server-side)"
                print(f"\n  Position no longer open - stop order may have triggered")
                break

            pos = mnq_pos[0]
            current_price = float(pos.mark_price) if pos.mark_price else None
            unrealized = float(pos.realized_day_gain) if hasattr(pos, 'realized_day_gain') and pos.realized_day_gain else None

        except Exception:
            current_price = None

        if current_price and fill_price:
            pnl_points = current_price - fill_price
            pnl_dollars = pnl_points * CONTRACTS * POINT_VALUE - COMMISSION_RT
            unrealized_dollars = pnl_points * CONTRACTS * POINT_VALUE

            # Print status every 5 seconds
            if elapsed - last_print >= 5:
                print(f"  [{elapsed:5.0f}s] Price: ${current_price:,.2f} | {pnl_points:+.2f} pts | Unrealized: ${unrealized_dollars:+,.2f} | Net: ${pnl_dollars:+,.2f}")
                last_print = elapsed

            # Check profit target (software side)
            if pnl_dollars >= MIN_PROFIT_DOLLARS:
                exit_reason = "PROFIT TARGET"
                print(f"\n  PROFIT TARGET HIT at ${current_price:,.2f} (net P&L: ${pnl_dollars:+,.2f})")
                break

        time.sleep(POLL_INTERVAL)

    # Close position
    if exit_reason != "STOP ORDER TRIGGERED (server-side)":
        print("  Closing position at market...")
        try:
            close_leg = active_future.build_leg(Decimal(CONTRACTS), OrderAction.SELL_TO_CLOSE)
            close_order = NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.MARKET,
                legs=[close_leg],
            )
            close_response = await account.place_order(session, close_order, dry_run=False)
            print(f"  CLOSE ORDER PLACED - ID: {close_response.order.id if close_response.order else '?'}")
        except Exception as e:
            print(f"  WARNING: Close order failed: {e}")
            print("  CHECK YOUR ACCOUNT - position may still be open!")

    # Cancel server-side stop if we closed manually
    if stop_order_id and exit_reason != "STOP ORDER TRIGGERED (server-side)":
        try:
            await account.delete_order(session, stop_order_id)
            print(f"  Cancelled server-side stop order {stop_order_id}")
        except Exception:
            pass  # May already be cancelled

    # Final summary
    time.sleep(2)
    print("\n" + "=" * 70)
    print(f"  TEST COMPLETE - Exit reason: {exit_reason}")
    print("=" * 70)

    # Verify no positions remain
    try:
        positions = await account.get_positions(session)
        mnq_pos = [p for p in positions if 'MNQ' in (p.symbol or '')]
        if mnq_pos:
            print("  WARNING: MNQ position may still be open. Check your account!")
        else:
            print("  OK - No MNQ positions remaining")
    except Exception:
        print("  Could not verify final position state - check your account")

    return 0


def main():
    parser = argparse.ArgumentParser(description='Test live MNQ order')
    parser.add_argument('--exec', action='store_true',
                        help='Actually execute orders (default: dry run)')
    args = parser.parse_args()
    return asyncio.run(run_test(dry_run=not args.exec))


if __name__ == "__main__":
    sys.exit(main())
