# Reya Python SDK
This repository contains Python examples of how to interact with the Reya ecosystem. It shows how to subscribe to the Reya Websocket for market data updates and how to execute on-chain actions (e.g. trade) via the RPC. 

For any questions or support, please open ticket on [discord](https://discord.com/invite/reyaxyz).

## Features
- Websocket connection for candles, prices and funding rates of the live Reya markets;
- Utilities for following key actions:
    - Executing trades on Reya DEX;
    - Depositing and withdrawing collateral from margin account;
    - Bridging USDC into Reya Network and bridging out to external chains (e.g. Arbitrum);
    - Staking and unstaking tokens from the Reya passive pool;
    - Updating underlying oracle prices to contribute for freshest execution prices;
 - Examples of how to use the SDK: 
    - Consume data feed updates from Reya Websocket;
    - Long and short trade executions on SOL-rUSD market;
    - Creating two margin accounts, A and B, depositing 1 rUSD into A, transfer it to account B and withdrawing it back from B to user's wallet;
    - Staking and unstaking 1 rUSD from the Reya passive pool;
    - Bridging in 1 USDC from Arbitrum and depositing it into margin account;
    - Withdrawing 1 rUSD from the margin account and bridge it out to Arbitrum.

## Get Started

Dependencies are managed with Poetry. To install Poetry, use the following command:

```bash
pipx install poetry
```
> **Note**: If `pipx` is not installed on your system, follow the [official installation guide](https://pipx.pypa.io/stable/installation/).

To create the shell dedicated to running the examples, run this from the root of the repository:
```bash
cd examples && poetry shell
poetry install --no-root
cd ..
```

Ensure you have the environmental variables set up (in .env file in the root of the repository). Find an example at `.env.example`.

To run any example file, run `python3 -m examples.<file_name>`, e.g.:
```bash
python3 -m examples.consume_data_feed 
```
or
```bash
python3 -m examples.trade_execution
```

## Contents
### Websocket client
The WebSocket client code found at `reya_data_feed/consumer.py` is designed to interact with the Reya WebSocket API. The client enables subscription and unsubscription to channels such as candles, spot prices, and funding rates, each supplying data of a specific Reya market.

At the time of writing, prices and candles updates are very fast, almost 500ms, whereas the funding rate is updated only every minute.

Find an example consumer at `examples/consume_data_feed.py`. To run, follow instructions from the "Get Started" section.

### Trade example
An example of executing on-chain trades on Reya DEX can be found at `examples/trade_execution.py`.

Prerequisites for calling it:
- Ensure your private key is included in the .env file as per the example.
- Ensure the chain_id is included in the .env file as per the example.
- Ensure you already have a Reya margin account funded with enough collateral and mentioned in the .env file. Create one using the built-in action or in the app [dashboard](https://app.reya.xyz).
- Ensure the account_id is included in the .env file as per the example.
- Ensure your wallet is funded with some ETH on Reya network to pay the gas fees. Find bridge [here](https://reya.network/bridge).
- Decide on the base value of the trade. A negative value means taking a short position, a positive one means a long position. The base is represented with 18 decimals precision. The base represents the units of exposure denoted in the underlying token of the market.
- Pick a price limit. The Price Limit can define the maximum allowable slippage of the trade. If the execution price exceeds this, the trade will revert. The price limit for a short trade must be lower than the pool price and vice-versa for a long trade. Price is represented with 18 decimals precision.

To run this example (`examples/trade_execution.py`), run from project root:

```bash
python3 -m examples.trade_execution
```

### Other Examples
Other similar examples can be found in the `examples` folder. They show use cases of the other actions developed in this SDK. Please check them out and find in-line documentation.