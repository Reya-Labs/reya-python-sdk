import asyncio
from .websocket_client import ReyaSocket

def on_error(wapp, message):
    print("On error", message)

def on_message(ws: ReyaSocket, message: dict):
    if message["type"] == "connected":
        ws.candles.subscribe(id="ETH-rUSD")
    if message["type"] == "channel_batch_data":
        for entry in message["contents"]:
            print("Price", entry["low"], "time", entry["startedAt"])

async def main():
    ws = ReyaSocket("wss://websocket.reya.xyz/", on_error=on_error, on_message=on_message)
    await ws.connect()

if __name__ == "__main__":
    print("... Start")
    asyncio.run(main())
    print("... End")