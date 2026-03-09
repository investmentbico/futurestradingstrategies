//+------------------------------------------------------------------+
//| PropFirmBot.mq5 — Upcomers Prop Firm Challenge EA                |
//| Runs natively on Mac MT5 (no Python needed)                      |
//|                                                                  |
//| Strategy: Mean-reversion EMA crossover + Stochastic + ATR        |
//| Optimized params from backtest:                                  |
//|   XAUUSD 2min: EMA 13/89, Stoch 25/80, SL 1.5xATR, TP 1.5R     |
//|   US100  2min: EMA 21/55, Stoch 20/80, SL 2.5xATR, TP 1.5R     |
//|                                                                  |
//| Prop firm rules:                                                 |
//|   $500k account, 1.5% max DD/day, $10,100 target, 61s min hold  |
//+------------------------------------------------------------------+
#property copyright "PropFirmBot"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

//--- Input parameters (change per symbol)
input group "=== Strategy Parameters ==="
input int      EMA_Fast        = 13;       // Fast EMA period
input int      EMA_Slow        = 89;       // Slow EMA period
input int      Stoch_K         = 9;        // Stochastic K period
input int      Stoch_D         = 3;        // Stochastic D period
input int      Stoch_Smooth    = 2;        // Stochastic smoothing
input int      ATR_Period       = 9;        // ATR period
input int      Stoch_Lo        = 25;       // Stochastic oversold
input int      Stoch_Hi        = 80;       // Stochastic overbought
input double   SL_ATR_Mult     = 1.5;      // SL = ATR * this
input double   TP_RR           = 1.5;      // TP = SL * this (R:R)

input group "=== Risk Management ==="
input double   Lots            = 2.0;      // Lot size
input double   HardStop        = 2000.0;   // Hard stop per trade ($)
input double   MaxDailyLoss    = 7500.0;   // Max daily loss ($)
input double   DailyTarget     = 10100.0;  // Daily profit target ($)
input int      MinHoldSeconds  = 61;       // Minimum hold time (prop firm rule)
input int      MaxDailyTrades  = 6;        // Max trades per day
input int      MaxConsecLosses = 3;        // Max consecutive losses before pause
input int      LossCooldownSec = 120;      // Cooldown after loss (seconds)

input group "=== Execution ==="
input int      MagicNumber     = 123789;   // EA magic number
input int      Slippage        = 20;       // Max slippage (points)
input ENUM_TIMEFRAMES Timeframe = PERIOD_M2; // Chart timeframe

//--- Global variables
CTrade         trade;
int            h_ema_fast, h_ema_slow, h_atr, h_stoch;
double         ema_fast_buf[], ema_slow_buf[], atr_buf[];
double         stoch_k_buf[], stoch_d_buf[];

// Position state
int            g_position;        // +1=long, -1=short, 0=flat
double         g_entry_price;
datetime       g_entry_time;
double         g_stop_loss;
double         g_trailing_stop;
double         g_peak_pnl;
ulong          g_ticket;

// Stats
int            g_total_trades;
int            g_wins;
double         g_total_pnl;
double         g_daily_pnl;
int            g_daily_trades;
bool           g_emergency_stop;
int            g_consec_losses;
bool           g_prev_uptrend;
bool           g_prev_uptrend_valid;
double         g_last_exit_pnl;
datetime       g_last_exit_time;
datetime       g_last_daily_reset;

// Point value calculation
double         g_point_value;
double         g_tick_sz;

