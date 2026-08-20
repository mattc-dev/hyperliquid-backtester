# Hyperliquid Backtester v0.1.0 ![Coverage](https://img.shields.io/badge/coverage-91%25-brightgreen)
A backtester for the trading platform Hyperliquid 

## Features
- Downloads historical counters for given ticker and asset.
- Extends a file of downloaded data with new data if already present.
- Clearly notes in the download file if there is no overlap between two downloads to prevent continuous execution.

## Installation
- `pip install -r requirements.txt`
- Run from `./client.py`

## Usage
- Modify the chosen asset and timeframe in the `symbol` and `tf` variables in the main execution block of `client.py`.

## Limitations
- As it uses candle downloads rather than tickers, it is impossible to know the exact price path within a candle. This is factored in to the project, as it will favour exits when the stop-loss is hit within a candle 100% of the time (even if TP is also hit), allowing a conservative estimate.
- Can only install as far back as ~5000 candles for each asset+timeframe.