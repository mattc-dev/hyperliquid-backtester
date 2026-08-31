"""
engine.py

Reads a strategy definition (JSON) and runs it against OHLCV bar data,
simulating:
  1. A fixed execution delay (default 2.5ms, from strategy.json) between
     signal generation and order arrival at the "exchange".
  2. Price movement during that delay, approximated as a random walk
     confined to the bar's realized high-low range.
  3. Liquidity-gated fills: an order only fills if the simulated arrival
     price actually reaches the order's target price.
"""

import argparse
import json
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------
# Data + strategy loading
# ---------------------------------------------------------------------

def load_data(csv_path: str) -> pd.DataFrame:
    records = pd.read_csv(csv_path, parse_dates=["datetime"])
    return records.sort_values("datetime").reset_index(drop=True)


def load_strategy(json_path: str) -> dict:
    with open(json_path, "r") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------

def compute_signals(df: pd.DataFrame, strategy: dict) -> pd.DataFrame:
    short_window = strategy["params"]["fast_ma_period"]
    long_window = strategy["params"]["slow_ma_period"]

    records = df.copy()
    records["fast_ma"] = records["close"].rolling(short_window).mean()
    records["slow_ma"] = records["close"].rolling(long_window).mean()

    records["fast_above_slow"] = records["fast_ma"] > records["slow_ma"]
    prev_fast_above_slow = records["fast_above_slow"].shift(1).fillna(False)

    # Use explicit logical operations to avoid Python 3.13 deprecation warnings
    records["entry_signal"] = records["fast_above_slow"] & np.logical_not(prev_fast_above_slow)
    records["exit_signal"] = np.logical_not(records["fast_above_slow"]) & prev_fast_above_slow

    return records


# ---------------------------------------------------------------------
# Intra-bar price path approximation
# ---------------------------------------------------------------------

def sample_price_after_delay(open_p: float, high: float, low: float, close: float,
                             bar_duration_s: float, delay_ms: float,
                             rng: np.random.Generator, n_steps: int = 200) -> float:
    time_ratio = min((delay_ms / 1000.0) / bar_duration_s, 1.0)
    target_idx = max(int(n_steps * time_ratio), 1)

    time_steps = np.linspace(0, 1, n_steps + 1)
    baseline_trajectory = open_p + (close - open_p) * time_steps

    price_span = max(high - low, 1e-8)
    volatility_scale = price_span / 4.0
    stochastic_noise = rng.normal(0, volatility_scale, size=n_steps + 1)

    synthetic_path = np.clip(baseline_trajectory + stochastic_noise, low, high)
    synthetic_path[0] = open_p

    return float(synthetic_path[target_idx])


# ---------------------------------------------------------------------
# Liquidity-gated fill check
# ---------------------------------------------------------------------

def check_fill(order_side: str, target_price: float, arrival_price: float,
               max_slippage_pct: float) -> tuple[bool, float | None]:
    max_variance = target_price * (max_slippage_pct / 100.0)

    if order_side == "long":
        if arrival_price <= target_price + max_variance:
            return True, arrival_price
        return False, None
    else:
        if arrival_price >= target_price - max_variance:
            return True, arrival_price
        return False, None


# ---------------------------------------------------------------------
# Backtest loop
# ---------------------------------------------------------------------

def compute_fee(notional: float, order_type: str, fees: dict) -> float:
    fee_rate = fees["maker_pct"] if order_type == "limit" else fees["taker_pct"]
    return notional * (fee_rate / 100.0)


def compute_funding(notional: float, funding: dict, minutes_held: float) -> float:
    interval_mins = funding["period_hours"] * 60.0
    minute_rate = (funding["rate_pct_per_period"] / 100.0) / interval_mins
    return notional * minute_rate * minutes_held


def check_sl_tp(entry_price: float, high: float, low: float,
                sl_pct: float, tp_pct: float) -> str:
    stop_threshold = entry_price * (1 - sl_pct / 100.0)
    target_threshold = entry_price * (1 + tp_pct / 100.0)

    stop_breached = low <= stop_threshold
    target_breached = high >= target_threshold

    if stop_breached and target_breached:
        return "sl"
    if stop_breached:
        return "sl"
    if target_breached:
        return "tp"
    return "none"


