#!/usr/bin/env python3
"""
Rithmic Trading Bot - Live Dashboard
======================================
Real-time terminal dashboard that monitors the trading bot.
Reads bot_state.json and log files to display live status.

Usage:
  python rithmic_dashboard.py              # Auto-refresh every 2s
  python rithmic_dashboard.py --log        # Also tail the log file
  python rithmic_dashboard.py --web        # Start web dashboard on port 8081
"""

import os
import sys
import json
import time
import curses
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading


STATE_FILE = Path(__file__).parent / "bot_state.json"
LOG_DIR = Path(__file__).parent / "logs"


def load_state():
    """Load current bot state from JSON file."""
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def format_pnl(val):
    """Format P&L with sign."""
    if val >= 0:
        return f"+${val:.2f}"
    return f"-${abs(val):.2f}"


def run_curses_dashboard(stdscr):
    """Main curses dashboard loop."""
    curses.curs_set(0)
    stdscr.timeout(2000)  # 2s refresh

    # Colors
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_RED, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)

    GREEN = curses.color_pair(1)
    RED = curses.color_pair(2)
    YELLOW = curses.color_pair(3)
    CYAN = curses.color_pair(4)
    HEADER = curses.color_pair(5) | curses.A_BOLD

    while True:
        try:
            stdscr.clear()
            h, w = stdscr.getmaxyx()
            state = load_state()

            # ── Header ───────────────────────────────────────────
            title = " RITHMIC MNQ TRADING DASHBOARD "
            stdscr.addstr(0, 0, title.center(w), HEADER)

            if not state:
                stdscr.addstr(2, 2, "Waiting for bot to start...", YELLOW)
                stdscr.addstr(3, 2, "Run: python rithmic_trading_bot.py", curses.A_DIM)
                stdscr.addstr(5, 2, f"State file: {STATE_FILE}", curses.A_DIM)
                stdscr.refresh()
                key = stdscr.getch()
                if key == ord('q'):
                    break
                continue

            row = 2

            # ── Connection Status ────────────────────────────────
            mode = state.get('mode', '?')
            connected = state.get('connected', False)
            symbol = state.get('symbol', '?')
            ts = state.get('ts', '')

            mode_color = RED if mode == 'LIVE' else YELLOW
            conn_color = GREEN if connected else RED

            stdscr.addstr(row, 2, f"Mode: ", curses.A_BOLD)
            stdscr.addstr(row, 8, mode, mode_color | curses.A_BOLD)
            stdscr.addstr(row, 8 + len(mode) + 2, f"| Connected: ", curses.A_BOLD)
            stdscr.addstr(row, 24 + len(mode), "YES" if connected else "NO", conn_color)
            stdscr.addstr(row, 35 + len(mode), f"| Symbol: {symbol}")
            row += 1

            if ts:
                try:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    age = (datetime.now(timezone.utc) - dt).total_seconds()
                    age_str = f"{age:.0f}s ago" if age < 60 else f"{age/60:.1f}m ago"
                    stdscr.addstr(row, 2, f"Last update: {ts[:19]} ({age_str})", curses.A_DIM)
                except Exception:
                    pass
            row += 2

            # ── Price & Position ─────────────────────────────────
            price = state.get('price', 0)
            bid = state.get('bid', 0)
            ask = state.get('ask', 0)
            position = state.get('position', 0)

            stdscr.addstr(row, 2, "MARKET", curses.A_BOLD | curses.A_UNDERLINE)
            row += 1
            stdscr.addstr(row, 2, f"  Price: ")
            stdscr.addstr(row, 11, f"{price:.2f}" if price else "---", CYAN | curses.A_BOLD)
            if bid and ask:
                spread = ask - bid
                stdscr.addstr(row, 25, f"Bid: {bid:.2f}  Ask: {ask:.2f}  Spread: {spread:.2f}")
            row += 2

            # ── Position Details ─────────────────────────────────
            stdscr.addstr(row, 2, "POSITION", curses.A_BOLD | curses.A_UNDERLINE)
            row += 1

            if position != 0:
                direction = "LONG" if position > 0 else "SHORT"
                dir_color = GREEN if position > 0 else RED
                entry = state.get('entry', 0)
                sl = state.get('sl', 0)
                tp = state.get('tp', 0)
                trail = state.get('trail', 0)
                unrealized = state.get('unrealized_pnl', 0)
                pnl_color = GREEN if unrealized >= 0 else RED

                stdscr.addstr(row, 2, f"  Status: ")
                stdscr.addstr(row, 12, f"{direction} {abs(position)}x", dir_color | curses.A_BOLD)
                stdscr.addstr(row, 25, f"Entry: {entry:.2f}")
                row += 1
                stdscr.addstr(row, 2, f"  SL: {sl:.2f}  |  TP: {tp:.2f}")
                if trail > 0:
                    stdscr.addstr(row, 35, f"|  Trail: {trail:.2f}")
                row += 1
                stdscr.addstr(row, 2, f"  Unrealized P&L: ")
                stdscr.addstr(row, 20, format_pnl(unrealized), pnl_color | curses.A_BOLD)
            else:
                stdscr.addstr(row, 2, f"  Status: ")
                stdscr.addstr(row, 12, "FLAT", YELLOW)
            row += 2

            # ── Session Stats ────────────────────────────────────
            stdscr.addstr(row, 2, "SESSION STATS", curses.A_BOLD | curses.A_UNDERLINE)
            row += 1

            total = state.get('total_trades', 0)
            wins = state.get('wins', 0)
            total_pnl = state.get('total_pnl', 0)
            daily_pnl = state.get('daily_pnl', 0)
            daily_trades = state.get('daily_trades', 0)
            bars = state.get('bars', 0)
            wr = (wins / total * 100) if total > 0 else 0

            pnl_color = GREEN if total_pnl >= 0 else RED
            daily_color = GREEN if daily_pnl >= 0 else RED

            stdscr.addstr(row, 2, f"  Trades: {total}  |  Wins: {wins}  |  Win Rate: {wr:.1f}%")
            row += 1
            stdscr.addstr(row, 2, f"  Total P&L: ")
            stdscr.addstr(row, 15, format_pnl(total_pnl), pnl_color | curses.A_BOLD)
            stdscr.addstr(row, 30, f"  Daily P&L: ")
            stdscr.addstr(row, 43, format_pnl(daily_pnl), daily_color)
            stdscr.addstr(row, 58, f"  Daily Trades: {daily_trades}")
            row += 1
            stdscr.addstr(row, 2, f"  Bars loaded: {bars}", curses.A_DIM)
            row += 2

            # ── Recent Trades ────────────────────────────────────
            trades = state.get('trades', [])
            if trades:
                stdscr.addstr(row, 2, "RECENT TRADES", curses.A_BOLD | curses.A_UNDERLINE)
                row += 1

                # Header
                header = f"  {'Time':<20} {'Dir':<6} {'Qty':<5} {'Entry':>10} {'Exit':>10} {'P&L':>12}"
                stdscr.addstr(row, 2, header, curses.A_DIM)
                row += 1

                # Show last N trades that fit
                max_trades = min(len(trades), h - row - 3)
                for trade in trades[-max_trades:]:
                    t_time = trade.get('time', '')[:19]
                    t_dir = trade.get('dir', '?')
                    t_qty = trade.get('qty', 0)
                    t_entry = trade.get('entry', 0)
                    t_exit = trade.get('exit', 0)
                    t_pnl = trade.get('pnl', 0)
                    t_color = GREEN if t_pnl >= 0 else RED

                    line = f"  {t_time:<20} {t_dir:<6} {t_qty:<5} {t_entry:>10.2f} {t_exit:>10.2f} "
                    stdscr.addstr(row, 2, line)
                    stdscr.addstr(row, 2 + len(line), format_pnl(t_pnl), t_color)
                    row += 1
                    if row >= h - 2:
                        break

            # ── Footer ───────────────────────────────────────────
            footer = " q=Quit | Refreshing every 2s | Webhook: POST localhost:8080 "
            if row < h - 1:
                stdscr.addstr(h - 1, 0, footer.center(w)[:w-1], HEADER)

            stdscr.refresh()

            key = stdscr.getch()
            if key == ord('q'):
                break

        except curses.error:
            pass
        except KeyboardInterrupt:
            break


