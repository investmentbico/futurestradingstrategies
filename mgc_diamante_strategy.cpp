/**
 * MGC Diamante Strategy - ATR Trailing Stop with Opposite-Signal Exit
 * ====================================================================
 * Converted from Pine Script: "Bruno Iarussi DIAMANTE CODE - Binance Strategy"
 *
 * KEY CHANGE from original: Positions are held until the OPPOSITE signal fires.
 *   - Long position exits ONLY when a Sell signal is generated
 *   - Short position exits ONLY when a Buy signal is generated
 *   - This eliminates premature HFT exits and rides trends fully
 *
 * Instrument: MGC (Micro Gold Futures, CME)
 *   - Point value: $10/point
 *   - Tick size: 0.10 ($1.00/tick)
 *
 * Build:
 *   g++ -O2 -std=c++17 -o mgc_diamante mgc_diamante_strategy.cpp
 *
 * Usage:
 *   ./mgc_diamante <data.csv> [key_value] [atr_period] [use_heikin_ashi]
 *   ./mgc_diamante data/mgc_1min.csv 1 10 0
 *   ./mgc_diamante data/mgc_5min.csv 2 14 1
 */

#include <iostream>
#include <fstream>
#include <sstream>
#include <vector>
#include <string>
#include <cmath>
#include <algorithm>
#include <numeric>
#include <iomanip>
#include <cstdlib>
#include <cstring>
#include <limits>

// =============================================================================
// DATA STRUCTURES
// =============================================================================

struct Bar {
    long long timestamp;
    double open;
    double high;
    double low;
    double close;
};

struct HeikinAshiBar {
    double open;
    double high;
    double low;
    double close;
};

struct Trade {
    long long entry_time;
    long long exit_time;
    double entry_price;
    double exit_price;
    int direction;         // +1 = long, -1 = short
    int contracts;
    double pnl;
    double costs;
    std::string exit_reason;
};

struct BacktestResult {
    int total_trades;
    int winning_trades;
    int losing_trades;
    double win_rate;
    double total_pnl;
    double gross_profit;
    double gross_loss;
    double profit_factor;
    double max_drawdown;
    double max_daily_drawdown;
    double avg_win;
    double avg_loss;
    double avg_holding_bars;
    std::vector<Trade> trades;
    std::vector<double> equity_curve;
};

// =============================================================================
// MGC CONTRACT SPECIFICATIONS
// =============================================================================

constexpr double MGC_POINT_VALUE = 10.0;    // $10 per point
constexpr double MGC_TICK_SIZE   = 0.10;    // 0.10 minimum tick
constexpr double MGC_TICK_VALUE  = 1.00;    // $1.00 per tick

// Apex Prop Firm costs per contract per side
constexpr double APEX_EXCHANGE_FEE  = 1.25;   // CME exchange fee
constexpr double APEX_CLEARING_FEE  = 0.10;   // Clearing fee
constexpr double APEX_NFA_FEE       = 0.02;   // NFA regulatory fee
constexpr double APEX_BROKER_COMM   = 0.25;   // Tradovate/Apex commission
constexpr double APEX_COST_PER_SIDE = APEX_EXCHANGE_FEE + APEX_CLEARING_FEE + APEX_NFA_FEE + APEX_BROKER_COMM;  // ~$1.62/side
constexpr double APEX_COST_RT       = APEX_COST_PER_SIDE * 2.0;  // ~$3.24 round trip per contract

constexpr double SPREAD_SLIPPAGE_TICKS = 1.0;  // 1 tick slippage each side (conservative for MGC)

// =============================================================================
// INDICATOR CALCULATIONS
// =============================================================================

/**
 * Calculate True Range for a single bar
 */
static inline double true_range(double high, double low, double prev_close) {
    return std::max({high - low, std::abs(high - prev_close), std::abs(low - prev_close)});
}

/**
 * ATR - Average True Range (Wilder's smoothing)
 */
std::vector<double> calc_atr(const std::vector<Bar>& bars, int period) {
    size_t n = bars.size();
    std::vector<double> atr(n, std::numeric_limits<double>::quiet_NaN());

    if ((int)n < period + 1) return atr;

    // Calculate true ranges
    std::vector<double> tr(n);
    tr[0] = bars[0].high - bars[0].low;
    for (size_t i = 1; i < n; ++i) {
        tr[i] = true_range(bars[i].high, bars[i].low, bars[i - 1].close);
    }

    // Initial ATR = simple average of first 'period' TRs
    double sum = 0.0;
    for (int i = 0; i < period; ++i) {
        sum += tr[i];
    }
    atr[period - 1] = sum / period;

    // Wilder's smoothing
    for (size_t i = (size_t)period; i < n; ++i) {
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period;
    }

    return atr;
}

