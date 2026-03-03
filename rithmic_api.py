import os
import subprocess
import threading
import queue
import time
import random

class RithmicAPI:
    def __init__(self, is_demo: bool = True):
        self.is_demo = is_demo
        self.process = None
        self.output_queue = queue.Queue()
        self.mock_price = 15000.0  # Starting mock MNQ price
        self.start_process()

    def start_process(self):
        # For demo mode or when wrapper isn't available, use mock data
        if self.is_demo or not os.path.exists('./rithmic_wrapper'):
            print("Using mock data (demo mode or wrapper not available)")
            self.process = None
            return

        env = os.environ.copy()
        # Set environment variables for Rithmic Test
        env['RITHMIC_APP_NAME'] = 'R|API+'
        env['RITHMIC_APP_VERSION'] = '17.9.0.0'
        env['RITHMIC_USER'] = os.getenv('RITHMIC_USER', 'testuser')
        env['RITHMIC_PASSWORD'] = os.getenv('RITHMIC_PASSWORD', 'testpass')

        try:
            self.process = subprocess.Popen(
                ['./rithmic_wrapper'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env
            )

            # Start thread to read output
            threading.Thread(target=self.read_output, daemon=True).start()

            # Wait a moment to see if it starts successfully
            time.sleep(1)
            if self.process.poll() is not None:
                print("Warning: rithmic_wrapper failed to start. Using mock data.")
                self.process = None

        except Exception as e:
            print(f"Warning: Failed to start rithmic_wrapper: {e}. Using mock data.")
            self.process = None

    def read_output(self):
        while self.process and self.process.poll() is None:
            line = self.process.stdout.readline()
            if not line:
                break
            self.output_queue.put(line.strip())

    def send_command(self, command):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(command + '\n')
                self.process.stdin.flush()
                return True
            except:
                return False
        return False

    def get_current_price(self, symbol='MNQ'):
        """Get current price for symbol"""
        if self.process:
            self.send_command(f'quote {symbol}')
            try:
                response = self.output_queue.get(timeout=2)
                # Parse response like "BEST: MNQ BID:15000.5 ASK:15000.75"
                if 'BEST:' in response and 'BID:' in response and 'ASK:' in response:
                    parts = response.split()
                    bid_idx = parts.index('BID:')
                    ask_idx = parts.index('ASK:')
                    bid = float(parts[bid_idx + 1].rstrip(','))
                    ask = float(parts[ask_idx + 1])
                    return (bid + ask) / 2  # Return midpoint
            except queue.Empty:
                pass

        # Fall back to mock data
        # Simulate some price movement
        self.mock_price += random.uniform(-5, 5)
        self.mock_price = max(14000, min(16000, self.mock_price))  # Keep in reasonable range
        return self.mock_price

    def get_market_data(self, symbol):
        """Get market data for symbol - returns dict with last_price"""
        price = self.get_current_price(symbol)
        return {'last_price': price}

    def get_positions(self):
        """Get current positions"""
        # Mock empty positions for now
        return []

    def get_account_info(self):
        """Get account information"""
        # Mock account info
        return {
            'cash_balance': 100000.0,
            'daily_pnl': 0.0
        }

    def place_order(self, order_data):
        """Place an order"""
        # Mock order placement
        return {'order_id': f'mock_order_{random.randint(1000,9999)}', 'status': 'filled'}

    def cancel_all_orders(self, symbol=None):
        """Cancel all orders"""
        return {'cancelled': 0}

    def get_futures_instruments(self):
        """Get futures instruments"""
        # Mock MNQ instrument
        return [{
            'symbol': 'MNQ',
            'tick_size': 0.25,
            'point_value': 20.0,
            'multiplier': 1
        }]

    def login(self):
        # Assume login happens on start
        pass

    def close(self):
        if self.process:
            try:
                self.send_command('quit')
                self.process.wait(timeout=5)
            except:
                self.process.terminate()
                self.process.wait()