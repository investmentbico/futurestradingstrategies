import pandas as pd
import numpy as np
import logging
from datetime import datetime
import pytz

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("StochD-Backtest")

# Load data - assuming mnq_1min.csv has columns: time, open, high, low, close
df = pd.read_csv('data/mnq_1min.csv')
df['timestamp'] = pd.to_datetime(df['time'], unit='s', utc=True)
df = df[['timestamp', 'open', 'high', 'low', 'close']].sort_values('timestamp').reset_index(drop=True)

# CONFIG - same as live bot
CONFIG = {
    "EMA_FAST"      : 34,
    "EMA_SLOW"      : 89,
    "STOCH_K"       : 14,
    "STOCH_D"       : 3,
    "STOCH_SMT"     : 3,
    "ATR_LEN"       : 14,
    "STOCH_LO"      : 30,
    "STOCH_HI"      : 70,
    "SL_ATR_MULT"   : 1.0,
    "TP_RR"         : 1.5,
    "TRAIL_ATR_MULT": 0.5,
    "BE_POINTS"     : 8.0,
    "CONTRACTS"     : 2,
    "POINT_VALUE"   : 20.0,
    "TICK_SIZE"     : 0.25,
    "MAX_LOSS_TRADE": 1200.0,
    "MAX_LOSS_DAY"  : 1200.0,
    "COMMISSION_RT" : 3.10,
    "SESSION_START" : (10, 0),
    "SESSION_END"   : (11, 0),
    "TIMEZONE"      : "America/New_York",
}

# Indicators
def calc_ema(prices: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(prices, dtype=float)
    out[0] = prices[0]
    for i in range(1, len(prices)):
        out[i] = prices[i] * k + out[i-1] * (1 - k)
    return out

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty(len(tr), dtype=float)
    out[:period] = np.nan
    out[period-1] = tr[:period].mean()
    for i in range(period, len(tr)):
        out[i] = (out[i-1] * (period-1) + tr[i]) / period
    return out

def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray, k_period: int, d_period: int, smooth: int):
    n = len(close)
    raw_k = np.empty(n, dtype=float)
    for i in range(n):
        lo = low[max(0, i - k_period + 1) : i + 1].min()
        hi = high[max(0, i - k_period + 1) : i + 1].max()
        rng = hi - lo
        raw_k[i] = 100.0 * (close[i] - lo) / rng if rng > 1e-9 else 50.0
    k_smooth = pd.Series(raw_k).rolling(smooth).mean().values
    d_smooth = pd.Series(k_smooth).rolling(d_period).mean().values
    return k_smooth, d_smooth

def compute_signals(bars: pd.DataFrame):
    if len(bars) < max(CONFIG["EMA_SLOW"], CONFIG["STOCH_K"] + CONFIG["STOCH_D"], CONFIG["ATR_LEN"]) + 5:
        return {}
    c = bars["close"].values
    h = bars["high"].values
    l = bars["low"].values
    ema_f = calc_ema(c, CONFIG["EMA_FAST"])
    ema_s = calc_ema(c, CONFIG["EMA_SLOW"])
    atr = calc_atr(h, l, c, CONFIG["ATR_LEN"])
    k_sm, d_sm = calc_stoch(h, l, c, CONFIG["STOCH_K"], CONFIG["STOCH_D"], CONFIG["STOCH_SMT"])
    return {
        "close": c[-1],
        "ema_fast": ema_f[-1],
        "ema_slow": ema_s[-1],
        "atr": atr[-1],
        "stoch_d": d_sm[-1],
        "stoch_d_prev": d_sm[-2] if len(d_sm) >= 2 else d_sm[-1],
        "uptrend": ema_f[-1] > ema_s[-1],
        "downtrend": ema_f[-1] < ema_s[-1],
        "d_rising": d_sm[-1] > d_sm[-2],
        "d_falling": d_sm[-1] < d_sm[-2],
        "atr_valid": not np.isnan(atr[-1]),
    }

