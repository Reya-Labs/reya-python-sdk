import json
import ssl
from dataclasses import dataclass, field

import websocket
from typing_extensions import Any, Callable, Optional, Self


@dataclass
class Channel:
    channel: str = field(init=False)
    app: websocket.WebSocketApp

    def subscribe(self, **kwargs) -> Self:
        self.app.send(
            json.dumps(
                {"type": "subscribe", "channel": self.channel, **kwargs})
        )
        return self

    def unsubscribe(self, **kwargs):
        self.app.send(
            json.dumps(
                {"type": "unsubscribe", "channel": self.channel, **kwargs})
        )


class Candles(Channel):
    channel = "candles"

    def subscribe(self, id, batched=True) -> Self:
        return super().subscribe(id=f"{id}/15MINS", batched=batched)

    def unsubscribe(self, id):
        super().unsubscribe(id=f"{id}/15MINS")


class Prices(Channel):
    channel = "prices"

    def subscribe(self, id, batched=False) -> Self:
        return super().subscribe(id=id, batched=batched)

    def unsubscribe(self, id):
        super().unsubscribe(id=id)


class FundingRates(Channel):
    channel = "funding-rates"

    def subscribe(self, id, batched=False) -> Self:
        return super().subscribe(id=id, batched=batched)

    def unsubscribe(self, id):
        super().unsubscribe(id=id)


def as_json(on_message):
    def wrapper(ws, message):
        return on_message(ws, json.loads(message))

    return wrapper


class ReyaSocket(websocket.WebSocketApp):
    def __init__(
        self,
        url: str,
        on_open: Optional[Callable[[websocket.WebSocket], None]] = None,
        on_message: Optional[Callable[[
            websocket.WebSocket, Any], None]] = None,
        *args,
        **kwargs,
    ):
        self.candles = Candles(self)
        self.prices = Prices(self)
        self.funding_rates = FundingRates(self)

        super().__init__(
            url=url,
            on_open=on_open,
            on_message=as_json(on_message),
            *args,
            **kwargs,
        )

    async def connect(self, sslopt={"cert_reqs": ssl.CERT_NONE}) -> None:
        self.run_forever(sslopt=sslopt)
