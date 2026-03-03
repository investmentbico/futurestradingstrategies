#!/usr/bin/env python3
"""
Contract Size Testing Script
Tests multiple contract sizes for the ES 2min strategy
"""

import subprocess
import sys
import os
import pandas as pd
from datetime import datetime

def run_backtest_with_contracts(contracts):
    """Run backtest with specified contract size"""
    print(f"\n🔄 Testing {contracts} contracts...")

    # Read the current backtest file
    with open('comprehensive_backtest.py', 'r') as f:
        content = f.read()

    # Replace the CONTRACTS value
    import re
    content = re.sub(r'    "CONTRACTS": \d+,', f'    "CONTRACTS": {contracts},', content)

    # Scale MAX_LOSS_TRADE proportionally (1200 for 2 contracts = 600 per contract)
    max_loss_per_contract = 600
    new_max_loss = max_loss_per_contract * contracts
    content = re.sub(r'    "MAX_LOSS_TRADE": \d+\.\d+,', f'    "MAX_LOSS_TRADE": {new_max_loss}.0,', content)

    # Write back the modified file
    with open('comprehensive_backtest.py', 'w') as f:
        f.write(content)

    # Run the backtest
    try:
        result = subprocess.run([sys.executable, 'comprehensive_backtest.py'],
                              capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            print(f"✅ {contracts} contracts backtest completed successfully")
            return True
        else:
            print(f"❌ {contracts} contracts backtest failed")
            print("Error:", result.stderr[:500])  # Show first 500 chars of error
            return False
    except subprocess.TimeoutExpired:
        print(f"⏰ {contracts} contracts backtest timed out")
        return False
    except Exception as e:
        print(f"💥 {contracts} contracts backtest error: {e}")
        return False

def main():
    """Test multiple contract sizes"""
    contract_sizes = [3, 4, 5, 7, 8, 10]

    print("🚀 Starting Contract Size Testing")
    print("=" * 50)

    results = []

    for contracts in contract_sizes:
        success = run_backtest_with_contracts(contracts)
        if success:
            # Try to read the latest results
            try:
                # Find the most recent results file
                results_dir = 'results'
                if os.path.exists(results_dir):
                    files = [f for f in os.listdir(results_dir) if f.startswith('backtest_summary_')]
                    if files:
                        latest_file = max(files)
                        df = pd.read_csv(f'{results_dir}/{latest_file}')
                        es_2min = df[(df['Symbol'] == 'ES') & (df['Timeframe'] == '2min')]
                        if not es_2min.empty:
                            result = {
                                'contracts': contracts,
                                'trades': es_2min['Trades'].iloc[0],
                                'win_rate': es_2min['Win_Rate'].iloc[0],
                                'total_pnl': es_2min['Total_PnL'].iloc[0],
                                'profit_factor': es_2min['Profit_Factor'].iloc[0],
                                'max_drawdown': es_2min['Max_Drawdown'].iloc[0],
                                'avg_trade': es_2min['Avg_Trade'].iloc[0],
                                'sharpe_ratio': es_2min['Sharpe_Ratio'].iloc[0]
                            }
                            results.append(result)
                            print(f"📊 {contracts} contracts: {result['trades']} trades, ${result['total_pnl']:,.0f} P&L")
            except Exception as e:
                print(f"⚠️  Could not read results for {contracts} contracts: {e}")

    # Restore original values - use regex replacement
    import re
    with open('comprehensive_backtest.py', 'r') as f:
        content = f.read()

    # Restore CONTRACTS
    content = re.sub(r'    "CONTRACTS": \d+,', '    "CONTRACTS": 2,', content)

    # Restore MAX_LOSS_TRADE
    content = re.sub(r'    "MAX_LOSS_TRADE": \d+\.\d+,', '    "MAX_LOSS_TRADE": 1200.0,', content)

    with open('comprehensive_backtest.py', 'w') as f:
        f.write(content)

    # Print summary
    print("\n" + "=" * 60)
    print("📈 CONTRACT SIZE TEST RESULTS")
    print("=" * 60)

    if results:
        print("<10")
        print("-" * 60)
        for result in sorted(results, key=lambda x: x['total_pnl'], reverse=True):
            print("<10")
    else:
        print("❌ No results collected")

if __name__ == "__main__":
    main()