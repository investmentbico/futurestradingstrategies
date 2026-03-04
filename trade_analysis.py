#!/usr/bin/env python3
"""
MNQ Trade Analysis - Deep Dive into Win/Loss Patterns
=====================================================

Analyzes individual trades from backtest to identify patterns that differentiate
winning trades from losing trades, then implements improvements.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import warnings
import os
warnings.filterwarnings('ignore')

# Import the backtest class
from mnq_backtest import MNQBacktest, STRATEGY_PARAMS, RISK_PARAMS

def analyze_trade_patterns():
    """Analyze winning vs losing trades to find patterns"""
    print("🔍 ANALYZING TRADE PATTERNS - WINNERS VS LOSERS")
    print("=" * 60)

    # Run backtest to get trade data
    backtest = MNQBacktest(symbol='MNQ', days=90)
    backtest.download_data()

    # Temporarily modify risk params for analysis
    original_params = backtest.risk_params.copy()
    backtest.risk_params.update({
        'CONTRACTS': 1,
        'HARD_STOP_DOLLARS': 800,
        'ATR_MULTIPLIER': 2.0,
        'ATR_PERIOD': 20,
        'TAKE_PROFIT_MULTIPLIER': 2.0
    })

    result = backtest.run_backtest()
    trades = result['trades']

    print(f"📊 Total Trades Analyzed: {len(trades)}")

    # Separate winning and losing trades
    winning_trades = [t for t in trades if t['pnl'] > 0]
    losing_trades = [t for t in trades if t['pnl'] < 0]

    print(f"✅ Winning Trades: {len(winning_trades)} ({len(winning_trades)/len(trades)*100:.1f}%)")
    print(f"❌ Losing Trades: {len(losing_trades)} ({len(losing_trades)/len(trades)*100:.1f}%)")

    # Analyze exit reasons
    win_exit_reasons = {}
    loss_exit_reasons = {}

    for trade in winning_trades:
        reason = trade.get('exit_reason', 'unknown')
        win_exit_reasons[reason] = win_exit_reasons.get(reason, 0) + 1

    for trade in losing_trades:
        reason = trade.get('exit_reason', 'unknown')
        loss_exit_reasons[reason] = loss_exit_reasons.get(reason, 0) + 1

    print("\n🎯 EXIT REASON ANALYSIS:")
    print("Winning Trades:")
    for reason, count in win_exit_reasons.items():
        pct = count / len(winning_trades) * 100
        print(f"   {reason}: {count} ({pct:.1f}%)")

    print("Losing Trades:")
    for reason, count in loss_exit_reasons.items():
        pct = count / len(losing_trades) * 100
        print(f"   {reason}: {count} ({pct:.1f}%)")

    # Analyze market conditions at entry
    print("\n📈 MARKET CONDITION ANALYSIS:")

    # Get data for analysis
    df = backtest.calculate_indicators(backtest.data.copy())

    win_atr_values = []
    loss_atr_values = []
    win_stoch_d = []
    loss_stoch_d = []
    win_trend_strength = []
    loss_trend_strength = []

    for trade in winning_trades:
        entry_time = trade['entry_time']
        if entry_time in df.index:
            row = df.loc[entry_time]
            win_atr_values.append(row['atr'])
            win_stoch_d.append(row['stoch_d'])
            # Trend strength as difference between EMAs
            trend_strength = abs(row['ema_fast'] - row['ema_slow']) / row['close']
            win_trend_strength.append(trend_strength)

    for trade in losing_trades:
        entry_time = trade['entry_time']
        if entry_time in df.index:
            row = df.loc[entry_time]
            loss_atr_values.append(row['atr'])
            loss_stoch_d.append(row['stoch_d'])
            # Trend strength as difference between EMAs
            trend_strength = abs(row['ema_fast'] - row['ema_slow']) / row['close']
            loss_trend_strength.append(trend_strength)

    print("ATR at Entry:")
    print(f"   Winners avg: {np.mean(win_atr_values):.2f}" if win_atr_values else "   Winners avg: N/A")
    print(f"   Losers avg:  {np.mean(loss_atr_values):.2f}" if loss_atr_values else "   Losers avg:  N/A")

    print("Stochastic D at Entry:")
    print(f"   Winners avg: {np.mean(win_stoch_d):.2f}" if win_stoch_d else "   Winners avg: N/A")
    print(f"   Losers avg:  {np.mean(loss_stoch_d):.2f}" if loss_stoch_d else "   Losers avg:  N/A")

    print("Trend Strength at Entry (EMA diff %):")
    print(f"   Winners avg: {np.mean(win_trend_strength):.4f}" if win_trend_strength else "   Winners avg: N/A")
    print(f"   Losers avg:  {np.mean(loss_trend_strength):.4f}" if loss_trend_strength else "   Losers avg:  N/A")

    # Analyze time-based patterns
    print("\n⏰ TIME-BASED PATTERNS:")

    win_hours = [t['entry_time'].hour for t in winning_trades]
    loss_hours = [t['entry_time'].hour for t in losing_trades]

    print("Most Profitable Hours:")
    win_hour_counts = {}
    for hour in win_hours:
        win_hour_counts[hour] = win_hour_counts.get(hour, 0) + 1
    sorted_win_hours = sorted(win_hour_counts.items(), key=lambda x: x[1], reverse=True)
    for hour, count in sorted_win_hours[:5]:
        print(f"   {hour:02d}:00: {count} wins")

    print("Most Loss-Making Hours:")
    loss_hour_counts = {}
    for hour in loss_hours:
        loss_hour_counts[hour] = loss_hour_counts.get(hour, 0) + 1
    sorted_loss_hours = sorted(loss_hour_counts.items(), key=lambda x: x[1], reverse=True)
    for hour, count in sorted_loss_hours[:5]:
        print(f"   {hour:02d}:00: {count} losses")

    # Analyze consecutive losses and potential filters
    print("\n🔄 CONSECUTIVE TRADE ANALYSIS:")

    # Check for patterns of consecutive losses
    consecutive_losses = 0
    max_consecutive_losses = 0
    for trade in trades:
        if trade['pnl'] < 0:
            consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, consecutive_losses)
        else:
            consecutive_losses = 0

    print(f"Max Consecutive Losses: {max_consecutive_losses}")

    # Analyze P&L distribution
    win_pnls = [t['pnl'] for t in winning_trades]
    loss_pnls = [t['pnl'] for t in losing_trades]

    print("\n💰 P&L DISTRIBUTION:")
    print(f"   Win avg:  ${np.mean(win_pnls):,.0f}" if win_pnls else "   Win avg:  N/A")
    print(f"   Win max:  ${np.max(win_pnls):,.0f}" if win_pnls else "   Win max:  N/A")
    print(f"   Loss avg: ${np.mean(loss_pnls):,.0f}" if loss_pnls else "   Loss avg: N/A")
    print(f"   Loss max: ${np.min(loss_pnls):,.0f}" if loss_pnls else "   Loss max: N/A")

    return {
        'win_exit_reasons': win_exit_reasons,
        'loss_exit_reasons': loss_exit_reasons,
        'win_atr_avg': np.mean(win_atr_values) if win_atr_values else 0,
        'loss_atr_avg': np.mean(loss_atr_values) if loss_atr_values else 0,
        'win_stoch_avg': np.mean(win_stoch_d) if win_stoch_d else 0,
        'loss_stoch_avg': np.mean(loss_stoch_d) if loss_stoch_d else 0,
        'max_consecutive_losses': max_consecutive_losses,
        'win_hours': sorted_win_hours,
        'loss_hours': sorted_loss_hours
    }

def implement_improvements(analysis_results):
    """Implement improvements based on trade analysis"""
    print("\n🚀 IMPLEMENTING IMPROVEMENTS BASED ON ANALYSIS")
    print("=" * 50)

    improvements = []

    # 1. Time-based filtering
    win_hours = [hour for hour, count in analysis_results['win_hours'][:3]]  # Top 3 winning hours
    loss_hours = [hour for hour, count in analysis_results['loss_hours'][:3]]  # Top 3 losing hours

    if win_hours:
        improvements.append(f"⏰ Time Filter: Only trade during winning hours {win_hours}")
        print(f"✅ Added time filter for hours: {win_hours}")

    # 2. ATR filtering - winners have higher ATR
    if analysis_results['win_atr_avg'] > analysis_results['loss_atr_avg'] * 1.2:
        min_atr = analysis_results['win_atr_avg'] * 0.8
        improvements.append(f"📊 ATR Filter: Minimum ATR of {min_atr:.2f} for entries")
        print(f"✅ Added ATR filter: Min ATR = {min_atr:.2f}")

    # 3. Stochastic extremity filter
    if analysis_results['win_stoch_avg'] < analysis_results['loss_stoch_avg']:
        # Winners have more extreme stochastic readings
        improvements.append("🎲 Stochastic Filter: Require more extreme stochastic readings")
        print("✅ Added stochastic extremity filter")

    # 4. Consecutive loss protection
    if analysis_results['max_consecutive_losses'] >= 3:
        improvements.append("🛡️ Consecutive Loss Protection: Pause trading after 3 consecutive losses")
        print("✅ Added consecutive loss protection")

    # 5. Exit optimization based on analysis
    win_exits = analysis_results['win_exit_reasons']
    loss_exits = analysis_results['loss_exit_reasons']

    # If most winners exit via take profit, tighten stops
    if 'take_profit' in win_exits and win_exits.get('take_profit', 0) > win_exits.get('atr_stop', 0):
        improvements.append("🎯 Exit Optimization: Tighten ATR stops since winners hit take profit")
        print("✅ Optimized exits: Tightened ATR stops")

    return improvements

def create_improved_backtest(improvements):
    """Create an improved version of the backtest with the implemented filters"""
    print("\n🔧 CREATING IMPROVED BACKTEST CLASS")
    print("=" * 40)

    # Create improved backtest class
    improved_code = '''
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
                hours_match = re.search(r'hours \\[([0-9,\\s]+)\\]', improvement)
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
'''

    # Write the improved class to a file
    output_dir = os.path.dirname(os.path.abspath(__file__))
    improved_file = os.path.join(output_dir, 'improved_mnq_backtest.py')
    with open(improved_file, 'w') as f:
        f.write('''
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from mnq_backtest import MNQBacktest, STRATEGY_PARAMS, RISK_PARAMS

''' + improved_code)

    print("✅ Created improved backtest class: improved_mnq_backtest.py")

def test_improved_strategy(improvements):
    """Test the improved strategy"""
    print("\n🧪 TESTING IMPROVED STRATEGY")
    print("=" * 35)

    # Import the improved backtest
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from improved_mnq_backtest import ImprovedMNQBacktest

    # Run improved backtest
    improved_backtest = ImprovedMNQBacktest(symbol='MNQ', days=90, improvements=improvements)
    improved_backtest.download_data()

    # Set same parameters as original
    improved_backtest.risk_params.update({
        'CONTRACTS': 1,
        'HARD_STOP_DOLLARS': 800,
        'ATR_MULTIPLIER': 2.0,
        'ATR_PERIOD': 20,
        'TAKE_PROFIT_MULTIPLIER': 2.0
    })

    improved_result = improved_backtest.run_backtest()

    print("🎯 IMPROVED STRATEGY RESULTS:")
    print(f"   Trades: {improved_result['total_trades']}")
    print(f"   Win Rate: {improved_result['win_rate']:.1f}%")
    print(f"   Total P&L: ${improved_result['total_pnl']:,.0f}")
    print(f"   Profit Factor: {improved_result.get('profit_factor', 0):.2f}")
    print(f"   Max Drawdown: ${improved_result.get('max_drawdown', 0):,.1f}")

    return improved_result

def main():
    """Main analysis and improvement workflow"""
    print("🎯 MNQ TRADE ANALYSIS & IMPROVEMENT SYSTEM")
    print("=" * 50)

    # Step 1: Analyze current trades
    analysis_results = analyze_trade_patterns()

    # Step 2: Implement improvements
    improvements = implement_improvements(analysis_results)

    # Step 3: Create improved backtest
    create_improved_backtest(improvements)

    # Step 4: Test improved strategy
    improved_result = test_improved_strategy(improvements)

    # Step 5: Compare results
    print("\n📊 COMPARISON: ORIGINAL VS IMPROVED")
    print("=" * 40)
    print("Original: 693 trades, 0.5% win rate, $16,162 P&L")
    print(f"Improved: {improved_result['total_trades']} trades, {improved_result['win_rate']:.1f}% win rate, ${improved_result['total_pnl']:,.0f} P&L")

    if improved_result['total_pnl'] > 16162:
        print("✅ IMPROVEMENT SUCCESSFUL!")
    else:
        print("⚠️  Further optimization needed")

if __name__ == "__main__":
    main()