/**
 * EMA - Exponential Moving Average
 */
std::vector<double> calc_ema(const std::vector<double>& prices, int period) {
    size_t n = prices.size();
    std::vector<double> ema(n);
    double k = 2.0 / (period + 1);

    ema[0] = prices[0];
    for (size_t i = 1; i < n; ++i) {
        ema[i] = prices[i] * k + ema[i - 1] * (1.0 - k);
    }

    return ema;
}

// =============================================================================
// HEIKIN ASHI CONVERSION
// =============================================================================

std::vector<HeikinAshiBar> to_heikin_ashi(const std::vector<Bar>& bars) {
    size_t n = bars.size();
    std::vector<HeikinAshiBar> ha(n);

    // First bar
    ha[0].close = (bars[0].open + bars[0].high + bars[0].low + bars[0].close) / 4.0;
    ha[0].open  = (bars[0].open + bars[0].close) / 2.0;
    ha[0].high  = bars[0].high;
    ha[0].low   = bars[0].low;

    for (size_t i = 1; i < n; ++i) {
        ha[i].close = (bars[i].open + bars[i].high + bars[i].low + bars[i].close) / 4.0;
        ha[i].open  = (ha[i - 1].open + ha[i - 1].close) / 2.0;
        ha[i].high  = std::max({bars[i].high, ha[i].open, ha[i].close});
        ha[i].low   = std::min({bars[i].low, ha[i].open, ha[i].close});
    }

    return ha;
}

// =============================================================================
// CORE STRATEGY: ATR TRAILING STOP WITH OPPOSITE-SIGNAL EXIT
// =============================================================================

/**
 * Calculate the ATR Trailing Stop line
 *
 * Logic from Pine Script:
 *   xATRTrailingStop :=
 *     src > nz(xATRTrailingStop[1]) and src[1] > nz(xATRTrailingStop[1]) ? max(nz(xATRTrailingStop[1]), src - nLoss) :
 *     src < nz(xATRTrailingStop[1]) and src[1] < nz(xATRTrailingStop[1]) ? min(nz(xATRTrailingStop[1]), src + nLoss) :
 *     src > nz(xATRTrailingStop[1]) ? src - nLoss : src + nLoss
 */
std::vector<double> calc_atr_trailing_stop(const std::vector<double>& src,
                                            const std::vector<double>& atr,
                                            int key_value) {
    size_t n = src.size();
    std::vector<double> trail(n, 0.0);

    // Initialize first valid bar
    int start = 0;
    for (size_t i = 0; i < n; ++i) {
        if (!std::isnan(atr[i])) {
            start = (int)i;
            trail[i] = src[i] - key_value * atr[i];
            break;
        }
    }

    for (size_t i = (size_t)(start + 1); i < n; ++i) {
        if (std::isnan(atr[i])) {
            trail[i] = trail[i - 1];
            continue;
        }

        double nLoss = key_value * atr[i];
        double prev_trail = trail[i - 1];

        if (src[i] > prev_trail && src[i - 1] > prev_trail) {
            // Price above trailing stop — ratchet up (tighten for longs)
            trail[i] = std::max(prev_trail, src[i] - nLoss);
        } else if (src[i] < prev_trail && src[i - 1] < prev_trail) {
            // Price below trailing stop — ratchet down (tighten for shorts)
            trail[i] = std::min(prev_trail, src[i] + nLoss);
        } else if (src[i] > prev_trail) {
            // Price crossed above — switch to long trailing
            trail[i] = src[i] - nLoss;
        } else {
            // Price crossed below — switch to short trailing
            trail[i] = src[i] + nLoss;
        }
    }

    return trail;
}

// =============================================================================
// SIGNAL GENERATION
// =============================================================================

struct Signals {
    std::vector<bool> buy_signal;   // Crossover: price crosses ABOVE trailing stop
    std::vector<bool> sell_signal;  // Crossover: price crosses BELOW trailing stop
    std::vector<bool> bar_buy;      // Price is above trailing stop
    std::vector<bool> bar_sell;     // Price is below trailing stop
};

