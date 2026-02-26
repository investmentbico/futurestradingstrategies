import pandas as pd
import numpy as np

# Load data
df = pd.read_csv('data/mnq_2min.csv')
df['timestamp'] = pd.to_datetime(df['time'], unit='s')
df = df[['timestamp', 'open', 'high', 'low', 'close']]

# Fixed inputs
atr_len = 14
take_profit_rr = 2.0
use_tp = True
use_trailing = True
trail_atr_mult = 1.0
breakeven_after_profit = 100.0
min_equity_for_trade = 100.0
tick_value = 0.25
daily_max_loss = 3000
session_start_min = 420
session_end_min = 945
min_bar_range_pct = 0.0
time_filter_enable = False
start_hour = 7
end_hour = 20

# Calculate fixed stuff
df['minute'] = df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute
df['in_session'] = (df['minute'] >= session_start_min) & (df['minute'] < session_end_min)
if time_filter_enable:
    df['in_time'] = (df['timestamp'].dt.hour >= start_hour) & (df['timestamp'].dt.hour < end_hour)
else:
    df['in_time'] = True
df['bar_range_pct'] = (df['high'] - df['low']) / df['low'] * 100
df['allow_bar_range'] = df['bar_range_pct'] >= min_bar_range_pct

# Indicators
def calculate_rsi(close, period):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_atr(high, low, close, period):
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def calculate_adx(high, low, close, period=14):
    plus_dm = high.diff()
    minus_dm = low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
    tr = calculate_atr(high, low, close, 1)
    plus_di = 100 * (plus_dm.ewm(span=period).mean() / tr.ewm(span=period).mean())
    minus_di = 100 * (minus_dm.ewm(span=period).mean() / tr.ewm(span=period).mean())
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di))
    adx = dx.ewm(span=period).mean()
    return adx

df['atr'] = calculate_atr(df['high'], df['low'], df['close'], atr_len)
df['adx'] = calculate_adx(df['high'], df['low'], df['close'], 14)

# Parameter ranges to test
param_combinations = [
    # Original style
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 60, 'rsi_short': 40, 'atr_mult': 2.0, 'risk': 0.5, 'momentum_period': 5, 'adx_thresh': 0},
    # Optimized
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 2.5, 'risk': 0.3, 'momentum_period': 10, 'adx_thresh': 0},
    # More flexible (looser)
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 55, 'rsi_short': 45, 'atr_mult': 1.5, 'risk': 0.7, 'momentum_period': 3, 'adx_thresh': 0},
    # Stricter
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 70, 'rsi_short': 30, 'atr_mult': 3.0, 'risk': 0.2, 'momentum_period': 15, 'adx_thresh': 25},
    # Different EMAs
    {'fast_ema': 10, 'slow_ema': 20, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 2.5, 'risk': 0.3, 'momentum_period': 10, 'adx_thresh': 0},
    {'fast_ema': 50, 'slow_ema': 100, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 2.5, 'risk': 0.3, 'momentum_period': 10, 'adx_thresh': 0},
    # Higher risk
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 2.5, 'risk': 0.5, 'momentum_period': 10, 'adx_thresh': 0},
    # Wider SL
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 3.0, 'risk': 0.3, 'momentum_period': 10, 'adx_thresh': 0},
    # No momentum
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 2.5, 'risk': 0.3, 'momentum_period': 0, 'adx_thresh': 0},
    # ADX only
    {'fast_ema': 21, 'slow_ema': 50, 'rsi_long': 65, 'rsi_short': 35, 'atr_mult': 2.5, 'risk': 0.3, 'momentum_period': 10, 'adx_thresh': 30},
]

results = []