# Session check
def in_session(ts: datetime) -> bool:
    tz = pytz.timezone(CONFIG["TIMEZONE"])
    now = ts.astimezone(tz)
    start = now.replace(hour=CONFIG["SESSION_START"][0], minute=CONFIG["SESSION_START"][1], second=0, microsecond=0)
    end = now.replace(hour=CONFIG["SESSION_END"][0], minute=CONFIG["SESSION_END"][1], second=0, microsecond=0)
    return start <= now < end

# Position state
class PositionState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.direction = None
        self.entry_price = None
        self.contracts = 0
        self.sl_price = None
        self.tp_price = None
        self.trail_dist = None
        self.trail_stop = None
        self.be_set = False
        self.entry_time = None

    def open_long(self, entry: float, sl: float, tp: float, trail: float, contracts: int):
        self.active = True
        self.direction = "long"
        self.entry_price = entry
        self.contracts = contracts
        self.sl_price = sl
        self.tp_price = tp
        self.trail_dist = trail
        self.trail_stop = entry - trail
        self.be_set = False
        self.entry_time = datetime.now()

    def open_short(self, entry: float, sl: float, tp: float, trail: float, contracts: int):
        self.active = True
        self.direction = "short"
        self.entry_price = entry
        self.contracts = contracts
        self.sl_price = sl
        self.tp_price = tp
        self.trail_dist = trail
        self.trail_stop = entry + trail
        self.be_set = False
        self.entry_time = datetime.now()

    def update_trail(self, high: float, low: float):
        if not self.active:
            return
        if self.direction == "long":
            new_trail = high - self.trail_dist
            if new_trail > self.trail_stop:
                self.trail_stop = new_trail
        elif self.direction == "short":
            new_trail = low + self.trail_dist
            if new_trail < self.trail_stop:
                self.trail_stop = new_trail

    def check_breakeven(self, price: float) -> bool:
        if self.be_set or not self.active:
            return False
        pts = (price - self.entry_price) if self.direction == "long" else (self.entry_price - price)
        if pts >= CONFIG["BE_POINTS"]:
            self.sl_price = self.entry_price
            self.trail_stop = self.entry_price
            self.be_set = True
            return True
        return False

    def current_pnl_pts(self, price: float) -> float:
        if not self.active:
            return 0.0
        return (price - self.entry_price) if self.direction == "long" else (self.entry_price - price)

    def current_pnl_dollars(self, price: float) -> float:
        pts = self.current_pnl_pts(price)
        return pts * self.contracts * CONFIG["POINT_VALUE"] - CONFIG["COMMISSION_RT"] * self.contracts

    def effective_sl(self) -> float:
        if self.direction == "long":
            return max(self.sl_price, self.trail_stop)
        else:
            return min(self.sl_price, self.trail_stop)

# Risk manager
class RiskManager:
    def __init__(self):
        self.daily_pnl = 0.0
        self.trade_count = 0
        self.day_halted = False
        self.last_reset_day = None

    def check_new_day(self, ts: datetime):
        today = ts.date()
        if self.last_reset_day != today:
            self.daily_pnl = 0.0
            self.day_halted = False
            self.last_reset_day = today

    def record_trade(self, pnl_dollars: float):
        commission = CONFIG["COMMISSION_RT"] * CONFIG["CONTRACTS"]
        net = pnl_dollars - commission
        self.daily_pnl += net
        self.trade_count += 1
        if self.daily_pnl <= -CONFIG["MAX_LOSS_DAY"]:
            self.day_halted = True

    def can_trade(self, ts: datetime) -> bool:
        self.check_new_day(ts)
        if self.day_halted:
            return False
        return True

    def calc_sl_distance(self, atr: float) -> float:
        atr_sl = max(atr * CONFIG["SL_ATR_MULT"], CONFIG["TICK_SIZE"])
        hard_sl = CONFIG["MAX_LOSS_TRADE"] / (CONFIG["CONTRACTS"] * CONFIG["POINT_VALUE"])
        return min(atr_sl, hard_sl)

