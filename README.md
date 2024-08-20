# reya-python-SDK
This repo contains Python examples for interacting with the Reya ecosystem

## Get Started

Dependencies are managed with Poetry. Install using: `pipx install poetry`
For `pipx`, follow official installation guide https://pipx.pypa.io/stable/installation/

To create the shell dediacted to running the examples, run this from the repo's root:
`cd examples && poetry shell && cd ..`

Ensure you have the env variables set up. Find example at `examples/.env.example`

To run any example file, run: `python3 -m examples.<file_name>`, e.g. `python3 -m examples.consume_data_feed`

## Contents
### Websocket client
The WebSocket client code found at `reya_data_feed/websocket_client.py` is designed for interacting with the Reya WebSocket API. The client enables subscription and unsubscription to different channels such as candles, spot prices, and funding-rates each supplying data of a specific Reya market. The list of supported markets is ETHUSD, BTCUSD, SOLUSD, ARBUSD, OPUSD, AVAXUSD

At the time of writing, prices and candles updates are very fast, almost 500ms, whereas the funding rate is updated only every minute.

Find an example consumer at `examples/consume_data_feed.py`

### Trade example
An example of executing an on-chain trade on Reya DEX can be found at `examples/trade_execution.py`.

It shows how oracle updates can be appended to trades to ensure the latest prices. As the price staleness buffer is reduced, every interaction with the Reya Dex will require prependded price updates. 

The prices updates can be obtained from the websocket API as seen in `examples/trade_based_on_updates.py`. They contain the latest price, corresponding timestamp and the signed message from the trusted producer. The signature is verified against the given values on-chain.

Aggregating these oracle calls with the actual trade call requires routing via the Multicall contract [https://www.multicall3.com/]. Thus, the message sender is not the user anymore and a signature is required to ensure the integrity of the trade information.

Calling the `execute_trade` frunction requires some infomarion:
- Ensure your private key is included in the .env file
- Ensure the contract addresses and chain_id are included in the .env file as per the example
- Ensure you already have a Reya margin account funded with enough collateral. Create one in the app dashboard: https://app.reya.xyz/. Examples of how to achieve this programatically are coming soon.
- Decide on the base value of the trade. A negative value means taking a short position, a positive one means a long position. The base is represented with 18 decimals presicion. The base represents the units of exposure denoted in the underlying token of the market
- Pick a price limit. If the execution price exceeds this, the trade will revert. The price limit for a short trade must be lower than the pool price and vice-versa for a long trade
- Specify your margin account id and nonce. As a temporary hack, you can see these when attempting to execute a trade on the app. Your wallet will prompt you to sign a message and will show the account id and the next nonce (your current nonce is 1 less)
- Ensure you listed price update payloads for all markets (source is described above)


### Trigger trades based on price and funding rate updates
Some users are interested in listening to market updates and executing a set of actions based on the observed data. This repo provides an example of this approach in `examples/trade_based_on_updates.py`. This script is contiously running, subscribing to the spot prices and the funding rates of all markets on Reya Dex and executing trades when some mock conditions are met. 

To make use of this example, the `decide_execution()` and `run_trades()` functions need changing.