Signals generate_signals(const std::vector<double>& src,
                          const std::vector<double>& trail) {
    size_t n = src.size();
    Signals sig;
    sig.buy_signal.resize(n, false);
    sig.sell_signal.resize(n, false);
    sig.bar_buy.resize(n, false);
    sig.bar_sell.resize(n, false);

    // EMA(src, 1) is just src itself (EMA period 1 = identity)
    // Pine: above = crossover(ema1, xATRTrailingStop)
    // Pine: below = crossover(xATRTrailingStop, ema1)
    // Pine: buySignal  = src > xATRTrailingStop and above
    // Pine: sellSignal = src < xATRTrailingStop and below

    for (size_t i = 1; i < n; ++i) {
        // Crossover detection
        bool above = (src[i] > trail[i]) && (src[i - 1] <= trail[i - 1]);
        bool below = (src[i] < trail[i]) && (src[i - 1] >= trail[i - 1]);

        sig.buy_signal[i]  = (src[i] > trail[i]) && above;
        sig.sell_signal[i] = (src[i] < trail[i]) && below;
        sig.bar_buy[i]     = (src[i] > trail[i]);
        sig.bar_sell[i]    = (src[i] < trail[i]);
    }

    return sig;
}

// =============================================================================
// BACKTEST ENGINE — OPPOSITE-SIGNAL EXIT ONLY
// =============================================================================

/**
 * Run the Diamante strategy backtest.
 *
 * EXIT LOGIC (the key change from original):
 *   - A Long position is ONLY exited when a Sell signal fires
 *   - A Short position is ONLY exited when a Buy signal fires
 *   - No trailing stops, no take-profit, no hard stops for exits
 *   - This lets the strategy ride the full trend until reversal
 *
 * The ATR trailing stop is used purely for signal generation,
 * NOT for position management exits.
 */