//+------------------------------------------------------------------+
//| Expert initialization                                             |
//+------------------------------------------------------------------+
int OnInit()
{
   // Setup trade object
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(Slippage);
   trade.SetTypeFilling(ORDER_FILLING_IOC);

   // Create indicator handles
   h_ema_fast = iMA(_Symbol, Timeframe, EMA_Fast, 0, MODE_EMA, PRICE_CLOSE);
   h_ema_slow = iMA(_Symbol, Timeframe, EMA_Slow, 0, MODE_EMA, PRICE_CLOSE);
   h_atr      = iATR(_Symbol, Timeframe, ATR_Period);
   h_stoch    = iStochastic(_Symbol, Timeframe, Stoch_K, Stoch_D, Stoch_Smooth, MODE_SMA, STO_LOWHIGH);

   if(h_ema_fast == INVALID_HANDLE || h_ema_slow == INVALID_HANDLE ||
      h_atr == INVALID_HANDLE || h_stoch == INVALID_HANDLE)
   {
      Print("ERROR: Failed to create indicator handles");
      return INIT_FAILED;
   }

   // Set buffer directions
   ArraySetAsSeries(ema_fast_buf, true);
   ArraySetAsSeries(ema_slow_buf, true);
   ArraySetAsSeries(atr_buf, true);
   ArraySetAsSeries(stoch_k_buf, true);
   ArraySetAsSeries(stoch_d_buf, true);

   // Determine point value per lot
   g_tick_sz = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_val = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   if(g_tick_sz > 0)
      g_point_value = tick_val / g_tick_sz;
   else
      g_point_value = tick_val;

   // Init state
   g_position = 0;
   g_entry_price = 0;
   g_entry_time = 0;
   g_stop_loss = 0;
   g_trailing_stop = 0;
   g_peak_pnl = 0;
   g_ticket = 0;
   g_total_trades = 0;
   g_wins = 0;
   g_total_pnl = 0;
   g_daily_pnl = 0;
   g_daily_trades = 0;
   g_emergency_stop = false;
   g_consec_losses = 0;
   g_prev_uptrend = false;
   g_prev_uptrend_valid = false;
   g_last_exit_pnl = 0;
   g_last_exit_time = 0;
   g_last_daily_reset = 0;

   // Check for existing positions from this EA
   SyncExistingPosition();

   Print("=============================================================");
   Print("PROP FIRM CHALLENGE BOT — INITIALIZED");
   Print("  Symbol:    ", _Symbol);
   Print("  Lots:      ", Lots);
   Print("  Strategy:  EMA ", EMA_Fast, "/", EMA_Slow,
         " Stoch ", Stoch_Lo, "/", Stoch_Hi,
         " ATR ", ATR_Period,
         " SL ", SL_ATR_Mult, "x TP ", TP_RR, "R");
   Print("  Hard Stop: $", HardStop);
   Print("  Daily:     $", MaxDailyLoss, " max loss | $", DailyTarget, " target");
   Print("  Min Hold:  ", MinHoldSeconds, "s");
   Print("  Pt Value:  $", g_point_value, "/pt/lot  Tick: ", g_tick_sz);
   Print("=============================================================");

   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Expert deinitialization                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   if(h_ema_fast != INVALID_HANDLE) IndicatorRelease(h_ema_fast);
   if(h_ema_slow != INVALID_HANDLE) IndicatorRelease(h_ema_slow);
   if(h_atr != INVALID_HANDLE)      IndicatorRelease(h_atr);
   if(h_stoch != INVALID_HANDLE)    IndicatorRelease(h_stoch);

   double wr = (g_total_trades > 0) ? (double)g_wins / g_total_trades * 100 : 0;
   Print("=============================================================");
   Print("SESSION SUMMARY");
   Print("  Trades: ", g_total_trades, " | Wins: ", g_wins, " | WR: ", DoubleToString(wr, 1), "%");
   Print("  Total P&L: $", DoubleToString(g_total_pnl, 2));
   Print("  Daily P&L: $", DoubleToString(g_daily_pnl, 2));
   Print("=============================================================");
}

//+------------------------------------------------------------------+
//| Sync with any existing position from this EA                     |
//+------------------------------------------------------------------+
void SyncExistingPosition()
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionGetString(POSITION_SYMBOL) != _Symbol) continue;
      if(PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;

      long type = PositionGetInteger(POSITION_TYPE);
      g_position = (type == POSITION_TYPE_BUY) ? 1 : -1;
      g_entry_price = PositionGetDouble(POSITION_PRICE_OPEN);
      g_entry_time = (datetime)PositionGetInteger(POSITION_TIME);
      g_stop_loss = PositionGetDouble(POSITION_SL);
      g_ticket = ticket;
      g_trailing_stop = 0;
      g_peak_pnl = PositionGetDouble(POSITION_PROFIT);

      Print("SYNCED existing position: ", (g_position > 0 ? "LONG" : "SHORT"),
            " @ ", g_entry_price, " ticket #", g_ticket);
      break;
   }
}

