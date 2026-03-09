#!/usr/bin/env python3
"""
TradersPost Webhook Client
============================
Sends trading signals to TradersPost.io which routes them to your
Apex Trader Funding account via Tradovate.

Usage:
  from traderspost_webhook import TradersPostClient
  tp = TradersPostClient()
  tp.send_long(quantity=3)
  tp.send_short(quantity=3)
  tp.send_exit()

Requires .env:
  TRADERSPOST_WEBHOOK_URL=https://webhooks.traderspost.io/trading/webhook/YOUR_UUID/YOUR_PASSWORD
  TRADERSPOST_TICKER=MNQ
"""

import os
import json
import time
import logging
import ssl
import urllib.request
import urllib.error

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Use the .env next to this script, not cwd
    load_dotenv(Path(__file__).resolve().parent / '.env')
except ImportError:
    pass

logger = logging.getLogger("traderspost")

# MNQ futures month codes: H=Mar, M=Jun, U=Sep, Z=Dec
MONTH_CODES = {3: 'H', 6: 'M', 9: 'U', 12: 'Z'}


def get_front_month_ticker(symbol="MNQ"):
    """Return the base symbol for TradersPost webhook.
    TradersPost resolves the front-month contract automatically —
    just send 'MNQ', NOT 'MNQH6' or 'MNQM2026'.
    Confirmed working: ticker='MNQ' on 2026-03-09."""
    return symbol