def run_backtest(df: pd.DataFrame, strategy: dict, seed: int = 42) -> pd.DataFrame:
    rng_engine = np.random.default_rng(seed)

    latency_ms = strategy["execution"]["simulated_delay_ms"]
    slippage_cap = strategy["execution"]["max_slippage_pct"]
    position_scale = strategy["position_size"]["value"]
    sl_threshold = strategy["exit"].get("stop_loss_pct")
    tp_threshold = strategy["exit"].get("take_profit_pct")

    starting_equity = strategy["account"]["initial_capital"]
    multiplier = strategy["leverage"]
    fee_rules = strategy["fees"]
    funding_rules = strategy["funding"]

    evaluated_bars = compute_signals(df, strategy)

    trade_ledger = []
    active_trade = False
    open_rate = None
    notional_value = None
    borrowed_cost_tally = 0.0

    for idx in range(1, len(evaluated_bars)):
        bar = evaluated_bars.iloc[idx]
        bar_length = 60.0

        if active_trade:
            borrowed_cost_tally += compute_funding(notional_value, funding_rules, minutes_held=1.0)

        if not active_trade and bar["entry_signal"]:
            quoted_price = bar["close"]
            simulated_execution = sample_price_after_delay(
                bar["open"], bar["high"], bar["low"], bar["close"],
                bar_length, latency_ms, rng_engine
            )
            matched, executed_price = check_fill("long", quoted_price, simulated_execution, slippage_cap)

            if matched:
                active_trade = True
                open_rate = executed_price
                margin_commited = starting_equity * position_scale
                notional_value = margin_commited * multiplier
                borrowed_cost_tally = 0.0
                entry_commission = compute_fee(notional_value, strategy["entry"]["order_type"], fee_rules)

                trade_ledger.append({
                    "datetime": bar["datetime"], "action": "entry",
                    "target_price": quoted_price, "arrival_price": simulated_execution,
                    "filled": True, "fill_price": executed_price, "exit_reason": None,
                    "notional": notional_value, "fee": entry_commission, "funding": None, "pnl_pct": None
                })
            else:
                trade_ledger.append({
                    "datetime": bar["datetime"], "action": "entry_missed",
                    "target_price": quoted_price, "arrival_price": simulated_execution,
                    "filled": False, "fill_price": None, "exit_reason": None,
                    "notional": None, "fee": None, "funding": None, "pnl_pct": None
                })
            continue

        if active_trade and sl_threshold is not None and tp_threshold is not None:
            bracket_status = check_sl_tp(open_rate, bar["high"], bar["low"], sl_threshold, tp_threshold)
            if bracket_status != "none":
                executed_price = open_rate * (1 - sl_threshold / 100.0) if bracket_status == "sl" else open_rate * (1 + tp_threshold / 100.0)
                exit_commission = compute_fee(notional_value, strategy["exit"]["order_type"], fee_rules)

                raw_yield = (executed_price - open_rate) / open_rate * position_scale * multiplier
                overhead_yield = (exit_commission + borrowed_cost_tally) / starting_equity
                net_yield = raw_yield - overhead_yield

                trade_ledger.append({
                    "datetime": bar["datetime"], "action": "exit",
                    "target_price": executed_price, "arrival_price": executed_price,
                    "filled": True, "fill_price": executed_price, "pnl_pct": net_yield,
                    "exit_reason": bracket_status, "notional": notional_value,
                    "fee": exit_commission, "funding": borrowed_cost_tally
                })
                active_trade = False
                open_rate = None
                continue

        if active_trade and bar["exit_signal"]:
            quoted_price = bar["close"]
            simulated_execution = sample_price_after_delay(
                bar["open"], bar["high"], bar["low"], bar["close"],
                bar_length, latency_ms, rng_engine
            )
            matched, executed_price = check_fill("short", quoted_price, simulated_execution, slippage_cap)

            if matched:
                active_trade = False
                exit_commission = compute_fee(notional_value, strategy["exit"]["order_type"], fee_rules)

                raw_yield = (executed_price - open_rate) / open_rate * position_scale * multiplier
                overhead_yield = (exit_commission + borrowed_cost_tally) / starting_equity
                net_yield = raw_yield - overhead_yield

                trade_ledger.append({
                    "datetime": bar["datetime"], "action": "exit",
                    "target_price": quoted_price, "arrival_price": simulated_execution,
                    "filled": True, "fill_price": executed_price, "pnl_pct": net_yield,
                    "exit_reason": "signal", "notional": notional_value,
                    "fee": exit_commission, "funding": borrowed_cost_tally
                })
                open_rate = None
            else:
                trade_ledger.append({
                    "datetime": bar["datetime"], "action": "exit_missed",
                    "target_price": quoted_price, "arrival_price": simulated_execution,
                    "filled": False, "fill_price": None, "exit_reason": None,
                    "notional": None, "fee": None, "funding": None, "pnl_pct": None
                })

    return pd.DataFrame(trade_ledger)


def main() -> None:
    cli_parser = argparse.ArgumentParser(description="Run a strategy backtest with simulated execution delay and liquidity-gated fills")
    cli_parser.add_argument("--data", required=True, help="Path to OHLCV CSV")
    cli_parser.add_argument("--strategy", required=True, help="Path to strategy JSON")
    cli_parser.add_argument("--out", default="trades.csv", help="Where to write trade log")
    args = cli_parser.parse_args()

    market_df = load_data(args.data)
    strategy_params = load_strategy(args.strategy)

    results_df = run_backtest(market_df, strategy_params)
    results_df.to_csv(args.out, index=False)

    total_execs = results_df["filled"].sum() if "filled" in results_df and not results_df.empty else 0
    total_rejections = (~results_df["filled"]).sum() if "filled" in results_df and not results_df.empty else 0
    print(f"Trades logged: {len(results_df)} | filled: {total_execs} | missed (liquidity/slippage): {total_rejections}")
    print(f"Trade log written to {args.out}")

    if "pnl_pct" in results_df and not results_df.empty:
        net_performance = results_df["pnl_pct"].sum()
        sum_commissions = results_df["fee"].sum() if "fee" in results_df else 0
        sum_funding = results_df["funding"].sum() if "funding" in results_df else 0
        print(f"Total PnL (net of fees + funding): {net_performance:.4%}")
        print(f"Total fees paid: {sum_commissions:.4f} | Total funding paid: {sum_funding:.4f}")


if __name__ == "__main__":
    main()