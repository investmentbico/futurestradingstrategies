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

# Conservative params for prop firm - ORIGINAL TOP
fast_ema = 21
slow_ema = 50
rsi_len = 14
rsi_long_thresh = 60
rsi_short_thresh = 40
atr_mult_sl = 2.0
risk_percent = 0.6

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
df['ema_fast'] = df['close'].ewm(span=fast_ema).mean()
df['ema_slow'] = df['close'].ewm(span=slow_ema).mean()

def calculate_rsi(close, period):
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

df['rsi'] = calculate_rsi(df['close'], rsi_len)

def calculate_atr(high, low, close, period):
    hl = high - low
    hc = (high - close.shift(1)).abs()
    lc = (low - close.shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

df['atr'] = calculate_atr(df['high'], df['low'], df['close'], atr_len)

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

df['adx'] = calculate_adx(df['high'], df['low'], df['close'], 14)

# Alpha mode
df['momentum'] = df['close'] > df['close'].shift(5)

df['is_bull'] = df['ema_fast'] > df['ema_slow']
df['is_bear'] = df['ema_fast'] < df['ema_slow']
long_base = df['is_bull'] & (df['rsi'] > rsi_long_thresh) & df['allow_bar_range'] & df['in_time']
short_base = df['is_bear'] & (df['rsi'] < rsi_short_thresh) & df['allow_bar_range'] & df['in_time']

long_signal = long_base & df['momentum'] & (df['adx'] > 20)
short_signal = short_base & ~df['momentum'] & (df['adx'] > 20)

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

# Analyze trades
if all_trades:
    wins = [t for t in all_trades if t['win']]
    losses = [t for t in all_trades if not t['win']]
    avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
    max_win = max((t['pnl'] for t in wins), default=0)
    max_loss = min((t['pnl'] for t in losses), default=0)
    long_trades = [t for t in all_trades if t['type'] == 'long']
    short_trades = [t for t in all_trades if t['type'] == 'short']
    long_win_rate = sum(1 for t in long_trades if t['win']) / len(long_trades) * 100 if long_trades else 0
    short_win_rate = sum(1 for t in short_trades if t['win']) / len(short_trades) * 100 if short_trades else 0
    sl_exits = sum(1 for t in all_trades if t['exit_reason'] == 'SL')
    tp_exits = sum(1 for t in all_trades if t['exit_reason'] == 'TP')
    trail_exits = sum(1 for t in all_trades if t['exit_reason'] == 'Trail')
    
    print('Alpha Mode Optimized Trade Analysis:')
    print(f'  Total Trades: {total_trades}')
    print(f'  Wins: {len(wins)}, Losses: {len(losses)}')
    print(f'  Win Rate: {win_rate:.2f}%')
    print(f'  Avg Win: ${avg_win:.2f}, Avg Loss: ${avg_loss:.2f}')
    print(f'  Max Win: ${max_win:.2f}, Max Loss: ${max_loss:.2f}')
    print(f'  Profit Factor: {profit_factor:.2f}')
    print(f'  Total PNL: ${total_pnl:.2f}')
    print(f'  Max Daily Drawdown: ${max_daily_dd:.2f}')
    print(f'  Long Trades: {len(long_trades)}, Win Rate: {long_win_rate:.2f}%')
    print(f'  Short Trades: {len(short_trades)}, Win Rate: {short_win_rate:.2f}%')
    print(f'  Exits - SL: {sl_exits}, TP: {tp_exits}, Trail: {trail_exits}')