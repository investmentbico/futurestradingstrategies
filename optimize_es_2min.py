#!/usr/bin/env python3
"""
Strategy Optimizer for ES 2min - Parameter Tuning
===============================================

Optimizes the Stoch-D strategy parameters specifically for ES 2min timeframe
to maximize P&L while maintaining robust performance.

Tests different combinations of:
- Stoch-D periods (fastk_period, slowk_period, slowd_period)
- Overbought/Oversold levels
- EMA filter periods
- Entry/exit thresholds

Usage:
    python optimize_es_2min.py
"""

import os
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

# Add current directory to path for imports
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from comprehensive_backtest import run_single_backtest

# =============================================================================
# OPTIMIZATION PARAMETERS
# =============================================================================

# Parameter ranges to test
PARAM_RANGES = {
    'stoch_periods': [
        (14, 3, 3),   # Standard (14, 3, 3)
        (21, 5, 5),   # Slower (21, 5, 5)
        (10, 3, 3),   # Faster (10, 3, 3)
        (14, 5, 5),   # (14, 5, 5)
        (21, 3, 3),   # (21, 3, 3)
    ],
    'stoch_levels': [
        (80, 20),     # Standard (80, 20)
        (75, 25),     # Wider (75, 25)
        (85, 15),     # Narrower (85, 15)
        (70, 30),     # Very wide (70, 30)
    ],
    'ema_periods': [
        20,           # Standard 20
        50,           # Longer 50
        10,           # Shorter 10
        30,           # Medium 30
    ],
    'thresholds': [
        0.5,          # Standard 0.5
        1.0,          # Higher 1.0
        0.3,          # Lower 0.3
        0.7,          # Medium-high 0.7
    ]
}

# =============================================================================
# OPTIMIZATION FUNCTION
# =============================================================================

def optimize_strategy():
    """Run parameter optimization for ES 2min"""

    print("🚀 STARTING ES 2MIN STRATEGY OPTIMIZATION")
    print("=" * 50)

    # Load ES 2min data
    data_file = 'data/es_2min.csv'
    if not os.path.exists(data_file):
        print(f"❌ Data file not found: {data_file}")
        return

    df = pd.read_csv(data_file)
    print(f"📊 Loaded {len(df)} bars from {data_file}")

    results = []

    total_combinations = (len(PARAM_RANGES['stoch_periods']) *
                         len(PARAM_RANGES['stoch_levels']) *
                         len(PARAM_RANGES['ema_periods']) *
                         len(PARAM_RANGES['thresholds']))

    print(f"🔄 Testing {total_combinations} parameter combinations...")

    combination_count = 0

    for stoch_k, stoch_d, stoch_smooth in PARAM_RANGES['stoch_periods']:
        for ob_level, os_level in PARAM_RANGES['stoch_levels']:
            for ema_period in PARAM_RANGES['ema_periods']:
                for threshold in PARAM_RANGES['thresholds']:

                    combination_count += 1

                    # Create strategy parameters
                    strategy_params = {
                        'stoch_k_period': stoch_k,
                        'stoch_d_period': stoch_d,
                        'stoch_smooth': stoch_smooth,
                        'overbought': ob_level,
                        'oversold': os_level,
                        'ema_period': ema_period,
                        'threshold': threshold
                    }

                    try:
                        # Run backtest
                        result = run_single_backtest(
                            df=df,
                            symbol='ES',
                            timeframe='2min',
                            strategy_params=strategy_params,
                            contracts=2,
                            starting_equity=300000
                        )

                        if result and result['trades'] > 0:
                            result_row = {
                                'stoch_k': stoch_k,
                                'stoch_d': stoch_d,
                                'stoch_smooth': stoch_smooth,
                                'overbought': ob_level,
                                'oversold': os_level,
                                'ema_period': ema_period,
                                'threshold': threshold,
                                'trades': result['trades'],
                                'win_rate': result['win_rate'],
                                'total_pnl': result['total_pnl'],
                                'profit_factor': result['profit_factor'],
                                'max_drawdown': result['max_drawdown'],
                                'avg_trade': result['avg_trade'],
                                'sharpe_ratio': result['sharpe_ratio']
                            }
                            results.append(result_row)

                            # Progress update
                            if combination_count % 10 == 0:
                                print(f"🔄 Tested {combination_count}/{total_combinations} combinations...")

                    except Exception as e:
                        print(f"❌ Error testing combination {combination_count}: {e}")
                        continue

    if not results:
        print("❌ No valid results found")
        return

    # Convert to DataFrame and sort by P&L
    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values('total_pnl', ascending=False)

    # Save results
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    results_file = f'results/es_2min_optimization_{timestamp}.csv'
    results_df.to_csv(results_file, index=False)

    print(f"\n✅ Optimization complete! Results saved to {results_file}")
    print(f"📊 Tested {len(results_df)} valid parameter combinations")

    # Display top 10 results
    print("\n🏆 TOP 10 PARAMETER COMBINATIONS:")
    print("-" * 80)
    top_10 = results_df.head(10)

    for i, row in top_10.iterrows():
        print("2d"
              f"Trades: {row['trades']}, "
              f"Win: {row['win_rate']:.1f}%, "
              f"P&L: ${row['total_pnl']:,.0f}, "
              f"PF: {row['profit_factor']:.2f}, "
              f"DD: {row['max_drawdown']:.1f}%")

    # Best parameters
    best = results_df.iloc[0]
    print("\n🎯 BEST PARAMETERS:")
    print(f"   Stoch-D: ({best['stoch_k']}, {best['stoch_d']}, {best['stoch_smooth']})")
    print(f"   Levels: OB={best['overbought']}, OS={best['oversold']}")
    print(f"   EMA Period: {best['ema_period']}")
    print(f"   Threshold: {best['threshold']}")
    print(f"   Performance: ${best['total_pnl']:,.0f} P&L, {best['win_rate']:.1f}% win rate")

    return results_df

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    optimize_strategy()