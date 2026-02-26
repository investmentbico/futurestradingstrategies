import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# Load data
# Assume CSV with columns: time (unix), open, high, low, close, ...
df = pd.read_csv('data/mnq_15min.csv')
df['timestamp'] = pd.to_datetime(df['time'], unit='s')
df = df[['timestamp', 'open', 'high', 'low', 'close']]
print(f"Data from {df['timestamp'].min()} to {df['timestamp'].max()}")

# Inputs
fast_ema = 5
slow_ema = 10
rsi_len = 14
rsi_long_thresh = 50
rsi_short_thresh = 50
atr_len = 14
atr_mult_sl = 1.0
take_profit_rr = 2.0
use_tp = True
use_trailing = True
trail_atr_mult = 1.0
breakeven_after_profit = 100.0
risk_percent = 1.0
min_equity_for_trade = 100.0
tick_value = 0.25  # MNQ tick value
daily_max_loss = 3000
session_start_min = 420  # 7am ET
session_end_min = 945   # 3:45pm ET
min_bar_range_pct = 0.0  # off
time_filter_enable = False
start_hour = 7
end_hour = 20

# Calculate minute of day
df['minute'] = df['timestamp'].dt.hour * 60 + df['timestamp'].dt.minute
df['in_session'] = (df['minute'] >= session_start_min) & (df['minute'] < session_end_min)

# Time filter
if time_filter_enable:
    df['in_time'] = (df['timestamp'].dt.hour >= start_hour) & (df['timestamp'].dt.hour < end_hour)
else:
    df['in_time'] = True

# Bar range filter
df['bar_range_pct'] = (df['high'] - df['low']) / df['low'] * 100
df['allow_bar_range'] = df['bar_range_pct'] >= min_bar_range_pct

# Indicators
df['ema_fast'] = df['close'].ewm(span=fast_ema).mean()
df['ema_slow'] = df['close'].ewm(span=slow_ema).mean()

# RSI
def calculate_rsi(close, period):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

df['rsi'] = calculate_rsi(df['close'], rsi_len)

# ATR
def calculate_atr(high, low, close, period):
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

df['atr'] = calculate_atr(df['high'], df['low'], df['close'], atr_len)

# ADX
def calculate_adx(high, low, close, period=14):
    dm_plus = high - high.shift(1)
    dm_minus = low.shift(1) - low
    dm_plus = dm_plus.where((dm_plus > dm_minus) & (dm_plus > 0), 0)
    dm_minus = dm_minus.where((dm_minus > dm_plus) & (dm_minus > 0), 0)
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    di_plus = 100 * (dm_plus.rolling(period).mean() / atr)
    di_minus = 100 * (dm_minus.rolling(period).mean() / atr)
    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
    adx = dx.rolling(period).mean()
    return adx

df['adx'] = calculate_adx(df['high'], df['low'], df['close'], 14)

# Signals
df['is_bull'] = df['ema_fast'] > df['ema_slow']
df['is_bear'] = df['ema_fast'] < df['ema_slow']
df['long_signal'] = df['is_bull'] & (df['rsi'] > rsi_long_thresh) & df['allow_bar_range'] & df['in_time'] & (df['adx'] > 25)
df['short_signal'] = df['is_bear'] & (df['rsi'] < rsi_short_thresh) & df['allow_bar_range'] & df['in_time'] & (df['adx'] > 25)

# Backtest
equity = 300000.0
daily_start = equity
position = 0  # positive for long, negative for short
entry_price = 0
qty = 0
sl_price = 0
tp_price = 0
trail_stop = 0
breakeven_set = False
equity_curve = []

for i in range(len(df)):
    row = df.iloc[i]
    
    # New day
    if i > 0 and row['timestamp'].date() != df.iloc[i-1]['timestamp'].date():
        daily_start = equity
        breakeven_set = False  # reset daily?
    
    daily_loss = equity - daily_start
    daily_lock = daily_loss <= -daily_max_loss
    
    # Calculate qty
    sl_dist = row['atr'] * atr_mult_sl
    if sl_dist <= 0.25:  # mintick
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
    
    # Entries
    if position == 0 and can_trade:
        if row['long_signal']:
            position = qty_calc
            entry_price = row['close']
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
            sl_price = entry_price + sl_dist
            if use_tp:
                tp_price = entry_price - sl_dist * take_profit_rr
            else:
                tp_price = float('-inf')
            trail_stop = sl_price if not use_trailing else entry_price + trail_atr_mult * row['atr']
            breakeven_set = False
    
    # Update trailing and breakeven
    if position > 0:
        if use_trailing:
            curr_trail = row['close'] - trail_atr_mult * row['atr']
            trail_stop = max(trail_stop, curr_trail)
        # Breakeven
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
    
    # Exits
    if position > 0:
        if row['close'] <= sl_price or (use_trailing and row['close'] <= trail_stop) or (use_tp and row['close'] >= tp_price):
            pnl = (row['close'] - entry_price) * position * tick_value
            equity += pnl
            position = 0
            breakeven_set = False
    elif position < 0:
        if row['close'] >= sl_price or (use_trailing and row['close'] >= trail_stop) or (use_tp and row['close'] <= tp_price):
            pnl = (entry_price - row['close']) * abs(position) * tick_value
            equity += pnl
            position = 0
            breakeven_set = False
    
    equity_curve.append(equity)

df['equity'] = equity_curve

# Results
initial_equity = 300000
final_equity = equity
total_return = (final_equity - initial_equity) / initial_equity * 100
print(f"Initial Equity: ${initial_equity}")
print(f"Final Equity: ${final_equity:.2f}")
print(f"Total Return: {total_return:.2f}%")

# Plot equity curve
# plt.figure(figsize=(12, 6))
# plt.plot(df['timestamp'], df['equity'])
# plt.title('Equity Curve')
# plt.xlabel('Time')
# plt.ylabel('Equity')
# plt.show()

# Save results
df.to_csv('results.csv', index=False)