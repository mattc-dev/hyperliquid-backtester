from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import requests

from client import get_candles, intervals, save_in_csv


def make_candle(t, step=60000):
    return {
        "t": t,
        "T": t + step,
        "s": "BTC",
        "i": "1m",
        "o": "100",
        "h": "105",
        "l": "95",
        "c": "102",
        "v": "10",
        "n": 5,
    }


def mock_res(data, ok=True):
    res = MagicMock()
    res.json.return_value = data
    if ok:
        res.raise_for_status.return_value = None
    else:
        res.raise_for_status.side_effect = requests.HTTPError("error")
    return res


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_cols(mock_time, mock_post):
    mock_time.return_value = 60.06
    candle = make_candle(0)
    mock_post.return_value = mock_res([candle])

    df = get_candles("BTC", "1m")

    assert list(df.columns) == [
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trades",
    ]
    assert df.iloc[0]["open"] == "100"
    assert pd.api.types.is_datetime64_any_dtype(df["datetime"])


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_utc(mock_time, mock_post):
    mock_time.return_value = 60.06
    candle = make_candle(1700000000000)
    mock_post.return_value = mock_res([candle])

    df = get_candles("BTC", "1m")

    expected = pd.to_datetime(1700000000000, unit="ms", utc=True)
    assert df.iloc[0]["datetime"] == expected


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_pagination(mock_time, mock_post):
    mock_time.return_value = 300.0
    p1 = [make_candle(0), make_candle(60000)]
    p2 = [make_candle(180000)]
    mock_post.side_effect = [mock_res(p1), mock_res(p2), mock_res([])]

    df = get_candles("BTC", "1m")

    assert len(df) == 3
    assert mock_post.call_count == 3


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_stuck_loop(mock_time, mock_post):
    mock_time.return_value = 60.06
    candle = make_candle(0)
    mock_post.return_value = mock_res([candle])

    df = get_candles("BTC", "1m")

    assert mock_post.call_count == 1
    assert len(df) == 1


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_empty(mock_time, mock_post):
    mock_time.return_value = 60.06
    mock_post.return_value = mock_res([])

    df = get_candles("BTC", "1m")

    assert df.empty
    assert mock_post.call_count == 1


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_error_partial(mock_time, mock_post):
    mock_time.return_value = 180.06
    p1 = [make_candle(0)]
    mock_post.side_effect = [mock_res(p1), requests.ConnectionError()]

    df = get_candles("BTC", "1m")

    assert len(df) == 1


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_http_error(mock_time, mock_post):
    mock_time.return_value = 60.06
    mock_post.return_value = mock_res([{}], ok=False)

    df = get_candles("BTC", "1m")

    assert df.empty


@patch("client.requests.post")
@patch("client.time.time")
def test_get_candles_timeout(mock_time, mock_post):
    mock_time.return_value = 60.06
    mock_post.side_effect = requests.Timeout()

    df = get_candles("BTC", "1m")

    assert isinstance(df, pd.DataFrame)
    assert df.empty


@pytest.mark.parametrize(
    "tf,expected",
    [
        ("1m", 60000),
        ("5m", 300000),
        ("15m", 900000),
        ("1h", 3600000),
        ("4h", 14400000),
        ("1d", 86400000),
    ],
)
def test_intervals(tf, expected):
    assert intervals[tf] == expected


def test_intervals_fallback():
    assert intervals.get("invalid", 60000) == 60000


def make_df(dates):
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(dates, utc=True),
            "open": [100] * len(dates),
            "high": [105] * len(dates),
            "low": [95] * len(dates),
            "close": [102] * len(dates),
            "volume": [10] * len(dates),
            "trades": [5] * len(dates),
        }
    )


def test_save_new_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = make_df(["2024-01-01T00:00:00Z"])

    save_in_csv(df, "BTC", "1m")

    filepath = tmp_path / "BTC_1m.csv"
    assert filepath.exists()
    res = pd.read_csv(filepath)
    assert len(res) == 1


def test_save_merge_overlapping(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df1 = make_df(["2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z"])
    save_in_csv(df1, "BTC", "1m")

    df2 = make_df(["2024-01-01T00:01:00Z", "2024-01-01T00:02:00Z"])
    save_in_csv(df2, "BTC", "1m")

    res = pd.read_csv(tmp_path / "BTC_1m.csv")
    assert len(res) == 3


def test_save_gap_detected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df1 = make_df(["2024-01-01T00:00:00Z"])
    save_in_csv(df1, "BTC", "1m")

    df2 = make_df(["2024-01-02T00:00:00Z"])
    save_in_csv(df2, "BTC", "1m")

    file = tmp_path / "BTC_1m.csv"
    text = file.read_text()
    assert "# Gap detected" in text

    res = pd.read_csv(file, comment="#")
    assert len(res) == 2


def test_save_sorts_datetime(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df1 = make_df(["2024-01-01T00:05:00Z", "2024-01-01T00:03:00Z"])
    save_in_csv(df1, "BTC", "1m")

    df2 = make_df(["2024-01-01T00:03:00Z", "2024-01-01T00:01:00Z"])
    save_in_csv(df2, "BTC", "1m")

    res = pd.read_csv(tmp_path / "BTC_1m.csv")
    times = pd.to_datetime(res["datetime"])
    assert list(times) == sorted(times)