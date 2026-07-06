"""Offline guards for the /v2/prices deprecation migration."""

from __future__ import annotations

from typing import Any, cast

import pytest

from sdk.open_api.models.asset_oracle_price import AssetOraclePrice
from sdk.open_api.models.market_summary import MarketSummary
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_websocket.resources.market import MarketResource
from sdk.reya_websocket.resources.prices import PricesResource
from sdk.reya_websocket.socket import ReyaSocket

pytestmark = pytest.mark.offline

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
OWNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"


class _RecordingSocket:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    def send_subscribe(self, channel: str, **kwargs: Any) -> None:
        self.messages.append(("subscribe", channel, kwargs))

    def send_unsubscribe(self, channel: str, **kwargs: Any) -> None:
        self.messages.append(("unsubscribe", channel, kwargs))


class _FakeMarkets:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def get_asset_oracle_prices(self) -> list[AssetOraclePrice]:
        self.calls.append(("get_asset_oracle_prices", None))
        return [
            AssetOraclePrice(asset="BTC", oraclePrice="100000", updatedAt=1747927089946),
            AssetOraclePrice(asset="ETH", oraclePrice="2500", updatedAt=1747927089946),
        ]

    async def get_perp_market_summary(self, symbol: str) -> MarketSummary:
        self.calls.append(("get_perp_market_summary", symbol))
        return MarketSummary(
            symbol=symbol,
            updatedAt=1747927089946,
            oiQty="1",
            fundingRate="0",
            longFundingValue="0",
            shortFundingValue="0",
            volume24h="10",
            markPrice="2500",
            throttledMidPrice="2501",
            pricesUpdatedAt=1747927089946,
        )


@pytest.fixture
def client() -> ReyaTradingClient:
    config = TradingConfig(
        api_url="https://invalid.example/v2",
        chain_id=89346162,
        owner_wallet_address=OWNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=12345,
        dex_id_override=2,
    )
    c = ReyaTradingClient(config)
    c._resources.markets = cast(Any, _FakeMarkets())  # pylint: disable=protected-access
    return c


@pytest.mark.asyncio
async def test_asset_oracle_price_helper_uses_asset_oracle_prices_endpoint(client: ReyaTradingClient) -> None:
    price = await client.get_asset_oracle_price("eth")

    assert price.asset == "ETH"
    assert price.oracle_price == "2500"
    assert client.markets.calls == [("get_asset_oracle_prices", None)]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_market_price_helpers_use_market_summary_endpoint(client: ReyaTradingClient) -> None:
    mark_price = await client.get_market_mark_price("ETHRUSDPERP")
    mid_price = await client.get_market_mid_price("ETHRUSDPERP")

    assert mark_price == "2500"
    assert mid_price == "2501"
    assert client.markets.calls == [  # type: ignore[attr-defined]
        ("get_perp_market_summary", "ETHRUSDPERP"),
        ("get_perp_market_summary", "ETHRUSDPERP"),
    ]


def test_websocket_resource_no_longer_exposes_deprecated_prices_channels() -> None:
    assert "/v2/prices" not in ReyaSocket.CHANNEL_PAYLOAD_MAP

    socket = ReyaSocket()
    assert socket._get_payload_type("/v2/prices/ETHRUSDPERP") is None  # pylint: disable=protected-access

    prices = PricesResource(_RecordingSocket())  # type: ignore[arg-type]
    assert not hasattr(prices, "all_prices")
    assert not hasattr(prices, "price")


def test_websocket_market_resources_expose_summary_channels() -> None:
    socket = _RecordingSocket()
    market = MarketResource(socket)  # type: ignore[arg-type]

    market.all_markets_summary.subscribe(batched=True)
    market.summary("ETHRUSDPERP").subscribe()
    market.all_spot_markets_summary.subscribe()
    market.spot_summary("WETHRUSD").subscribe()

    assert socket.messages == [
        ("subscribe", "/v2/perpMarkets/summary", {"batched": True}),
        ("subscribe", "/v2/perpMarket/ETHRUSDPERP/summary", {"batched": False}),
        ("subscribe", "/v2/spotMarkets/summary", {"batched": False}),
        ("subscribe", "/v2/spotMarket/WETHRUSD/summary", {"batched": False}),
    ]
