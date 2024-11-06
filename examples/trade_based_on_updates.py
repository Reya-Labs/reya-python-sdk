from reya_data_feed.consumer import ReyaSocket
from examples.consume_data_feed import on_error
import asyncio
import random
from examples.utils.trade import execute_trade, getConfigs

import os
from dotenv import load_dotenv
import argparse

# Note: the list of markets keeps updating, please check with the team for the updated ids
market_ids = ["ETHUSDMARK", "BTCUSDMARK", "SOLUSDMARK", "ARBUSDMARK", "OPUSDMARK", "AVAXUSDMARK", "MKRUSDMARK", "LINKUSDMARK", "AAVEUSDMARK", "CRVUSDMARK", "UNIUSDMARK", "SUIUSDMARK", "TIAUSDMARK", "SEIUSDMARK", "ZROUSDMARK", "XRPUSDMARK", "WIFUSDMARK", "1000PEPEUSDMARK"]
global_price_payloads = {key: None for key in market_ids}
global_funding_rates = {key: None for key in market_ids}

current_nonce = 1

'''Listen to price changes and funding rate changes and trade based on this information'''


def on_message_prices(ws: ReyaSocket, message: dict):
    if message["type"] == "connected":
        print("Connected")
        for market_id in market_ids:
            ws.prices.subscribe(id=market_id)
            ws.funding_rates.subscribe(id=market_id)

    if message["type"] == "channel_data":
        # Store prices and funding rates
        if message["channel"] == 'prices':
            global_price_payloads[message['id']] = message["contents"]
        # Note: funding-rates update every 30 seconds
        # elif message["channel"] == 'funding-rates':
        #     global_funding_rates[message['id']] = message["contents"]
        else:
            pass

        # If all markets's data is assigned, trade can be executed
        is_data_assigned = all(value is not None for value in global_price_payloads.values()) 
            # and all(value is not None for value in global_funding_rates.values())
        if is_data_assigned and decide_execution(global_price_payloads, global_funding_rates):
            # Execute trades if market conditions satify decide_execution conditions
            run_trades()


def _map_payloads(payload):
    payload['signedPrice']['pricePayload']['price'] = int(payload['signedPrice']['pricePayload']['price'])
    return payload['signedPrice']


''' Mock function that decides if a trade should be executed based on current market conditions '''


def decide_execution(_, __):
    return random.random() < 0.1


''' Runs a trade on current market  '''


def run_trades():
    configs = getConfigs()
    global current_nonce
    success = execute_trade(
        configs=configs,
        base= < REPLACE_ME > , # e.g. -10**18 for short trade, WAD precision
        price_limit= < REPLACE_ME >, # e.g. 2678 * (10 **18), WAD precision
        market_id= < REPLACE_ME > , # e.g. MarketIds.SOL.value,
        account_id=configs['account_id'],  # your margin account id
        # sigature nonce of owner address stored in Reya Core
        current_core_nonce=current_nonce,
        price_payloads=list(map(_map_payloads, global_price_payloads.values()))
    )
    # incrememnt nonce aftre every successful trade
    current_nonce += 1 if success else 0


async def main():
    load_dotenv()
    ws = ReyaSocket(os.environ['REYA_WS_URL'],
                    on_error=on_error, on_message=on_message_prices)
    await ws.connect()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Example script for demonstration.")
    parser.add_argument('--current-nonce', type=str, default="1",
                        help="Current nonce of the margin account owner, tracked in Core")
    args = parser.parse_args()
    current_nonce = int(args.current_nonce)

    print("... Start")
    asyncio.run(main())
    print("... End")