BacktestResult run_backtest(const std::vector<Bar>& bars,
                             int key_value,
                             int atr_period,
                             bool use_heikin_ashi,
                             int contracts,
                             double starting_equity,
                             double max_daily_drawdown) {
    size_t n = bars.size();
    BacktestResult result = {};
    result.equity_curve.reserve(n);

    // Get price source (Heikin Ashi or regular close)
    std::vector<double> src(n);
    if (use_heikin_ashi) {
        auto ha = to_heikin_ashi(bars);
        for (size_t i = 0; i < n; ++i) {
            src[i] = ha[i].close;
        }
    } else {
        for (size_t i = 0; i < n; ++i) {
            src[i] = bars[i].close;
        }
    }

    // Calculate indicators
    auto atr = calc_atr(bars, atr_period);
    auto trail = calc_atr_trailing_stop(src, atr, key_value);
    auto signals = generate_signals(src, trail);

    // Trading state
    int position = 0;           // +contracts = long, -contracts = short, 0 = flat
    double entry_price = 0.0;
    long long entry_time = 0;
    int entry_bar = 0;

    double equity = starting_equity;
    double peak_equity = starting_equity;
    double max_dd = 0.0;

    // Daily drawdown tracking
    double daily_pnl = 0.0;
    double max_daily_dd = 0.0;
    long long current_day = -1;  // Track by day (timestamp / 86400)

    result.equity_curve.push_back(equity);

    // Warmup: skip until ATR is valid
    int warmup = atr_period + 2;

    for (size_t i = (size_t)warmup; i < n; ++i) {
        // Daily reset check (using Unix day)
        long long day = bars[i].timestamp / 86400;
        if (day != current_day) {
            if (current_day >= 0 && daily_pnl < 0) {
                max_daily_dd = std::min(max_daily_dd, daily_pnl);
            }
            daily_pnl = 0.0;
            current_day = day;
        }

        // Check if daily drawdown limit hit — close position and stop trading today
        if (daily_pnl <= -max_daily_drawdown && position != 0) {
            double exit_price = bars[i].close;
            double slippage = SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE;

            double pnl;
            if (position > 0) {
                exit_price -= slippage;
                pnl = (exit_price - entry_price) * std::abs(position) * MGC_POINT_VALUE;
            } else {
                exit_price += slippage;
                pnl = (entry_price - exit_price) * std::abs(position) * MGC_POINT_VALUE;
            }

            double costs = APEX_COST_RT * std::abs(position);
            pnl -= costs;

            Trade t;
            t.entry_time = entry_time;
            t.exit_time = bars[i].timestamp;
            t.entry_price = entry_price;
            t.exit_price = exit_price;
            t.direction = (position > 0) ? 1 : -1;
            t.contracts = std::abs(position);
            t.pnl = pnl;
            t.costs = costs;
            t.exit_reason = "daily_limit";
            result.trades.push_back(t);

            equity += pnl;
            daily_pnl += pnl;
            result.equity_curve.push_back(equity);

            position = 0;
            continue;
        }

        // Skip new entries if daily limit already hit
        if (daily_pnl <= -max_daily_drawdown) {
            continue;
        }

        // =================================================================
        // OPPOSITE-SIGNAL EXIT + NEW ENTRY (reversal)
        // =================================================================

        if (position > 0 && signals.sell_signal[i]) {
            // LONG position → Sell signal fires → EXIT long
            double exit_price = bars[i].close - (SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE);
            double pnl = (exit_price - entry_price) * contracts * MGC_POINT_VALUE;
            double costs = APEX_COST_RT * contracts;
            pnl -= costs;

            Trade t;
            t.entry_time = entry_time;
            t.exit_time = bars[i].timestamp;
            t.entry_price = entry_price;
            t.exit_price = exit_price;
            t.direction = 1;
            t.contracts = contracts;
            t.pnl = pnl;
            t.costs = costs;
            t.exit_reason = "opposite_signal_sell";
            result.trades.push_back(t);

            equity += pnl;
            daily_pnl += pnl;
            result.equity_curve.push_back(equity);

            // Immediately enter SHORT (reversal)
            entry_price = bars[i].close + (SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE);
            entry_time = bars[i].timestamp;
            entry_bar = (int)i;
            position = -contracts;

        } else if (position < 0 && signals.buy_signal[i]) {
            // SHORT position → Buy signal fires → EXIT short
            double exit_price = bars[i].close + (SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE);
            double pnl = (entry_price - exit_price) * contracts * MGC_POINT_VALUE;
            double costs = APEX_COST_RT * contracts;
            pnl -= costs;

            Trade t;
            t.entry_time = entry_time;
            t.exit_time = bars[i].timestamp;
            t.entry_price = entry_price;
            t.exit_price = exit_price;
            t.direction = -1;
            t.contracts = contracts;
            t.pnl = pnl;
            t.costs = costs;
            t.exit_reason = "opposite_signal_buy";
            result.trades.push_back(t);

            equity += pnl;
            daily_pnl += pnl;
            result.equity_curve.push_back(equity);

            // Immediately enter LONG (reversal)
            entry_price = bars[i].close + (SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE);
            entry_time = bars[i].timestamp;
            entry_bar = (int)i;
            position = contracts;

        } else if (position == 0) {
            // =================================================================
            // FLAT — LOOK FOR NEW ENTRY
            // =================================================================
            if (signals.buy_signal[i]) {
                entry_price = bars[i].close + (SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE);
                entry_time = bars[i].timestamp;
                entry_bar = (int)i;
                position = contracts;
            } else if (signals.sell_signal[i]) {
                entry_price = bars[i].close + (SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE);
                entry_time = bars[i].timestamp;
                entry_bar = (int)i;
                position = -contracts;
            }
        }

        // Update drawdown tracking
        if (equity > peak_equity) peak_equity = equity;
        double dd = peak_equity - equity;
        if (dd > max_dd) max_dd = dd;
    }

    // Close any open position at end of data
    if (position != 0) {
        double exit_price = bars.back().close;
        double slippage = SPREAD_SLIPPAGE_TICKS * MGC_TICK_SIZE;
        double pnl;

        if (position > 0) {
            exit_price -= slippage;
            pnl = (exit_price - entry_price) * contracts * MGC_POINT_VALUE;
        } else {
            exit_price += slippage;
            pnl = (entry_price - exit_price) * contracts * MGC_POINT_VALUE;
        }

        double costs = APEX_COST_RT * contracts;
        pnl -= costs;

        Trade t;
        t.entry_time = entry_time;
        t.exit_time = bars.back().timestamp;
        t.entry_price = entry_price;
        t.exit_price = exit_price;
        t.direction = (position > 0) ? 1 : -1;
        t.contracts = contracts;
        t.pnl = pnl;
        t.costs = costs;
        t.exit_reason = "end_of_data";
        result.trades.push_back(t);

        equity += pnl;
        result.equity_curve.push_back(equity);
    }

    // Record last day's daily PnL
    if (daily_pnl < 0) {
        max_daily_dd = std::min(max_daily_dd, daily_pnl);
    }

    // =================================================================
    // COMPUTE STATISTICS
    // =================================================================
    result.total_trades = (int)result.trades.size();
    result.winning_trades = 0;
    result.losing_trades = 0;
    result.gross_profit = 0.0;
    result.gross_loss = 0.0;
    result.total_pnl = 0.0;
    double total_holding = 0.0;

    for (const auto& t : result.trades) {
        result.total_pnl += t.pnl;
        if (t.pnl > 0) {
            result.winning_trades++;
            result.gross_profit += t.pnl;
        } else {
            result.losing_trades++;
            result.gross_loss += std::abs(t.pnl);
        }
        // Estimate holding bars (approximate from timestamps)
        total_holding += (double)(t.exit_time - t.entry_time);
    }

    result.win_rate = (result.total_trades > 0)
        ? (100.0 * result.winning_trades / result.total_trades)
        : 0.0;

    result.profit_factor = (result.gross_loss > 0)
        ? (result.gross_profit / result.gross_loss)
        : (result.gross_profit > 0 ? 999.99 : 0.0);

    result.avg_win = (result.winning_trades > 0)
        ? (result.gross_profit / result.winning_trades)
        : 0.0;

    result.avg_loss = (result.losing_trades > 0)
        ? (result.gross_loss / result.losing_trades)
        : 0.0;

    result.avg_holding_bars = (result.total_trades > 0)
        ? (total_holding / result.total_trades / 60.0)  // Convert seconds to minutes
        : 0.0;

    result.max_drawdown = max_dd;
    result.max_daily_drawdown = std::abs(max_daily_dd);

    return result;
}

