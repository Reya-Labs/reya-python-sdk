# reya-python-examples
This repo contains Python examples of how to interact with the Reya ecosystem. It shows how to subscribe to the Reya Websocket for market data updates and how to execute on-chain trades via the RPC.

## Get Started

Dependencies are managed with Poetry. To install Poetry, use the following command:

```bash
pipx install poetry
```
> **Note**: If `pipx` is not installed on your system, follow the [official installation guide](https://pipx.pypa.io/stable/installation/).

To create the shell dedicated to running the examples, run this from the repo's root:
```bash
cd examples && poetry shell
poetry install --no-root
cd ..
```

Ensure you have the env variables set up. Find an example at `examples/.env.example`

To run any example file, run `python3 -m examples.<file_name>`, e.g.:
```python3 -m examples.consume_data_feed```

## Contents
### Websocket client
The WebSocket client code found at `reya_data_feed/consumer.py` is designed to interact with the Reya WebSocket API. The client enables subscription and unsubscription to channels such as candles, spot prices, and funding rates, each supplying data of a specific Reya market.

At the time of writing, prices and candles updates are very fast, almost 500ms, whereas the funding rate is updated only every minute.

Find an example consumer at `examples/consume_data_feed.py`. To run, follow instructions from the "Get Started" section.

### Trade example
An example of executing an on-chain trade on Reya DEX can be found at `examples/trade_execution.py`.

It shows how oracle updates can be appended to trades to ensure the latest prices are used. As the price staleness buffer is reduced, every interaction with the Reya Dex will require prepended price updates. 

The prices updates can be obtained from the websocket API as seen in `examples/trade_based_on_updates.py`. The updates contain the latest price, corresponding timestamp and the signed message from the trusted producer. The signature is verified against the given values on-chain.

Aggregating these oracle calls with the actual trade call requires routing via the [Multicall contract](https://explorer.reya.network/address/0xED28d27dFcA47AD2513C9f2e2d3C098C2eA5A47F?tab=contract). Thus, the message sender is not the user anymore and a signature is required to ensure the integrity of the trade information.

Prerequisites for calling `execute_trade()`:
- Ensure your private key is included in the .env file as per the example
- Ensure the chain_id is included in the .env file as per the example
- Ensure you already have a Reya margin account funded with enough collateral and mentioned in the .env file. Create one in the app [dashboard](https://app.reya.xyz). Examples of how to achieve this programmatically are coming soon.
- Ensure the account_id is included in the .env file as per the example
- Ensure your wallet is funded with some ETH on Reya network to pay the gas fees. Find bridge [here](https://reya.network/bridge).
- Decide on the base value of the trade. A negative value means taking a short position, a positive one means a long position. The base is represented with 18 decimals precision. The base represents the units of exposure denoted in the underlying token of the market
- Pick a price limit. The Price Limit can define the maximum allowable slippage of the trade. If the execution price exceeds this, the trade will revert. The price limit for a short trade must be lower than the pool price and vice-versa for a long trade. Price is represented with 18 decimals precision.
- List price update payloads for all markets (source is described above)

To run this example (`examples/trade_execution.py`), run from project root:

```bash
python3 -m examples.trade_execution
```


### Trigger trades based on price and funding rate updates
Some users might be interested in listening to market updates and executing a set of actions based on the observed data. This repo provides an example of this approach in `examples/trade_based_on_updates.py`. This script is continuously running, subscribing to the spot prices and the funding rates of all markets on Reya Dex and executing trades when some mock conditions are met. 

To make use of this example, adjust these two functions `decide_execution()` and `run_trades()`.
Follow the trade example instructions to adjust trading execution with the proper inputs.
Deciding when to execute a trade is up to the user.

To run this example (`examples/trade_based_on_updates.py`), run from project root:

```bash
python3 -m examples.trade_based_on_updates
```
