from reya_data_feed.websocket_client import ReyaSocket
import asyncio


def on_error(_, message):
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
        ws.prices.subscribe(id="SOLUSD")
    if message["type"] == "channel_data":
        print("Received", message)
        print("Price", message["contents"]['pricePayload']["price"],
              "time", message["contents"]['pricePayload']['timestamp'])


async def main():
    ws = ReyaSocket(os.environ['REYA_WS_URL'],
                    on_error=on_error, on_message=on_message_prices)
    await ws.connect()

if __name__ == "__main__":
    print("...Start")
    asyncio.run(main())
    print("...End")