# ── Web Dashboard ────────────────────────────────────────────────────
WEB_HTML = """<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>Rithmic Trading Dashboard</title>
<meta http-equiv="refresh" content="3">
<style>
  body { font-family: 'Courier New', monospace; background: #1a1a2e; color: #eee; padding: 20px; }
  h1 { color: #00d4ff; text-align: center; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; max-width: 900px; margin: 0 auto; }
  .card { background: #16213e; border-radius: 8px; padding: 15px; border: 1px solid #0f3460; }
  .card h3 { color: #00d4ff; margin: 0 0 10px 0; font-size: 14px; text-transform: uppercase; }
  .value { font-size: 24px; font-weight: bold; }
  .green { color: #00ff88; }
  .red { color: #ff4444; }
  .yellow { color: #ffaa00; }
  .dim { color: #666; font-size: 12px; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th { text-align: left; color: #00d4ff; border-bottom: 1px solid #0f3460; padding: 5px; font-size: 12px; }
  td { padding: 5px; font-size: 13px; border-bottom: 1px solid #0a0a1a; }
  .full { grid-column: 1 / -1; }
</style>
</head><body>
<h1>RITHMIC MNQ TRADING DASHBOARD</h1>
<div id="app">Loading...</div>
<script>
async function update() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    if (!s.symbol) { document.getElementById('app').innerHTML='<p style="text-align:center;color:#ffaa00">Waiting for bot...</p>'; return; }
    const pos = s.position;
    const dir = pos > 0 ? 'LONG' : pos < 0 ? 'SHORT' : 'FLAT';
    const dirCls = pos > 0 ? 'green' : pos < 0 ? 'red' : 'yellow';
    const pnlCls = (v) => v >= 0 ? 'green' : 'red';
    const fmtPnl = (v) => (v>=0?'+':'-') + '$' + Math.abs(v).toFixed(2);
    let trades = (s.trades||[]).slice(-10).reverse().map(t =>
      `<tr><td>${(t.time||'').slice(0,19)}</td><td class="${t.pnl>=0?'green':'red'}">${t.dir}</td>` +
      `<td>${t.qty}</td><td>${t.entry?.toFixed(2)}</td><td>${t.exit?.toFixed(2)}</td>` +
      `<td class="${t.pnl>=0?'green':'red'}">${fmtPnl(t.pnl)}</td></tr>`
    ).join('');
    const wr = s.total_trades > 0 ? (s.wins/s.total_trades*100).toFixed(1) : '0.0';
    document.getElementById('app').innerHTML = `
      <div class="grid">
        <div class="card"><h3>Mode</h3><div class="value ${s.mode=='LIVE'?'red':'yellow'}">${s.mode}</div>
          <div class="dim">${s.connected?'Connected':'Disconnected'} | ${s.symbol}</div></div>
        <div class="card"><h3>Price</h3><div class="value" style="color:#00d4ff">${s.price?.toFixed(2)||'---'}</div>
          <div class="dim">Bid: ${s.bid?.toFixed(2)||'-'} | Ask: ${s.ask?.toFixed(2)||'-'}</div></div>
        <div class="card"><h3>Position</h3><div class="value ${dirCls}">${dir} ${pos!=0?Math.abs(pos)+'x':''}</div>
          ${pos!=0?`<div class="dim">Entry: ${s.entry?.toFixed(2)} | SL: ${s.sl?.toFixed(2)} | TP: ${s.tp?.toFixed(2)}</div>
          <div class="${pnlCls(s.unrealized_pnl)}">Unrealized: ${fmtPnl(s.unrealized_pnl||0)}</div>`:''}</div>
        <div class="card"><h3>Session P&L</h3><div class="value ${pnlCls(s.total_pnl)}">${fmtPnl(s.total_pnl||0)}</div>
          <div class="dim">Daily: <span class="${pnlCls(s.daily_pnl)}">${fmtPnl(s.daily_pnl||0)}</span> |
          Trades: ${s.total_trades} | Win: ${wr}%</div></div>
        <div class="card full"><h3>Recent Trades</h3>
          <table><tr><th>Time</th><th>Dir</th><th>Qty</th><th>Entry</th><th>Exit</th><th>P&L</th></tr>${trades||'<tr><td colspan=6 class="dim">No trades yet</td></tr>'}</table></div>
      </div>
      <p class="dim" style="text-align:center;margin-top:15px">Updated: ${s.ts?.slice(0,19)||'-'} | Bars: ${s.bars} | Auto-refresh 3s</p>`;
  } catch(e) { document.getElementById('app').innerHTML='<p class="red">Error loading state</p>'; }
}
update();
setInterval(update, 3000);
</script></body></html>"""


