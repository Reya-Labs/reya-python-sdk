from reya_data_feed.websocket_client import ReyaSocket
from examples.consume_data_feed import on_error
import asyncio
import random
from examples.trade_execution import execute_trade, getConfigs, MarketIds

market_ids = ["ETHUSD", "BTCUSD", "SOLUSD", "ARBUSD", "OPUSD", "AVAXUSD"]
global_price_payloads = {key: None for key in market_ids}
global_funding_rates = {key: None for key in market_ids}

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
        elif message["channel"] == 'funding-rates':
            global_funding_rates[message['id']] = message["contents"]
        else:
            pass

        # If all markets's data is assigned, trade can be executed
        is_data_assigned = all(value is not None for value in global_price_payloads.values(
        )) and all(value is not None for value in global_funding_rates.values())
        if is_data_assigned and decide_execution(global_price_payloads, global_funding_rates):
            # Execute trades if market conditions satify decide_execution conditions
            run_trades()


def _map_payloads(payload):
    payload['pricePayload']['price'] = int(payload['pricePayload']['price'])
    return payload


''' Mock function that decides if a trade should be executed based on current market conditions '''


def decide_execution(_, __):
    return random.random() < 0.1


''' Runs a trade on current market  '''


def run_trades():
    configs = getConfigs()
    execute_trade(
        configs=configs,
        base=-(10**18),
        price_limit=0,
        market_id=MarketIds.SOL.value,
        account_id=12,  # your margin account id
        current_core_nonce=72,  # sigature nonce of owner address stored in Reya Core
        price_payloads=map(_map_payloads, global_price_payloads.values())
    )


async def main():
    ws = ReyaSocket(os.environ['REYA_WS_URL'],
                    on_error=on_error, on_message=on_message_prices)
    await ws.connect()

if __name__ == "__main__":
    print("... Start")
    asyncio.run(main())
    print("... End")
