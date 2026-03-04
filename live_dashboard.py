#!/usr/bin/env python3
"""
Live MNQ Trading Dashboard
Displays real-time trading statistics and P&L
"""

import os
import time
import json
from datetime import datetime
from typing import Dict, List, Optional
import curses
import sys

class LiveTradingDashboard:
    def __init__(self, log_file: str = "trading_session.log"):
        self.log_file = log_file
        self.last_position = 0
        self.trades = []
        self.current_pnl = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.starting_equity = 20000.0  # From account balance
        self.current_equity = self.starting_equity

    def parse_log_line(self, line: str) -> Optional[Dict]:
        """Parse a log line for trading information"""
        try:
            # Parse LONG ENTRY
            if "LONG ENTRY:" in line:
                parts = line.split("LONG ENTRY:")
                if len(parts) > 1:
                    trade_info = parts[1].strip()
                    # Extract quantity and price
                    qty_part = trade_info.split()[0]
                    quantity = int(qty_part)
                    price_part = trade_info.split("$")[1].split()[0]
                    entry_price = float(price_part)

                    return {
                        'type': 'entry',
                        'direction': 'long',
                        'quantity': quantity,
                        'entry_price': entry_price,
                        'timestamp': datetime.now()
                    }

            # Parse SHORT ENTRY
            elif "SHORT ENTRY:" in line:
                parts = line.split("SHORT ENTRY:")
                if len(parts) > 1:
                    trade_info = parts[1].strip()
                    qty_part = trade_info.split()[0]
                    quantity = int(qty_part)
                    price_part = trade_info.split("$")[1].split()[0]
                    entry_price = float(price_part)

                    return {
                        'type': 'entry',
                        'direction': 'short',
                        'quantity': quantity,
                        'entry_price': entry_price,
                        'timestamp': datetime.now()
                    }

            # Parse LONG EXIT
            elif "LONG EXIT:" in line and "P&L:" in line:
                parts = line.split("LONG EXIT:")
                if len(parts) > 1:
                    exit_info = parts[1].strip()
                    price_part = exit_info.split("$")[1].split()[0]
                    exit_price = float(price_part)
                    pnl_part = exit_info.split("P&L: $")[1]
                    pnl = float(pnl_part)

                    return {
                        'type': 'exit',
                        'direction': 'long',
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'timestamp': datetime.now()
                    }

            # Parse SHORT EXIT
            elif "SHORT EXIT:" in line and "P&L:" in line:
                parts = line.split("SHORT EXIT:")
                if len(parts) > 1:
                    exit_info = parts[1].strip()
                    price_part = exit_info.split("$")[1].split()[0]
                    exit_price = float(price_part)
                    pnl_part = exit_info.split("P&L: $")[1]
                    pnl = float(pnl_part)

                    return {
                        'type': 'exit',
                        'direction': 'short',
                        'exit_price': exit_price,
                        'pnl': pnl,
                        'timestamp': datetime.now()
                    }

        except Exception as e:
            pass

        return None

    def update_trades(self):
        """Update trades from log file"""
        if not os.path.exists(self.log_file):
            return

        try:
            with open(self.log_file, 'r') as f:
                lines = f.readlines()

            # Process new lines
            for line in lines:
                trade_data = self.parse_log_line(line)
                if trade_data:
                    if trade_data['type'] == 'entry':
                        # New trade entry
                        self.trades.append({
                            'direction': trade_data['direction'],
                            'quantity': trade_data['quantity'],
                            'entry_price': trade_data['entry_price'],
                            'entry_time': trade_data['timestamp'],
                            'exit_price': None,
                            'exit_time': None,
                            'pnl': 0.0,
                            'status': 'open'
                        })
                        self.total_trades += 1

                    elif trade_data['type'] == 'exit':
                        # Close the last matching trade
                        for trade in reversed(self.trades):
                            if (trade['direction'] == trade_data['direction'] and
                                trade['status'] == 'open'):
                                trade['exit_price'] = trade_data['exit_price']
                                trade['exit_time'] = trade_data['timestamp']
                                trade['pnl'] = trade_data['pnl']
                                trade['status'] = 'closed'

                                if trade['pnl'] > 0:
                                    self.winning_trades += 1
                                break

            # Calculate current P&L
            self.current_pnl = sum(trade['pnl'] for trade in self.trades if trade['status'] == 'closed')
            self.current_equity = self.starting_equity + self.current_pnl

        except Exception as e:
            pass

    def display_dashboard(self, stdscr):
        """Display the live trading dashboard"""
        curses.curs_set(0)
        stdscr.nodelay(1)

        while True:
            try:
                self.update_trades()

                stdscr.clear()

                # Title
                stdscr.addstr(0, 0, "🎯 MNQ LIVE TRADING DASHBOARD", curses.A_BOLD)
                stdscr.addstr(1, 0, "=" * 50, curses.A_DIM)

                # Current Status
                stdscr.addstr(3, 0, "💰 ACCOUNT STATUS", curses.A_BOLD)
                stdscr.addstr(4, 0, f"Starting Equity: ${self.starting_equity:,.2f}")
                stdscr.addstr(5, 0, f"Current Equity:  ${self.current_equity:,.2f}", curses.A_BOLD)
                stdscr.addstr(6, 0, f"Total P&L:      ${self.current_pnl:+,.2f}", curses.A_BOLD)
                pnl_color = curses.COLOR_GREEN if self.current_pnl >= 0 else curses.COLOR_RED
                curses.init_pair(1, pnl_color, curses.COLOR_BLACK)
                stdscr.addstr(6, 18, f"${self.current_pnl:+,.2f}", curses.color_pair(1) | curses.A_BOLD)

                # Performance Stats
                stdscr.addstr(8, 0, "📊 PERFORMANCE STATS", curses.A_BOLD)
                win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0
                stdscr.addstr(9, 0, f"Total Trades: {self.total_trades}")
                stdscr.addstr(10, 0, f"Winning Trades: {self.winning_trades}")
                stdscr.addstr(11, 0, f"Win Rate: {win_rate:.1f}%")

                # Recent Trades
                stdscr.addstr(13, 0, "📈 RECENT TRADES", curses.A_BOLD)
                stdscr.addstr(14, 0, "-" * 50, curses.A_DIM)

                # Show last 10 trades
                y_pos = 15
                for i, trade in enumerate(reversed(self.trades[-10:])):
                    if y_pos >= curses.LINES - 2:
                        break

                    direction = trade['direction'].upper()
                    status = trade['status'].upper()

                    if trade['status'] == 'closed':
                        pnl_str = f"${trade['pnl']:+.2f}"
                        pnl_color = curses.COLOR_GREEN if trade['pnl'] >= 0 else curses.COLOR_RED
                        curses.init_pair(2, pnl_color, curses.COLOR_BLACK)
                        stdscr.addstr(y_pos, 0, f"{direction} {trade['quantity']}x @ ${trade['entry_price']:.2f} → ${trade['exit_price']:.2f}")
                        stdscr.addstr(y_pos, 45, pnl_str, curses.color_pair(2))
                    else:
                        stdscr.addstr(y_pos, 0, f"{direction} {trade['quantity']}x @ ${trade['entry_price']:.2f} [OPEN]")

                    y_pos += 1

                # Footer
                stdscr.addstr(curses.LINES - 2, 0, "-" * 50, curses.A_DIM)
                stdscr.addstr(curses.LINES - 1, 0, "Press 'q' to quit | Auto-refreshing every 5 seconds", curses.A_DIM)

                stdscr.refresh()

                # Check for quit key
                try:
                    key = stdscr.getch()
                    if key == ord('q'):
                        break
                except:
                    pass

                time.sleep(5)  # Update every 5 seconds

            except KeyboardInterrupt:
                break
            except Exception as e:
                stdscr.addstr(curses.LINES - 1, 0, f"Error: {str(e)}")
                stdscr.refresh()
                time.sleep(5)

def main():
    dashboard = LiveTradingDashboard()
    curses.wrapper(dashboard.display_dashboard)

if __name__ == "__main__":
    main()