for params in param_combinations:
    fast_ema = params['fast_ema']
    slow_ema = params['slow_ema']
    rsi_long_thresh = params['rsi_long']
    rsi_short_thresh = params['rsi_short']
    atr_mult_sl = params['atr_mult']
    risk_percent = params['risk']
    momentum_period = params['momentum_period']
    adx_thresh = params['adx_thresh']

    # Calculate indicators
    df['ema_fast'] = df['close'].ewm(span=fast_ema).mean()
    df['ema_slow'] = df['close'].ewm(span=slow_ema).mean()
    df['rsi'] = calculate_rsi(df['close'], 14)

    # Signals
    df['is_bull'] = df['ema_fast'] > df['ema_slow']
    df['is_bear'] = df['ema_fast'] < df['ema_slow']
    long_base = df['is_bull'] & (df['rsi'] > rsi_long_thresh) & df['allow_bar_range'] & df['in_time']
    short_base = df['is_bear'] & (df['rsi'] < rsi_short_thresh) & df['allow_bar_range'] & df['in_time']

    if momentum_period > 0:
        df['momentum'] = df['close'] > df['close'].shift(momentum_period)
        long_signal = long_base & df['momentum']
        short_signal = short_base & ~df['momentum']
    else:
        long_signal = long_base
        short_signal = short_base

    if adx_thresh > 0:
        long_signal = long_signal & (df['adx'] > adx_thresh)
        short_signal = short_signal & (df['adx'] > adx_thresh)

    df['long_signal'] = long_signal
    df['short_signal'] = short_signal

    # Backtest
    equity = 300000.0
    daily_start = equity
    position = 0
    entry_price = 0
    qty = 0
    sl_price = 0
    tp_price = 0
    trail_stop = 0
    breakeven_set = False
    winning_trades = []
    losing_trades = []
    all_trades = []
    daily_drawdowns = []
    daily_min = equity

    for i in range(len(df)):
        row = df.iloc[i]

        if i > 0 and row['timestamp'].date() != df.iloc[i-1]['timestamp'].date():
            daily_dd = daily_start - daily_min
            daily_drawdowns.append(daily_dd)
            daily_start = equity
            daily_min = equity
            breakeven_set = False

        daily_loss = equity - daily_start
        daily_lock = daily_loss <= -daily_max_loss

        sl_dist = row['atr'] * atr_mult_sl
        if sl_dist <= 0.25:
            sl_dist = 0.25
        risk_dollars = equity * (risk_percent / 100.0)
        per_unit_risk = sl_dist * tick_value
        if per_unit_risk > 0:
            qty_calc = int(risk_dollars // per_unit_risk)
        else:
            qty_calc = 0
        if qty_calc < 1:
            qty_calc = 0

        can_trade = (equity >= min_equity_for_trade) and (qty_calc > 0) and not daily_lock and row['in_session']

        if position == 0 and can_trade:
            if row['long_signal']:
                position = qty_calc
                entry_price = row['close']
                entry_time = row['timestamp']
                sl_price = entry_price - sl_dist
                if use_tp:
                    tp_price = entry_price + sl_dist * take_profit_rr
                else:
                    tp_price = float('inf')
                trail_stop = sl_price if not use_trailing else entry_price - trail_atr_mult * row['atr']
                breakeven_set = False

            elif row['short_signal']:
                position = -qty_calc
                entry_price = row['close']
                entry_time = row['timestamp']
                sl_price = entry_price + sl_dist
                if use_tp:
                    tp_price = entry_price - sl_dist * take_profit_rr
                else:
                    tp_price = float('-inf')
                trail_stop = sl_price if not use_trailing else entry_price + trail_atr_mult * row['atr']
                breakeven_set = False

        if position > 0:
            if use_trailing:
                curr_trail = row['close'] - trail_atr_mult * row['atr']
                trail_stop = max(trail_stop, curr_trail)
            profit = (row['close'] - entry_price) * position * tick_value
            if profit >= breakeven_after_profit and not breakeven_set:
                sl_price = entry_price
                breakeven_set = True
        elif position < 0:
            if use_trailing:
                curr_trail = row['close'] + trail_atr_mult * row['atr']
                trail_stop = min(trail_stop, curr_trail)
            profit = (entry_price - row['close']) * abs(position) * tick_value
            if profit >= breakeven_after_profit and not breakeven_set:
                sl_price = entry_price
                breakeven_set = True

        if position > 0:
            if row['close'] <= sl_price or (use_trailing and row['close'] <= trail_stop) or (use_tp and row['close'] >= tp_price):
                pnl = (row['close'] - entry_price) * position * tick_value
                equity += pnl
                win = pnl > 0
                if win:
                    winning_trades.append(pnl)
                else:
                    losing_trades.append(pnl)
                exit_reason = 'SL' if row['close'] <= sl_price else ('Trail' if use_trailing and row['close'] <= trail_stop else 'TP')
                all_trades.append({
                    'entry_time': entry_time,
                    'exit_time': row['timestamp'],
                    'pnl': pnl,
                    'type': 'long',
                    'win': win,
                    'exit_reason': exit_reason
                })
                position = 0
                breakeven_set = False
        elif position < 0:
            if row['close'] >= sl_price or (use_trailing and row['close'] >= trail_stop) or (use_tp and row['close'] <= tp_price):
                pnl = (entry_price - row['close']) * abs(position) * tick_value
                equity += pnl
                win = pnl > 0
                if win:
                    winning_trades.append(pnl)
                else:
                    losing_trades.append(pnl)
                exit_reason = 'SL' if row['close'] >= sl_price else ('Trail' if use_trailing and row['close'] >= trail_stop else 'TP')
                all_trades.append({
                    'entry_time': entry_time,
                    'exit_time': row['timestamp'],
                    'pnl': pnl,
                    'type': 'short',
                    'win': win,
                    'exit_reason': exit_reason
                })
                position = 0
                breakeven_set = False

        daily_min = min(daily_min, equity)

    # Final daily dd
    daily_dd = daily_start - daily_min
    daily_drawdowns.append(daily_dd)

    final_equity = equity
    total_pnl = final_equity - 300000
    total_trades = len(winning_trades) + len(losing_trades)
    win_rate = (len(winning_trades) / total_trades * 100) if total_trades > 0 else 0
    gross_profit = sum(winning_trades) if winning_trades else 0
    gross_loss = abs(sum(losing_trades)) if losing_trades else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999
    max_daily_dd = max(daily_drawdowns) if daily_drawdowns else 0

    results.append({
        'params': params,
        'pnl': total_pnl,
        'win_rate': win_rate,
        'pf': profit_factor,
        'dd': max_daily_dd,
        'trades': total_trades
    })

# Sort by PNL descending
results.sort(key=lambda x: x['pnl'], reverse=True)

print("Parameter Optimization Results (Top 5 by PNL):")
for i, res in enumerate(results[:5]):
    p = res['params']
    print(f"\n{i+1}. PNL: ${res['pnl']:.2f}, Win: {res['win_rate']:.2f}%, PF: {res['pf']:.2f}, DD: ${res['dd']:.2f}, Trades: {res['trades']}")
    print(f"   Params: EMA {p['fast_ema']}/{p['slow_ema']}, RSI {p['rsi_long']}/{p['rsi_short']}, ATR {p['atr_mult']}, Risk {p['risk']}%, Mom {p['momentum_period']}, ADX {p['adx_thresh']}")

print("\nTop by Win Rate:")
win_sorted = sorted(results, key=lambda x: x['win_rate'], reverse=True)
for i, res in enumerate(win_sorted[:3]):
    p = res['params']
    print(f"{i+1}. Win: {res['win_rate']:.2f}%, PNL: ${res['pnl']:.2f}, PF: {res['pf']:.2f}, DD: ${res['dd']:.2f}")
    print(f"   Params: EMA {p['fast_ema']}/{p['slow_ema']}, RSI {p['rsi_long']}/{p['rsi_short']}, ATR {p['atr_mult']}, Risk {p['risk']}%, Mom {p['momentum_period']}, ADX {p['adx_thresh']}")

print("\nTop by PF:")
pf_sorted = sorted(results, key=lambda x: x['pf'], reverse=True)
for i, res in enumerate(pf_sorted[:3]):
    p = res['params']
    print(f"{i+1}. PF: {res['pf']:.2f}, PNL: ${res['pnl']:.2f}, Win: {res['win_rate']:.2f}%, DD: ${res['dd']:.2f}")
    print(f"   Params: EMA {p['fast_ema']}/{p['slow_ema']}, RSI {p['rsi_long']}/{p['rsi_short']}, ATR {p['atr_mult']}, Risk {p['risk']}%, Mom {p['momentum_period']}, ADX {p['adx_thresh']}")