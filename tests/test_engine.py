import json
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from engine import (
    check_fill,
    check_sl_tp,
    compute_fee,
    compute_funding,
    compute_signals,
    load_data,
    load_strategy,
    main,
    run_backtest,
    sample_price_after_delay,
)


@pytest.fixture
def sample_strategy():
    return {
        "params": {"fast_ma_period": 2, "slow_ma_period": 4},
        "execution": {"simulated_delay_ms": 2.5, "max_slippage_pct": 0.05},
        "position_size": {"value": 0.5},
        "account": {"initial_capital": 10000.0},
        "leverage": 2.0,
        "entry": {"order_type": "limit"},
        "exit": {"order_type": "market", "stop_loss_pct": 2.0, "take_profit_pct": 4.0},
        "fees": {"maker_pct": 0.015, "taker_pct": 0.045},
        "funding": {"period_hours": 1.0, "rate_pct_per_period": 0.1},
    }


@pytest.fixture
def sample_ohlcv_data():
    dates = pd.date_range("2024-01-01T00:00:00Z", periods=10, freq="1min")
    # Prices crafted to simulate cross-overs and volatility
    prices = [100.0, 101.0, 102.0, 105.0, 104.0, 102.0, 99.0, 98.0, 101.0, 103.0]
    return pd.DataFrame(
        {
            "datetime": dates,
            "open": prices,
            "high": [p + 1.0 for p in prices],
            "low": [p - 1.0 for p in prices],
            "close": prices,
            "volume": [10.0] * 10,
            "trades": [5] * 10,
        }
    )


def test_load_data(tmp_path):
    file_path = tmp_path / "test_data.csv"
    data = (
        "datetime,open,high,low,close,volume,trades\n"
        "2024-01-01 00:01:00,101,102,100,101.5,10,5\n"
        "2024-01-01 00:00:00,100,105,95,102,10,5\n"
    )
    file_path.write_text(data)

    df = load_data(str(file_path))

    assert len(df) == 2
    assert df.iloc[0]["open"] == 100  # Verifies sorting by datetime
    assert pd.api.types.is_datetime64_any_dtype(df["datetime"])


def test_load_strategy(tmp_path, sample_strategy):
    file_path = tmp_path / "strategy.json"
    file_path.write_text(json.dumps(sample_strategy))

    strategy = load_strategy(str(file_path))
    assert strategy == sample_strategy


def test_compute_signals(sample_ohlcv_data, sample_strategy):
    df = compute_signals(sample_ohlcv_data, sample_strategy)

    assert "fast_ma" in df.columns
    assert "slow_ma" in df.columns
    assert "entry_signal" in df.columns
    assert "exit_signal" in df.columns
    assert df["entry_signal"].dtype == bool
    assert df["exit_signal"].dtype == bool


def test_sample_price_after_delay():
    rng = np.random.default_rng(42)
    price = sample_price_after_delay(
        open_p=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        bar_duration_s=60.0,
        delay_ms=2.5,
        rng=rng,
    )

    assert 90.0 <= price <= 110.0
    assert isinstance(price, float)


@pytest.mark.parametrize(
    "side, target, arrival, max_slip, expected_filled, expected_price",
    [
        ("long", 100.0, 100.04, 0.05, True, 100.04),  # Buy fill within tolerance
        ("long", 100.0, 100.10, 0.05, False, None),  # Buy fill rejected (excess slippage)
        ("short", 100.0, 99.96, 0.05, True, 99.96),   # Sell fill within tolerance
        ("short", 100.0, 99.90, 0.05, False, None),   # Sell fill rejected (excess slippage)
    ],
)
def test_check_fill(side, target, arrival, max_slip, expected_filled, expected_price):
    filled, fill_price = check_fill(side, target, arrival, max_slip)
    assert filled == expected_filled
    assert fill_price == expected_price


def test_compute_fee(sample_strategy):
    fees = sample_strategy["fees"]
    notional = 10000.0

    # Limit order (Maker)
    maker_fee = compute_fee(notional, "limit", fees)
    assert maker_fee == pytest.approx(1.5)

    # Market order (Taker)
    taker_fee = compute_fee(notional, "market", fees)
    assert taker_fee == pytest.approx(4.5)


def test_compute_funding(sample_strategy):
    funding_config = sample_strategy["funding"]
    notional = 10000.0
    
    # Rate is 0.1% per 60 min -> 10 / 60 = ~0.1667 per minute for 10,000 notional
    cost = compute_funding(notional, funding_config, minutes_held=60.0)
    assert cost == pytest.approx(10.0)


@pytest.mark.parametrize(
    "high, low, sl_pct, tp_pct, expected",
    [
        (101.0, 99.0, 2.0, 4.0, "none"),  # Neither hit (range 99 - 101)
        (101.0, 97.5, 2.0, 4.0, "sl"),    # Stop-loss hit (SL price 98.0)
        (105.0, 99.0, 2.0, 4.0, "tp"),    # Take-profit hit (TP price 104.0)
        (105.0, 97.0, 2.0, 4.0, "sl"),    # Both hit (conservative policy resolves to SL)
    ],
)
def test_check_sl_tp(high, low, sl_pct, tp_pct, expected):
    entry_price = 100.0
    result = check_sl_tp(entry_price, high, low, sl_pct, tp_pct)
    assert result == expected


def test_run_backtest_full_flow(sample_ohlcv_data, sample_strategy):
    trades_df = run_backtest(sample_ohlcv_data, sample_strategy)

    assert isinstance(trades_df, pd.DataFrame)
    assert "datetime" in trades_df.columns
    assert "action" in trades_df.columns
    assert "filled" in trades_df.columns


def test_run_backtest_slippage_miss(sample_ohlcv_data, sample_strategy):
    # Set slippage tolerance to 0 to trigger missed fills
    sample_strategy["execution"]["max_slippage_pct"] = 0.000001
    trades_df = run_backtest(sample_ohlcv_data, sample_strategy)

    if not trades_df.empty:
        actions = trades_df["action"].tolist()
        assert "entry_missed" in actions or "exit_missed" in actions


@patch("engine.argparse.ArgumentParser.parse_args")
@patch("engine.load_data")
@patch("engine.load_strategy")
@patch("engine.run_backtest")
def test_main(mock_run_backtest, mock_load_strategy, mock_load_data, mock_parse_args, tmp_path):
    out_file = tmp_path / "trades.csv"
    mock_parse_args.return_value.data = "dummy.csv"
    mock_parse_args.return_value.strategy = "dummy.json"
    mock_parse_args.return_value.out = str(out_file)

    mock_load_data.return_value = pd.DataFrame()
    mock_load_strategy.return_value = {}
    mock_run_backtest.return_value = pd.DataFrame(
        [
            {
                "datetime": "2024-01-01",
                "action": "entry",
                "filled": True,
                "pnl_pct": 0.02,
                "fee": 1.5,
                "funding": 0.2,
            }
        ]
    )

    main()

    assert out_file.exists()