// =============================================================================
// DATA LOADING
// =============================================================================

std::vector<Bar> load_csv(const std::string& filename) {
    std::vector<Bar> bars;
    std::ifstream file(filename);

    if (!file.is_open()) {
        std::cerr << "ERROR: Cannot open file: " << filename << std::endl;
        return bars;
    }

    std::string line;
    // Skip header
    std::getline(file, line);

    while (std::getline(file, line)) {
        if (line.empty()) continue;

        Bar b;
        std::stringstream ss(line);
        std::string token;

        std::getline(ss, token, ',');
        b.timestamp = std::stoll(token);

        std::getline(ss, token, ',');
        b.open = std::stod(token);

        std::getline(ss, token, ',');
        b.high = std::stod(token);

        std::getline(ss, token, ',');
        b.low = std::stod(token);

        std::getline(ss, token, ',');
        b.close = std::stod(token);

        bars.push_back(b);
    }

    return bars;
}

// =============================================================================
// REPORTING
// =============================================================================

void print_result(const BacktestResult& r, const std::string& label,
                  int contracts, double starting_equity) {
    std::cout << "\n" << std::string(70, '=') << "\n";
    std::cout << "  " << label << "\n";
    std::cout << std::string(70, '=') << "\n";

    std::cout << std::fixed << std::setprecision(2);
    std::cout << "  Contracts:          " << contracts << "\n";
    std::cout << "  Starting Equity:    $" << starting_equity << "\n";
    std::cout << "  Final Equity:       $" << (starting_equity + r.total_pnl) << "\n";
    std::cout << "  Total P&L:          $" << r.total_pnl << "\n";
    std::cout << "  Total Trades:       " << r.total_trades << "\n";
    std::cout << "  Winning Trades:     " << r.winning_trades << "\n";
    std::cout << "  Losing Trades:      " << r.losing_trades << "\n";
    std::cout << "  Win Rate:           " << r.win_rate << "%\n";
    std::cout << "  Profit Factor:      " << r.profit_factor << "\n";
    std::cout << "  Avg Win:            $" << r.avg_win << "\n";
    std::cout << "  Avg Loss:           $" << r.avg_loss << "\n";
    std::cout << "  Max Drawdown:       $" << r.max_drawdown << "\n";
    std::cout << "  Max Daily Drawdown: $" << r.max_daily_drawdown << "\n";
    std::cout << "  Avg Hold (min):     " << r.avg_holding_bars << "\n";

    double total_costs = 0;
    for (const auto& t : r.trades) total_costs += t.costs;
    std::cout << "  Total Costs:        $" << total_costs << "\n";
    std::cout << "  Cost/Trade:         $" << (r.total_trades > 0 ? total_costs / r.total_trades : 0.0) << "\n";
}

