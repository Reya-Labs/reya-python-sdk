from reya_data_feed.websocket_client import ReyaSocket
import asyncio

def on_error(wapp, message):
    print("On error", message)

def on_message_candles(ws: ReyaSocket, message: dict):
    if message["type"] == "connected":
        ws.candles.subscribe(id="ETH-rUSD")
    if message["type"] == "channel_batch_data":
        for entry in message["contents"]:
            print("Price", entry["low"], "time", entry["startedAt"])

def on_message_prices(ws: ReyaSocket, message: dict):
    if message["type"] == "connected":
        print("Connected")
        ws.prices.subscribe(id="ETHUSD")
    if message["type"] == "channel_batch_data":
        print("Received")
        for entry in message["contents"]:
            print("Price", entry['pricePayload']["price"], "time", entry['pricePayload']['timestamp'])

async def main():
    ws = ReyaSocket("wss://ws-test.reya-cronos.network", on_error=on_error, on_message=on_message_prices)
    await ws.connect()

if __name__ == "__main__":
    print("... Start")
    asyncio.run(main())
    print("... End")