//+------------------------------------------------------------------+
//| Get indicator values                                              |
//+------------------------------------------------------------------+
bool GetIndicators(double &ema_f, double &ema_s, double &atr_val,
                   double &stoch_d, double &stoch_d_prev, bool &uptrend)
{
   if(CopyBuffer(h_ema_fast, 0, 0, 2, ema_fast_buf) < 2) return false;
   if(CopyBuffer(h_ema_slow, 0, 0, 2, ema_slow_buf) < 2) return false;
   if(CopyBuffer(h_atr, 0, 0, 2, atr_buf) < 2) return false;
   if(CopyBuffer(h_stoch, 1, 0, 3, stoch_d_buf) < 3) return false;  // buffer 1 = %D

   ema_f = ema_fast_buf[0];
   ema_s = ema_slow_buf[0];
   atr_val = atr_buf[0];
   stoch_d = stoch_d_buf[0];
   stoch_d_prev = stoch_d_buf[1];
   uptrend = (ema_f > ema_s);

   return true;
}

//+------------------------------------------------------------------+
//| Calculate P&L for current position                                |
//+------------------------------------------------------------------+
double CalcPnL(double current_price)
{
   if(g_position == 0) return 0;
   double diff = (g_position > 0)
      ? (current_price - g_entry_price)
      : (g_entry_price - current_price);
   return diff * Lots * g_point_value;
}

//+------------------------------------------------------------------+
//| Check entry signals                                               |
//+------------------------------------------------------------------+
int CheckEntry(double ema_f, double ema_s, double atr_val,
               double stoch_d, double stoch_d_prev, bool uptrend)
{
   if(g_position != 0) return 0;
   if(g_emergency_stop) return 0;
   if(g_daily_trades >= MaxDailyTrades) return 0;
   if(g_consec_losses >= MaxConsecLosses) return 0;

   // Cooldown after loss
   if(g_last_exit_pnl < 0 && (TimeCurrent() - g_last_exit_time) < LossCooldownSec)
      return 0;

   // Daily limits
   if(g_daily_pnl <= -MaxDailyLoss)
   {
      g_emergency_stop = true;
      Print("DAILY LOSS LIMIT HIT: $", DoubleToString(g_daily_pnl, 2));
      return 0;
   }
   if(g_daily_pnl >= DailyTarget)
   {
      g_emergency_stop = true;
      Print("DAILY TARGET HIT: $", DoubleToString(g_daily_pnl, 2));
      return 0;
   }

   bool d_rising = (stoch_d > stoch_d_prev);
   bool d_falling = (stoch_d < stoch_d_prev);
   bool downtrend = !uptrend;
   bool atr_ok = (atr_val > 0);
   int signal = 0;

   // PRIMARY: Mean reversion from stoch extreme
   if(uptrend && atr_ok && stoch_d <= Stoch_Lo && d_rising)
   {
      signal = 1;
      Print("SIGNAL: Reversal LONG - Stoch ", DoubleToString(stoch_d, 1), " turning up in uptrend");
   }
   else if(downtrend && atr_ok && stoch_d >= Stoch_Hi && d_falling)
   {
      signal = -1;
      Print("SIGNAL: Reversal SHORT - Stoch ", DoubleToString(stoch_d, 1), " turning down in downtrend");
   }

   // SECONDARY: Moderate pullback
   if(signal == 0 && atr_ok)
   {
      if(uptrend && stoch_d <= Stoch_Lo + 10 && d_rising)
      {
         signal = 1;
         Print("SIGNAL: Momentum LONG - Stoch ", DoubleToString(stoch_d, 1), " rising");
      }
      else if(downtrend && stoch_d >= Stoch_Hi - 10 && d_falling)
      {
         signal = -1;
         Print("SIGNAL: Momentum SHORT - Stoch ", DoubleToString(stoch_d, 1), " falling");
      }
   }

   // EMA crossover
   if(signal == 0 && g_prev_uptrend_valid && atr_ok)
   {
      if(uptrend && !g_prev_uptrend && stoch_d <= Stoch_Hi - 10)
      {
         signal = 1;
         Print("SIGNAL: EMA Cross LONG - bullish cross + Stoch ", DoubleToString(stoch_d, 1));
      }
      else if(downtrend && g_prev_uptrend && stoch_d >= Stoch_Lo + 10)
      {
         signal = -1;
         Print("SIGNAL: EMA Cross SHORT - bearish cross + Stoch ", DoubleToString(stoch_d, 1));
      }
   }

   g_prev_uptrend = uptrend;
   g_prev_uptrend_valid = true;
   return signal;
}

