import argparse
import os
import time
import pandas as pd
import requests

API = "https://api.hyperliquid.xyz/info"

intervals = {
    "1m": 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


def get_candles(coin="BTC", interval="1m"):
    # fetch the data from hyperliquid
    step = intervals.get(interval, 60000)
    candles = []
    start = 0
    end = int(time.time() * 1000)

    while start < end:
        payload = {
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": start,
                "endTime": end,
            },
        }

        try:
            res = requests.post(API, json=payload, timeout=10)
            res.raise_for_status()
            data = res.json()
        except Exception as e:
            print("Error:", e)
            break

        if not data:
            break

        candles.extend(data)
        last = data[-1]["t"]
        if last <= start:
            break

        start = last + step
        print(f"Candles obtained thus far: {len(candles)}")
        time.sleep(0.05)

    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles)
    df = df.rename(
        columns={
            "t": "timestamp_open",
            "T": "timestamp_close",
            "s": "symbol",
            "i": "interval",
            "o": "open",
            "h": "high",
            "l": "low",
            "c": "close",
            "v": "volume",
            "n": "trades",
        }
    )

    df["datetime"] = pd.to_datetime(df["timestamp_open"], unit="ms", utc=True)
    return df[["datetime", "open", "high", "low", "close", "volume", "trades"]]


def save_in_csv(df, symbol, tf, out_filename=None):
    # save the downloaded candles into a csv
    # if the csv already exists, merely add to it
    filename = out_filename if out_filename else f"{symbol}_{tf}.csv"

    if os.path.exists(filename):
        existing = pd.read_csv(filename, comment="#")
        existing["datetime"] = pd.to_datetime(existing["datetime"], utc=True)

        if not set(df["datetime"]).isdisjoint(set(existing["datetime"])):
            df = (
                pd.concat([existing, df])
                .drop_duplicates(subset=["datetime"])
                .sort_values("datetime")
                .reset_index(drop=True)
            )
            df.to_csv(filename, index=False)
        else:
            with open(filename, "a") as f:
                f.write("\n# Gap detected\n")
            df.to_csv(filename, mode="a", header=False, index=False)
    else:
        df.to_csv(filename, index=False)

    print("Saved to", filename)


def main():
    parser = argparse.ArgumentParser(description="Fetch historical candles from Hyperliquid")
    parser.add_argument("--symbol", default="BTC", help="Asset ticker symbol (e.g. BTC, ETH)")
    parser.add_argument("--tf", default="1m", choices=list(intervals.keys()), help="Candle timeframe interval")
    parser.add_argument("--out", default=None, help="Custom output CSV file path")

    args = parser.parse_args()

    df = get_candles(args.symbol, args.tf)
    print("fetched", len(df))

    if not df.empty:
        save_in_csv(df, args.symbol, args.tf, out_filename=args.out)


if __name__ == "__main__":
    main()