from examples.utils.consts import MarketPriceStreams, MarketTickers, MarketIds
from reya_data_feed.consumer import ReyaSocket
import asyncio
import os
from dotenv import load_dotenv

market_ids = [ticker.value for ticker in MarketIds]
tickers = [ticker.value for ticker in MarketTickers]
market_price_streams = [ticker.value for ticker in MarketPriceStreams]

candles = {key: None for key in market_ids}
funding_rates = {key: None for key in market_price_streams}
prices = {key: None for key in market_price_streams}

def on_error(_, message):
    print("Error in handling message:", message)
    exit(1)

def on_message_candles(ws: ReyaSocket, message: dict):
    if message["type"] == "connected":
        for ticker in tickers:
            ws.candles.subscribe(id=ticker)
    if message["type"] == "channel_batch_data":
        print(message)

        for content in message["contents"]:
            market_id = int(content["market_id"])
            candles[market_id] = content

        umarkets_ids = list(filter(lambda x: candles[x] is None, market_ids))
        print("Uninitialized candles:", len(umarkets_ids), umarkets_ids)
        print()

def on_message_funding_rates(ws: ReyaSocket, message: dict):
    print(message)
    if message["type"] == "connected":
        print("Connected")
        for price_stream in market_price_streams:
            ws.funding_rates.subscribe(id=price_stream)
    if message["type"] == "channel_data":
        print(message)

        funding_rates[message["id"]] = message["contents"]

        uprice_streams = list(filter(lambda x: funding_rates[x] is None, market_price_streams))
        print("Uninitialized funding rates:", len(uprice_streams), uprice_streams)
        print()


def on_message_prices(ws: ReyaSocket, message: dict):
    if message["type"] == "connected":
        print("Connected")
        for price_stream in market_price_streams:
            ws.prices.subscribe(id=price_stream)
    if message["type"] == "channel_data":
        print(message)

        prices[message["id"]] = message["contents"]
        
        uprices_streams = list(filter(lambda x: prices[x] is None, market_price_streams))
        print("Uninitialized prices:", len(uprices_streams), uprices_streams)
        print()

async def main():
    load_dotenv()
    ws = ReyaSocket(os.environ['REYA_WS_URL'], on_error=on_error, on_message=on_message_prices)
    await ws.connect()

if __name__ == "__main__":
    print("...Start")
    asyncio.run(main())
    print("...End")