//+------------------------------------------------------------------+
//| Check exit conditions (ENFORCES 61-SECOND RULE)                  |
//+------------------------------------------------------------------+
bool CheckExit(double atr_val, double current_price)
{
   if(g_position == 0) return false;

   long hold_time = (long)(TimeCurrent() - g_entry_time);
   double pnl = CalcPnL(current_price);

   if(pnl > g_peak_pnl)
      g_peak_pnl = pnl;

   // ═══════════════════════════════════════════════
   // 61-SECOND RULE: NEVER close before 61 seconds
   // Exception: catastrophic hard stop
   // ═══════════════════════════════════════════════
   if(hold_time < MinHoldSeconds)
   {
      if(pnl <= -HardStop)
      {
         Print("EMERGENCY HARD STOP at ", hold_time, "s (< 61s) | P&L: $", DoubleToString(pnl, 2));
         return true;
      }
      return false;
   }

   // After 61 seconds — normal exit logic:

   // Hard stop
   if(pnl <= -HardStop)
   {
      Print("HARD STOP: P&L=$", DoubleToString(pnl, 2), " | Hold=", hold_time, "s");
      return true;
   }

   double r_unit = HardStop;

   // Smart trailing stop logic
   if(g_position > 0) // LONG
   {
      if(g_peak_pnl >= r_unit * 3)
      {
         double floor_price = g_entry_price + (g_peak_pnl * 0.65) / (Lots * g_point_value);
         if(floor_price > g_trailing_stop) g_trailing_stop = floor_price;
      }
      else if(g_peak_pnl >= r_unit * 2)
      {
         double floor_price = g_entry_price + (g_peak_pnl * 0.45) / (Lots * g_point_value);
         if(floor_price > g_trailing_stop) g_trailing_stop = floor_price;
      }
      else if(g_peak_pnl >= r_unit)
      {
         double be = g_entry_price + g_tick_sz;
         if(be > g_trailing_stop) g_trailing_stop = be;
      }

      // ATR trail
      if(atr_val > 0)
      {
         double atr_trail = current_price - atr_val * 1.5;
         if(atr_trail > g_trailing_stop) g_trailing_stop = atr_trail;
      }

      if(g_trailing_stop > 0 && current_price <= g_trailing_stop)
      {
         Print("TRAIL EXIT: ", current_price, " <= ", g_trailing_stop,
               " | P&L=$", DoubleToString(pnl, 2), " | Hold=", hold_time, "s");
         return true;
      }
      if(current_price <= g_stop_loss)
      {
         Print("ATR STOP: ", current_price, " <= SL ", g_stop_loss,
               " | P&L=$", DoubleToString(pnl, 2), " | Hold=", hold_time, "s");
         return true;
      }
   }
   else if(g_position < 0) // SHORT
   {
      if(g_peak_pnl >= r_unit * 3)
      {
         double floor_price = g_entry_price - (g_peak_pnl * 0.65) / (Lots * g_point_value);
         if(g_trailing_stop == 0 || floor_price < g_trailing_stop) g_trailing_stop = floor_price;
      }
      else if(g_peak_pnl >= r_unit * 2)
      {
         double floor_price = g_entry_price - (g_peak_pnl * 0.45) / (Lots * g_point_value);
         if(g_trailing_stop == 0 || floor_price < g_trailing_stop) g_trailing_stop = floor_price;
      }
      else if(g_peak_pnl >= r_unit)
      {
         double be = g_entry_price - g_tick_sz;
         if(g_trailing_stop == 0 || be < g_trailing_stop) g_trailing_stop = be;
      }

      if(atr_val > 0)
      {
         double atr_trail = current_price + atr_val * 1.5;
         if(g_trailing_stop == 0 || atr_trail < g_trailing_stop) g_trailing_stop = atr_trail;
      }

      if(g_trailing_stop > 0 && current_price >= g_trailing_stop)
      {
         Print("TRAIL EXIT: ", current_price, " >= ", g_trailing_stop,
               " | P&L=$", DoubleToString(pnl, 2), " | Hold=", hold_time, "s");
         return true;
      }
      if(current_price >= g_stop_loss)
      {
         Print("ATR STOP: ", current_price, " >= SL ", g_stop_loss,
               " | P&L=$", DoubleToString(pnl, 2), " | Hold=", hold_time, "s");
         return true;
      }
   }

   return false;
}