class TradersPostClient:
    """Send trading signals to TradersPost.io webhook."""

    def __init__(self, webhook_url=None, ticker=None):
        self.webhook_url = webhook_url or os.getenv('TRADERSPOST_WEBHOOK_URL', '')
        self.ticker = ticker or os.getenv('TRADERSPOST_TICKER', '') or get_front_month_ticker()
        self.log_file = Path(__file__).parent / "logs" / "traderspost_signals.log"
        self.log_file.parent.mkdir(exist_ok=True)

        if not self.webhook_url:
            logger.warning("TRADERSPOST_WEBHOOK_URL not set - signals will be logged but not sent")

        logger.info(f"TradersPost client: ticker={self.ticker}")

    def _send(self, payload):
        """Send webhook payload to TradersPost. Returns (success, response_data)."""
        payload['ticker'] = self.ticker
        payload['time'] = datetime.now(timezone.utc).isoformat()

        # Log every signal
        log_entry = {
            'sent_at': payload['time'],
            'payload': payload,
            'url_set': bool(self.webhook_url),
        }

        if not self.webhook_url:
            log_entry['status'] = 'DRY_RUN'
            logger.info(f"[DRY RUN] Would send: {json.dumps(payload)}")
            self._log(log_entry)
            return True, {'dry_run': True}

        # Send with retry
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    self.webhook_url, data=data, headers=headers, method='POST'
                )
                with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
                    body = resp.read().decode('utf-8')
                    result = json.loads(body) if body else {}
                    success = result.get('success', resp.status == 200)

                    log_entry['status'] = 'OK' if success else 'REJECTED'
                    log_entry['response'] = result
                    log_entry['http_status'] = resp.status
                    self._log(log_entry)

                    if success:
                        logger.info(f"Signal sent: {payload['action']} | {self.ticker} | Response: {result}")
                    else:
                        logger.warning(f"Signal rejected: {result}")
                    return success, result

            except urllib.error.HTTPError as e:
                body = e.read().decode('utf-8', errors='replace')
                logger.error(f"HTTP {e.code}: {body} (attempt {attempt + 1}/3)")
                log_entry['status'] = f'HTTP_{e.code}'
                log_entry['error'] = body
                if attempt < 2:
                    time.sleep(2 ** attempt)

            except Exception as e:
                logger.error(f"Send error: {e} (attempt {attempt + 1}/3)")
                log_entry['status'] = 'ERROR'
                log_entry['error'] = str(e)
                if attempt < 2:
                    time.sleep(2 ** attempt)

        self._log(log_entry)
        return False, {'error': 'All retries failed'}

    def _log(self, entry):
        """Append signal to log file."""
        try:
            with open(self.log_file, 'a') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass

    # ── Public API ───────────────────────────────────────────────────

    def send_long(self, quantity=None, signal_price=None,
                  stop_loss_amount=None, take_profit_amount=None):
        """Enter long position."""
        payload = {
            'action': 'buy',
            'sentiment': 'bullish',
        }
        if quantity:
            payload['quantity'] = quantity
        if signal_price:
            payload['signalPrice'] = round(signal_price, 2)
        if stop_loss_amount and take_profit_amount:
            payload['stopLoss'] = {'type': 'stop', 'amount': round(stop_loss_amount, 2)}
            payload['takeProfit'] = {'amount': round(take_profit_amount, 2)}
        return self._send(payload)

    def send_short(self, quantity=None, signal_price=None,
                   stop_loss_amount=None, take_profit_amount=None):
        """Enter short position."""
        payload = {
            'action': 'sell',
            'sentiment': 'bearish',
        }
        if quantity:
            payload['quantity'] = quantity
        if signal_price:
            payload['signalPrice'] = round(signal_price, 2)
        if stop_loss_amount and take_profit_amount:
            payload['stopLoss'] = {'type': 'stop', 'amount': round(stop_loss_amount, 2)}
            payload['takeProfit'] = {'amount': round(take_profit_amount, 2)}
        return self._send(payload)

    def send_exit(self, cancel_orders=True):
        """Exit/flatten current position."""
        payload = {
            'action': 'exit',
        }
        if cancel_orders:
            payload['cancel'] = True
        return self._send(payload)

    def send_cancel(self):
        """Cancel all open orders."""
        return self._send({'action': 'cancel'})

    def send_raw(self, payload):
        """Send arbitrary payload (for advanced use)."""
        return self._send(payload)

    def test(self):
        """Test connectivity by sending a dry-run signal (action=exit with 0 qty).
        Note: This will NOT place any trade if you have no position."""
        logger.info("Testing TradersPost webhook connectivity...")
        if not self.webhook_url:
            logger.warning("No webhook URL set. Set TRADERSPOST_WEBHOOK_URL in .env")
            return False, {'error': 'no_url'}

        # Send an exit signal - safe because it does nothing if no position open
        payload = {
            'action': 'exit',
            'extras': {'test': True, 'source': 'connectivity_test'},
        }
        success, result = self._send(payload)
        if success:
            logger.info(f"Webhook connected! Response: {result}")
        else:
            logger.error(f"Webhook test failed: {result}")
        return success, result


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    p = argparse.ArgumentParser(description="TradersPost Webhook Client")
    p.add_argument('action', choices=['test', 'long', 'short', 'exit', 'cancel'],
                   help='Signal to send')
    p.add_argument('--qty', type=int, default=None, help='Number of contracts')
    p.add_argument('--price', type=float, default=None, help='Signal price')
    p.add_argument('--sl', type=float, default=None, help='Stop loss amount (points)')
    p.add_argument('--tp', type=float, default=None, help='Take profit amount (points)')
    p.add_argument('--ticker', default=None, help='Override ticker (e.g. MNQM2026)')
    p.add_argument('--url', default=None, help='Override webhook URL')
    args = p.parse_args()

    tp = TradersPostClient(webhook_url=args.url, ticker=args.ticker)

    if args.action == 'test':
        success, result = tp.test()
    elif args.action == 'long':
        success, result = tp.send_long(args.qty, args.price, args.sl, args.tp)
    elif args.action == 'short':
        success, result = tp.send_short(args.qty, args.price, args.sl, args.tp)
    elif args.action == 'exit':
        success, result = tp.send_exit()
    elif args.action == 'cancel':
        success, result = tp.send_cancel()

    print(f"\n{'OK' if success else 'FAILED'}: {json.dumps(result, indent=2)}")
