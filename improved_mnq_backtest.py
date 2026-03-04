
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from mnq_backtest import MNQBacktest, STRATEGY_PARAMS, RISK_PARAMS


class ImprovedMNQBacktest(MNQBacktest):
    """Improved MNQ backtest with filters based on trade analysis"""

    def __init__(self, symbol="MNQ", days=90, improvements=None):
        super().__init__(symbol, days)
        self.improvements = improvements or []
        self.consecutive_losses = 0
        self.trading_paused = False

    def check_entry_signals(self, row):
        """Enhanced entry signals with additional filters"""
        # Basic signal check
        signal = super().check_entry_signals(row)
        if not signal:
            return None

        # Apply improvements
        for improvement in self.improvements:
            if "Time Filter" in improvement:
                # Extract hours from improvement string
                import re
                hours_match = re.search(r'hours \[([0-9,\s]+)\]', improvement)
                if hours_match:
                    allowed_hours = [int(h.strip()) for h in hours_match.group(1).split(',')]
                    current_hour = row.name.hour if hasattr(row.name, 'hour') else pd.Timestamp(row.name).hour
                    if current_hour not in allowed_hours:
                        return None

            if "ATR Filter" in improvement:
                min_atr = float(improvement.split()[-1])
                if row['atr'] < min_atr:
                    return None

            if "Stochastic Filter" in improvement:
                # Require more extreme stochastic readings
                if signal == 'long' and row['stoch_d'] > 20:
                    return None
                if signal == 'short' and row['stoch_d'] < 80:
                    return None

        # Check if trading is paused due to consecutive losses
        if self.trading_paused:
            return None

        return signal

    def run_backtest(self):
        """Run backtest with consecutive loss tracking"""
        result = super().run_backtest()

        # Add consecutive loss tracking
        trades = result['trades']
        self.consecutive_losses = 0

        for trade in trades:
            if trade['pnl'] < 0:
                self.consecutive_losses += 1
                if self.consecutive_losses >= 3:
                    self.trading_paused = True
            else:
                self.consecutive_losses = 0
                self.trading_paused = False

        return result