# Backtest
position = PositionState()
risk = RiskManager()
bars = pd.DataFrame()
trades = []

for idx, row in df.iterrows():
    ts = row['timestamp']
    bar = {
        'timestamp': ts,
        'open': row['open'],
        'high': row['high'],
        'low': row['low'],
        'close': row['close']
    }
    bars = pd.concat([bars, pd.DataFrame([bar])], ignore_index=True)
    if len(bars) > 300:
        bars = bars.iloc[-300:].reset_index(drop=True)

    if len(bars) < 100:  # warmup
        continue

    sig = compute_signals(bars)
    if not sig or not sig.get("atr_valid"):
        continue

    # Update trail on bar
    if position.active:
        position.update_trail(row['high'], row['low'])
        position.check_breakeven(row['close'])

    # Check exits
    if position.active:
        eff_sl = position.effective_sl()
        if position.direction == "long":
            if row['low'] <= eff_sl:
                exit_price = eff_sl
                reason = "SL"
            elif row['high'] >= position.tp_price:
                exit_price = position.tp_price
                reason = "TP"
            else:
                exit_price = None
        elif position.direction == "short":
            if row['high'] >= eff_sl:
                exit_price = eff_sl
                reason = "SL"
            elif row['low'] <= position.tp_price:
                exit_price = position.tp_price
                reason = "TP"
            else:
                exit_price = None

        if exit_price:
            pnl = position.current_pnl_dollars(exit_price)
            risk.record_trade(pnl)
            trades.append({
                'entry_time': position.entry_time,
                'exit_time': ts,
                'direction': position.direction,
                'entry_price': position.entry_price,
                'exit_price': exit_price,
                'pnl': pnl,
                'reason': reason
            })
            position.reset()

    # Entry signals
    if not position.active and in_session(ts) and risk.can_trade(ts):
        sl_dist = risk.calc_sl_distance(sig["atr"])
        tp_dist = sl_dist * CONFIG["TP_RR"]
        trail = sig["atr"] * CONFIG["TRAIL_ATR_MULT"]
        price = sig["close"]

        if sig["uptrend"] and sig["stoch_d"] < CONFIG["STOCH_LO"] and sig["d_rising"]:
            sl = price - sl_dist
            tp = price + tp_dist
            position.open_long(price, sl, tp, trail, CONFIG["CONTRACTS"])
            log.info(f"ENTER LONG @ {price:.2f} SL={sl:.2f} TP={tp:.2f}")

        elif sig["downtrend"] and sig["stoch_d"] > CONFIG["STOCH_HI"] and sig["d_falling"]:
            sl = price + sl_dist
            tp = price - tp_dist
            position.open_short(price, sl, tp, trail, CONFIG["CONTRACTS"])
            log.info(f"ENTER SHORT @ {price:.2f} SL={sl:.2f} TP={tp:.2f}")

# Final results
total_trades = len(trades)
wins = [t for t in trades if t['pnl'] > 0]
losses = [t for t in trades if t['pnl'] <= 0]
win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0
total_pnl = sum(t['pnl'] for t in trades)
avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
profit_factor = sum(t['pnl'] for t in wins) / abs(sum(t['pnl'] for t in losses)) if losses else 999

print("Stoch-D NQ Backtest Results:")
print(f"Total Trades: {total_trades}")
print(f"Win Rate: {win_rate:.2f}%")
print(f"Total PNL: ${total_pnl:.2f}")
print(f"Avg Win: ${avg_win:.2f}")
print(f"Avg Loss: ${avg_loss:.2f}")
print(f"Profit Factor: {profit_factor:.2f}")
print(f"Max Daily Drawdown: ${risk.daily_pnl:.2f}")  # approximate