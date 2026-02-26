import pandas as pd
import numpy as np

# Load data
df = pd.read_csv('data/mnq_2min.csv')
df['timestamp'] = pd.to_datetime(df['time'], unit='s')
df = df[['timestamp', 'open', 'high', 'low', 'close']]
print(f"Data from {df['timestamp'].min()} to {df['timestamp'].max()}")

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

# ATR
def calculate_atr(high, low, close, period):
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

df['atr'] = calculate_atr(df['high'], df['low'], df['close'], atr_len)

# RSI
def calculate_rsi(close, period):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# Parameter ranges
fast_emas = [5, 21]
slow_emas = [10, 50]
rsi_lens = [14]
rsi_threshs = [50]
risk_percents = [1.0, 2.0]
atr_mults = [1.0, 2.0]

best_return = -100
best_params = {}

for fast_ema in fast_emas:
    for slow_ema in slow_emas:
        if fast_ema >= slow_ema:
            continue
        for rsi_len in rsi_lens:
            for rsi_thresh in rsi_threshs:
                rsi_long_thresh = rsi_thresh
                rsi_short_thresh = rsi_thresh
                for risk_percent in risk_percents:
                    for atr_mult_sl in atr_mults:
                        # Indicators
                        df['ema_fast'] = df['close'].ewm(span=fast_ema).mean()
                        df['ema_slow'] = df['close'].ewm(span=slow_ema).mean()
                        df['rsi'] = calculate_rsi(df['close'], rsi_len)
                        df['is_bull'] = df['ema_fast'] > df['ema_slow']
                        df['is_bear'] = df['ema_fast'] < df['ema_slow']
                        df['long_signal'] = df['is_bull'] & (df['rsi'] > rsi_long_thresh) & df['allow_bar_range'] & df['in_time']
                        df['short_signal'] = df['is_bear'] & (df['rsi'] < rsi_short_thresh) & df['allow_bar_range'] & df['in_time']
                        
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
                        equity_curve = []
                        
                        for i in range(len(df)):
                            row = df.iloc[i]
                            
                            if i > 0 and row['timestamp'].date() != df.iloc[i-1]['timestamp'].date():
                                daily_start = equity
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
                                    position = 0
                                    breakeven_set = False
                            elif position < 0:
                                if row['close'] >= sl_price or (use_trailing and row['close'] >= trail_stop) or (use_tp and row['close'] <= tp_price):
                                    pnl = (entry_price - row['close']) * abs(position) * tick_value
                                    equity += pnl
                                    position = 0
                                    breakeven_set = False
                            
                            equity_curve.append(equity)
                        
                        final_equity = equity
                        total_return = (final_equity - 300000) / 300000 * 100
                        if total_return > best_return:
                            best_return = total_return
                            best_params = {
                                'fast_ema': fast_ema,
                                'slow_ema': slow_ema,
                                'rsi_len': rsi_len,
                                'rsi_thresh': rsi_thresh,
                                'risk_percent': risk_percent,
                                'atr_mult_sl': atr_mult_sl
                            }

print(f"Best Return: {best_return:.2f}%")
print(f"Best Params: {best_params}")