//+------------------------------------------------------------------+
//| Enter trade                                                       |
//+------------------------------------------------------------------+
bool EnterTrade(int direction, double atr_val)
{
   if(g_position != 0) return false;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid == 0 || ask == 0) return false;

   double atr_sl = atr_val * SL_ATR_Mult;
   double hard_stop_pts = HardStop / (Lots * g_point_value);
   double sl_amount = MathMin(atr_sl, hard_stop_pts);
   double tp_amount = atr_val * SL_ATR_Mult * TP_RR;

   double price, sl, tp;
   ENUM_ORDER_TYPE order_type;

   if(direction > 0) // LONG
   {
      price = ask;
      sl = price - sl_amount;
      tp = price + tp_amount;
      order_type = ORDER_TYPE_BUY;
   }
   else // SHORT
   {
      price = bid;
      sl = price + sl_amount;
      tp = price - tp_amount;
      order_type = ORDER_TYPE_SELL;
   }

   // Normalize prices
   int digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   sl = NormalizeDouble(sl, digits);
   tp = NormalizeDouble(tp, digits);
   price = NormalizeDouble(price, digits);

   Print("===========================================================");
   Print((direction > 0 ? "LONG" : "SHORT"), " ENTRY: ", Lots, " lots @ ", price);
   Print("  SL: ", sl, " (", DoubleToString(sl_amount, 2), " pts) | TP: ", tp,
         " (", DoubleToString(tp_amount, 2), " pts)");
   Print("  ATR: ", DoubleToString(atr_val, 2), " | Hard Stop: $", HardStop);
   Print("===========================================================");

   bool result;
   if(direction > 0)
      result = trade.Buy(Lots, _Symbol, price, sl, tp, "PropFirmBot");
   else
      result = trade.Sell(Lots, _Symbol, price, sl, tp, "PropFirmBot");

   if(!result)
   {
      Print("ORDER FAILED: ", trade.ResultComment(), " code=", trade.ResultRetcode());
      return false;
   }

   g_ticket = trade.ResultOrder();
   g_position = direction;
   g_entry_price = trade.ResultPrice();
   g_entry_time = TimeCurrent();
   g_stop_loss = sl;
   g_trailing_stop = 0;
   g_peak_pnl = 0;
   g_daily_trades++;

   Print("ORDER FILLED: Ticket #", g_ticket, " @ ", g_entry_price);
   return true;
}