void print_trade_log(const BacktestResult& r, int max_trades = 20) {
    std::cout << "\n  --- Last " << std::min(max_trades, r.total_trades) << " Trades ---\n";
    std::cout << "  " << std::setw(12) << "Entry" << " "
              << std::setw(12) << "Exit" << " "
              << std::setw(6) << "Dir" << " "
              << std::setw(10) << "EntryPx" << " "
              << std::setw(10) << "ExitPx" << " "
              << std::setw(10) << "P&L" << " "
              << std::setw(18) << "Reason" << "\n";

    int start = std::max(0, r.total_trades - max_trades);
    for (int i = start; i < r.total_trades; ++i) {
        const auto& t = r.trades[i];
        std::cout << "  " << std::setw(12) << t.entry_time << " "
                  << std::setw(12) << t.exit_time << " "
                  << std::setw(6) << (t.direction > 0 ? "LONG" : "SHORT") << " "
                  << std::setw(10) << std::fixed << std::setprecision(2) << t.entry_price << " "
                  << std::setw(10) << t.exit_price << " "
                  << std::setw(10) << t.pnl << " "
                  << std::setw(18) << t.exit_reason << "\n";
    }
}

// =============================================================================
// MAIN
// =============================================================================

int main(int argc, char* argv[]) {
    if (argc < 2) {
        std::cout << "MGC Diamante Strategy - ATR Trailing Stop (Opposite-Signal Exit)\n";
        std::cout << "================================================================\n\n";
        std::cout << "Usage: " << argv[0] << " <data.csv> [key_value] [atr_period] [use_heikin_ashi] [contracts]\n\n";
        std::cout << "Arguments:\n";
        std::cout << "  data.csv        - OHLC CSV file (time,open,high,low,close)\n";
        std::cout << "  key_value       - ATR sensitivity multiplier (default: 1)\n";
        std::cout << "  atr_period      - ATR calculation period (default: 10)\n";
        std::cout << "  use_heikin_ashi - 0=normal candles, 1=Heikin Ashi (default: 0)\n";
        std::cout << "  contracts       - Number of MGC contracts (default: 1)\n\n";
        std::cout << "Example:\n";
        std::cout << "  " << argv[0] << " data/mgc_1min.csv 1 10 0 3\n";
        return 0;
    }

    std::string data_file = argv[1];
    int key_value       = (argc > 2) ? std::atoi(argv[2]) : 1;
    int atr_period      = (argc > 3) ? std::atoi(argv[3]) : 10;
    bool use_ha         = (argc > 4) ? (std::atoi(argv[4]) != 0) : false;
    int contracts       = (argc > 5) ? std::atoi(argv[5]) : 1;
    double start_equity = 150000.0;
    double max_daily_dd = 2000.0;

    std::cout << "MGC Diamante Strategy - Opposite-Signal Exit\n";
    std::cout << "=============================================\n";
    std::cout << "Data file:       " << data_file << "\n";
    std::cout << "Key Value:       " << key_value << "\n";
    std::cout << "ATR Period:      " << atr_period << "\n";
    std::cout << "Heikin Ashi:     " << (use_ha ? "Yes" : "No") << "\n";
    std::cout << "Contracts:       " << contracts << "\n";
    std::cout << "Starting Equity: $" << std::fixed << std::setprecision(0) << start_equity << "\n";
    std::cout << "Max Daily DD:    $" << max_daily_dd << "\n";
    std::cout << "Apex RT Cost:    $" << std::setprecision(2) << APEX_COST_RT << "/contract\n";
    std::cout << "Spread Slippage: " << SPREAD_SLIPPAGE_TICKS << " ticks/side\n\n";

    // Load data
    auto bars = load_csv(data_file);
    if (bars.empty()) {
        std::cerr << "No data loaded. Exiting.\n";
        return 1;
    }
    std::cout << "Loaded " << bars.size() << " bars\n";

    // Run backtest
    auto result = run_backtest(bars, key_value, atr_period, use_ha, contracts,
                                start_equity, max_daily_dd);

    // Print results
    std::string label = "MGC " + std::to_string(contracts) + " contracts | "
                      + "Key=" + std::to_string(key_value) + " ATR=" + std::to_string(atr_period)
                      + (use_ha ? " [Heikin Ashi]" : " [Normal]");
    print_result(result, label, contracts, start_equity);
    print_trade_log(result);

    return 0;
}