def start_web_dashboard(port=8081):
    """Start web-based dashboard server."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/api/state':
                state = STATE_FILE.read_text() if STATE_FILE.exists() else '{}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(state.encode())
            else:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html')
                self.end_headers()
                self.wfile.write(WEB_HTML.encode())

        def log_message(self, fmt, *args):
            pass

    srv = HTTPServer(('0.0.0.0', port), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    print(f"Web dashboard: http://localhost:{port}")
    return srv


# ── Main ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Rithmic Trading Dashboard")
    parser.add_argument('--web', action='store_true', help='Start web dashboard')
    parser.add_argument('--web-port', type=int, default=8081)
    parser.add_argument('--log', action='store_true', help='Also tail log file')
    args = parser.parse_args()

    if args.web:
        start_web_dashboard(args.web_port)
        print("Web dashboard running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    if args.log:
        # Find latest log file
        logs = sorted(LOG_DIR.glob("rithmic_bot_*.log"))
        if logs:
            log_path = logs[-1]
            print(f"Tailing: {log_path}")
            os.system(f"tail -f {log_path}")
        else:
            print("No log files found")
        return

    # Default: curses terminal dashboard
    try:
        curses.wrapper(run_curses_dashboard)
    except Exception as e:
        print(f"Dashboard error: {e}")
        print("Try: python rithmic_dashboard.py --web")


if __name__ == "__main__":
    main()