//+------------------------------------------------------------------+
//| Exit trade                                                        |
//+------------------------------------------------------------------+
bool ExitTrade()
{
   if(g_position == 0) return false;

   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   if(bid == 0 || ask == 0) return false;

   long hold_time = (long)(TimeCurrent() - g_entry_time);
   double price = (g_position > 0) ? bid : ask;
   double pnl = CalcPnL(price);
   string dir = (g_position > 0) ? "LONG" : "SHORT";

   Print("===========================================================");
   Print(dir, " EXIT: ", Lots, " lots @ ", price);
   Print("  P&L: $", DoubleToString(pnl, 2), " | Hold: ", hold_time,
         "s | Peak: $", DoubleToString(g_peak_pnl, 2));
   Print("===========================================================");

   bool result = trade.PositionClose(g_ticket, Slippage);
   if(!result)
   {
      Print("EXIT ORDER FAILED: ", trade.ResultComment());
      return false;
   }

   // Update stats
   g_total_trades++;
   if(pnl > 0)
   {
      g_wins++;
      g_consec_losses = 0;
   }
   else
      g_consec_losses++;

   g_total_pnl += pnl;
   g_daily_pnl += pnl;
   g_last_exit_pnl = pnl;
   g_last_exit_time = TimeCurrent();

   // Reset position state
   g_position = 0;
   g_entry_price = 0;
   g_entry_time = 0;
   g_stop_loss = 0;
   g_trailing_stop = 0;
   g_peak_pnl = 0;
   g_ticket = 0;

   return true;
}

//+------------------------------------------------------------------+
//| Timer event — handles 61s hold rule exit timing                  |
//+------------------------------------------------------------------+
void OnTimer()
{
   // If position open and past 61s, re-evaluate exit
   if(g_position != 0)
   {
      double ema_f, ema_s, atr_val, stoch_d, stoch_d_prev;
      bool uptrend;
      if(GetIndicators(ema_f, ema_s, atr_val, stoch_d, stoch_d_prev, uptrend))
      {
         double price = (g_position > 0)
            ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
            : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
         if(CheckExit(atr_val, price))
            ExitTrade();
      }
   }
}

//+------------------------------------------------------------------+
//| Tick event — main loop                                            |
//+------------------------------------------------------------------+
void OnTick()
{
   // Daily reset
   MqlDateTime dt;
   TimeCurrent(dt);
   datetime today_start = StringToTime(IntegerToString(dt.year) + "." +
                                        IntegerToString(dt.mon) + "." +
                                        IntegerToString(dt.day));
   if(today_start != g_last_daily_reset)
   {
      if(g_last_daily_reset != 0)
         Print("DAILY RESET | Yesterday P&L: $", DoubleToString(g_daily_pnl, 2));
      g_daily_pnl = 0;
      g_daily_trades = 0;
      g_emergency_stop = false;
      g_consec_losses = 0;
      g_last_daily_reset = today_start;
   }

   // Get indicators
   double ema_f, ema_s, atr_val, stoch_d, stoch_d_prev;
   bool uptrend;
   if(!GetIndicators(ema_f, ema_s, atr_val, stoch_d, stoch_d_prev, uptrend))
      return;

   // Check exits
   if(g_position != 0)
   {
      double exit_price = (g_position > 0)
         ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
         : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      if(CheckExit(atr_val, exit_price))
         ExitTrade();
   }

   // Check entries
   if(g_position == 0)
   {
      int signal = CheckEntry(ema_f, ema_s, atr_val, stoch_d, stoch_d_prev, uptrend);
      if(signal != 0)
         EnterTrade(signal, atr_val);
   }

   // Status logging every ~30 ticks (use static counter)
   static int tick_count = 0;
   tick_count++;
   if(tick_count % 30 == 0)
   {
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      string pos_str = (g_position > 0) ? "LONG" :
                       (g_position < 0) ? "SHORT" : "FLAT";
      double unrealized = CalcPnL((g_position > 0) ? bid : ask);
      double wr = (g_total_trades > 0) ? (double)g_wins / g_total_trades * 100 : 0;

      string trend_str = uptrend ? "UP" : "DN";
      string d_dir = (stoch_d > stoch_d_prev) ? "^" : "v";

      Print(bid, "/", ask, " | ", pos_str,
            " | Unreal=$", DoubleToString(unrealized, 0),
            " | Daily=$", DoubleToString(g_daily_pnl, 0),
            " | Trades=", g_daily_trades,
            " | Total=$", DoubleToString(g_total_pnl, 0),
            " WR=", DoubleToString(wr, 0), "%",
            " | ", trend_str, " StD=", DoubleToString(stoch_d, 0), d_dir,
            " ATR=", DoubleToString(atr_val, 1));
   }
}
//+------------------------------------------------------------------+
