# Hyperliquid Backtester v0.2.0 ![Coverage](https://img.shields.io/badge/coverage-89%25-brightgreen)
A backtester for the trading platform Hyperliquid 

## Features
- Downloads historical candles for given ticker and asset.
- Extends a file of downloaded data with new data if already present.
- Clearly notes in the download file if there is no overlap between two downloads to prevent continuous execution.
- Models sub-second execution delays using a stochastic Brownian path approximation bounded by the candle's high and low.
- Accounts for maker/taker fees and position holding funding costs over the duration of a trade.

## Installation
- `pip install -r requirements.txt`
- Run from `./client.py` and `./engine.py`

## Usage
- Use `client.py` to pull candle data for a given symbol and timeframe:

```bash
python client.py --symbol ETH --tf 5m --out data/eth_5m.csv
```

- Use `engine.py` to run a backtest of a strategy:

```bash
python engine.py --data BTC_1m.csv --strategy strategy.json --out trades.csv
```

## Limitations
- As it uses candle downloads rather than tickers, it is impossible to know the exact price path within a candle. This is factored in to the project, as it will favour exits when the stop-loss is hit within a candle 100% of the time (even if TP is also hit), allowing a conservative estimate.
- Can only install as far back as ~5000 candles for each asset+timeframe.
- The current time delay is set to 2.5 milliseconds and the price movements within that time are generated as reasonable approximations using scaled volatility over time. In the future, real measurements using `ping` can be made to find variance and the sub-minute price action can be measured.
- Sub-candle price action relies on a Brownian path approximation rather than tick-level order book depth or true time-and-sales data. 
- Funding rate and fees are approximated as fixed strategy inputs and do not change dynamically with user activity or promotion events.
- There is currently no genuine operational difference between how makers and takers are filled. This can be resolved by having makers made prematurely and filled whenever the market is shown to move past them (with a chance of not being taken if made once the price crosses the boundary) and having takers execute immediately; for more Hyperliquid realism the takers should be made even if they are at a slightly different price to the one intended.

## Strategy JSON Schema
```JSON
{
  "params": {
    "fast_ma_period": 2,
    "slow_ma_period": 4
  },
  "execution": {
    "simulated_delay_ms": 2.5,
    "max_slippage_pct": 0.05
  },
  "position_size": {
    "value": 0.5
  },
  "leverage": 1,
  "account": {
    "initial_capital": 10000.0
  },
  "entry": {
    "order_type": "limit"
  },
  "exit": {
    "order_type": "market",
    "stop_loss_pct": 1.0,
    "take_profit_pct": 2.0
  },
  "fees": {
    "maker_pct": 0.02,
    "taker_pct": 0.05
  },
  "funding": {
    "period_hours": 8,
    "rate_pct_per_period": 0.01
  }
}
```

## Testing
You can verify the coverage level for yourself:

```bash
pytest --cov=engine --cov=client tests/
```

