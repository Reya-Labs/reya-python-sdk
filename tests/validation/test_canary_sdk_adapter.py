"""Offline contract tests for the injected PRO-657 Reya SDK adapter."""

from __future__ import annotations

from types import SimpleNamespace

from decimal import Decimal

import pytest

from scripts.canary_lifecycle import OpenOrderUnverifiedError, OrderExpectation, OrderPlan
from scripts.canary_preflight import SUPPORTED_ENVIRONMENTS, CanaryPolicy, CanaryProfile
from scripts.canary_sdk_adapter import SdkAdapterError, SdkLifecycleAdapter, ThreadSafeWsOrderStore
from sdk.async_api.order import Order as AsyncOrder
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.order_changes_subscribed_payload import OrderChangesSubscribedPayload
from sdk.open_api.models.cancel_order_response import CancelOrderResponse
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.modify_order_response import ModifyOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters

pytestmark = pytest.mark.offline


class FakeRestClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            account_id=42,
            api_url=SUPPORTED_ENVIRONMENTS["devnet1"].api_url,
            chain_id=SUPPORTED_ENVIRONMENTS["devnet1"].chain_id,
            dex_id=SUPPORTED_ENVIRONMENTS["devnet1"].exchange_id,
            owner_wallet_address="0x1111111111111111111111111111111111111111",
            orders_gateway_address=SUPPORTED_ENVIRONMENTS["devnet1"].orders_gateway,
        )
        self.market_ids = {"BTCRUSDPERP": 1}
        self.create_response = CreateOrderResponse(status=OrderStatus.OPEN, orderId="123", clientOrderId="777")
        self.modify_response = ModifyOrderResponse(status=OrderStatus.OPEN, orderId="123", clientOrderId="777")
        self.cancel_response = CancelOrderResponse(
            status=OrderStatus.CANCELLED,
            orderId="123",
            clientOrderId="777",
        )
        self.open_orders: list[Order] = []
        self.created: list[LimitOrderParameters] = []
        self.modified: list[ModifyOrderParameters] = []
        self.cancelled: list[dict[str, object]] = []

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        self.created.append(params)
        return self.create_response

    async def modify_order(self, params: ModifyOrderParameters) -> ModifyOrderResponse:
        self.modified.append(params)
        return self.modify_response

    async def cancel_order(
        self,
        symbol: str,
        account_id: int | None = None,
        order_id: str | None = None,
        client_order_id: int | None = None,
    ) -> CancelOrderResponse:
        self.cancelled.append(
            {
                "symbol": symbol,
                "account_id": account_id,
                "order_id": order_id,
                "client_order_id": client_order_id,
            }
        )
        return self.cancel_response

    async def get_open_orders(self) -> list[Order]:
        return self.open_orders

    def get_market_id_from_symbol(self, symbol: str) -> int:
        return self.market_ids[symbol]


class FakeWsOrders:
    def __init__(self) -> None:
        self.orders: dict[str, AsyncOrder] = {}

    def get(self, order_id: str) -> AsyncOrder | None:
        return self.orders.get(order_id)


@pytest.fixture(name="plan")
def fixture_plan() -> OrderPlan:
    return OrderPlan(
        account_id=42,
        wallet_address="0x1111111111111111111111111111111111111111",
        market_symbol="BTCRUSDPERP",
        market_id=1,
        is_buy=True,
        quantity=Decimal("0.0100"),
        initial_limit_px=Decimal("100.50"),
        modified_limit_px=Decimal("101.25"),
    )


@pytest.fixture(name="profile")
def fixture_profile() -> CanaryProfile:
    return CanaryProfile(
        name="devnet1-pro-657",
        enabled=True,
        environment="devnet1",
        identity=SUPPORTED_ENVIRONMENTS["devnet1"],
        release_manifest_id="perp-ob-f989de0",
        rpc_url_env="REYA_CANARY_RPC_URL",
        policy=CanaryPolicy(
            market_symbol="BTCRUSDPERP",
            market_id=1,
            max_quantity=Decimal("1"),
            max_notional=Decimal("1000"),
            allowed_account_ids=(42,),
            allowed_wallet_addresses=("0x1111111111111111111111111111111111111111",),
        ),
    )


def _rest_order(
    *,
    status: str = "OPEN",
    limit_px: str = "100.5",
    post_only: bool = True,
) -> Order:
    return Order.model_validate(
        {
            "exchangeId": 1,
            "symbol": "BTCRUSDPERP",
            "accountId": 42,
            "orderId": "123",
            "clientOrderId": "777",
            "qty": "0.01",
            "execQty": "0",
            "cumQty": "0",
            "side": "B",
            "limitPx": limit_px,
            "orderType": "LIMIT",
            "timeInForce": "GTC",
            "reduceOnly": False,
            "postOnly": post_only,
            "status": status,
            "createdAt": 1,
            "lastUpdateAt": 1,
        }
    )


def _ws_order(
    *,
    status: str = "OPEN",
    limit_px: str = "100.5",
    sequence_number: int | None = 10,
    cancel_reason: str | None = None,
) -> AsyncOrder:
    payload: dict[str, object] = {
        "exchangeId": 1,
        "symbol": "BTCRUSDPERP",
        "accountId": 42,
        "orderId": "123",
        "sequenceNumber": sequence_number,
        "clientOrderId": "777",
        "qty": "0.01",
        "execQty": "0",
        "cumQty": "0",
        "side": "B",
        "limitPx": limit_px,
        "orderType": "LIMIT",
        "timeInForce": "GTC",
        "reduceOnly": False,
        "postOnly": True,
        "status": status,
        "createdAt": 1,
        "lastUpdateAt": sequence_number or 1,
    }
    if cancel_reason is not None:
        payload["cancelReason"] = cancel_reason
        payload["cancelReasonMessage"] = "test-only"
    return AsyncOrder.model_validate(payload)


@pytest.mark.asyncio
async def test_adapter_maps_exact_post_only_gtc_create_modify_and_single_cancel(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    rest = FakeRestClient()
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile)

    order_id = await adapter.place_post_only_gtc(plan, 777)
    await adapter.modify_post_only_gtc(order_id, plan, 777)
    await adapter.cancel_order(order_id, plan)

    assert rest.created == [
        LimitOrderParameters(
            symbol="BTCRUSDPERP",
            is_buy=True,
            limit_px="100.50",
            qty="0.0100",
            time_in_force=TimeInForce.GTC,
            reduce_only=False,
            post_only=True,
            client_order_id=777,
        )
    ]
    assert rest.created[0].time_in_force.value == "GTC"
    assert rest.modified[0].order_id == 123
    assert rest.modified[0].limit_px == "101.25"
    assert rest.modified[0].qty == "0.0100"
    assert rest.modified[0].post_only is True
    assert rest.modified[0].reduce_only is False
    assert rest.modified[0].client_order_id == 777
    assert rest.cancelled == [
        {
            "symbol": "BTCRUSDPERP",
            "account_id": 42,
            "order_id": "123",
            "client_order_id": 777,
        }
    ]


@pytest.mark.asyncio
async def test_place_rejects_non_resting_response(profile: CanaryProfile, plan: OrderPlan) -> None:
    rest = FakeRestClient()
    rest.create_response = CreateOrderResponse(
        status=OrderStatus.CANCELLED,
        orderId="123",
        clientOrderId="777",
    )
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile)

    with pytest.raises(SdkAdapterError):
        await adapter.place_post_only_gtc(plan, 777)

    assert not rest.cancelled


@pytest.mark.asyncio
async def test_open_response_with_wrong_client_id_exposes_canonical_id_for_cleanup(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    rest = FakeRestClient()
    rest.create_response = CreateOrderResponse(status=OrderStatus.OPEN, orderId="123", clientOrderId="778")
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile)

    with pytest.raises(OpenOrderUnverifiedError) as raised:
        await adapter.place_post_only_gtc(plan, 777)

    assert raised.value.order_id == "123"


@pytest.mark.asyncio
async def test_adapter_refuses_modify_and_cancel_for_unowned_order(profile: CanaryProfile, plan: OrderPlan) -> None:
    rest = FakeRestClient()
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile)

    with pytest.raises(SdkAdapterError, match="ownership"):
        await adapter.modify_post_only_gtc("999", plan, 777)
    with pytest.raises(SdkAdapterError, match="not owned"):
        await adapter.cancel_order("999", plan)

    assert not rest.modified
    assert not rest.cancelled


@pytest.mark.asyncio
async def test_rest_observation_requires_all_exact_order_fields(profile: CanaryProfile, plan: OrderPlan) -> None:
    rest = FakeRestClient()
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile, poll_interval_s=0.001)
    await adapter.place_post_only_gtc(plan, 777)
    rest.open_orders = [_rest_order()]

    await adapter.wait_rest(OrderExpectation("123", "OPEN", Decimal("100.50"), Decimal("0.0100")), plan, 0.02)
    rest.open_orders = []
    await adapter.wait_rest(OrderExpectation("123", "CANCELLED"), plan, 0.02)

    rest.open_orders = [_rest_order(post_only=False)]
    with pytest.raises(SdkAdapterError, match="REST did not prove OPEN"):
        await adapter.wait_rest(OrderExpectation("123", "OPEN", Decimal("100.50"), Decimal("0.0100")), plan, 0.01)

    with pytest.raises(SdkAdapterError, match="not owned"):
        await adapter.wait_rest(OrderExpectation("999", "CANCELLED"), plan, 0.01)


@pytest.mark.asyncio
async def test_ws_observation_requires_user_cancel_and_ignores_feed_reset(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    rest = FakeRestClient()
    ws_orders = FakeWsOrders()
    adapter = SdkLifecycleAdapter(rest, ws_orders, profile, poll_interval_s=0.001)
    await adapter.place_post_only_gtc(plan, 777)
    ws_orders.orders["123"] = _ws_order()

    await adapter.wait_ws(OrderExpectation("123", "OPEN", Decimal("100.50"), Decimal("0.0100")), plan, 0.02)

    ws_orders.orders["123"] = _ws_order(status="CANCELLED", cancel_reason="FEED_RESET")
    with pytest.raises(SdkAdapterError, match="did not prove CANCELLED"):
        await adapter.wait_ws(OrderExpectation("123", "CANCELLED"), plan, 0.01)

    ws_orders.orders["123"] = _ws_order(status="CANCELLED", cancel_reason="USER_CANCEL", sequence_number=11)
    await adapter.wait_ws(OrderExpectation("123", "CANCELLED"), plan, 0.02)


def test_thread_safe_ws_store_ignores_unrelated_and_stale_order_messages() -> None:
    store = ThreadSafeWsOrderStore("0x1")
    store.ingest(object())
    store.ingest(
        OrderChangeUpdatePayload.model_validate(
            {
                "type": "channel_data",
                "timestamp": 0,
                "channel": "/v2/wallet/0x2/orderChanges",
                "data": [_ws_order(limit_px="500", sequence_number=99)],
            }
        )
    )
    store.ingest(
        OrderChangesSubscribedPayload.model_validate(
            {
                "type": "subscribed",
                "channel": "/v2/wallet/0x1/orderChanges",
                "contents": {"data": [_ws_order(sequence_number=None)], "snapshotSequenceNumber": 9},
            }
        )
    )
    store.on_message(
        object(),
        OrderChangeUpdatePayload.model_validate(
            {
                "type": "channel_data",
                "timestamp": 1,
                "channel": "/v2/wallet/0x1/orderChanges",
                "data": [_ws_order(limit_px="101.25", sequence_number=11)],
            }
        ),
    )
    store.ingest(
        OrderChangeUpdatePayload.model_validate(
            {
                "type": "channel_data",
                "timestamp": 2,
                "channel": "/v2/wallet/0x1/orderChanges",
                "data": [_ws_order(limit_px="99", sequence_number=10)],
            }
        )
    )

    assert store.get("123") is not None
    assert store.get("123").limit_px == "101.25"  # type: ignore[union-attr]


def test_adapter_rejects_invalid_poll_interval(profile: CanaryProfile) -> None:
    with pytest.raises(ValueError, match="finite"):
        SdkLifecycleAdapter(FakeRestClient(), FakeWsOrders(), profile, poll_interval_s=float("nan"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config_field", "value", "message"),
    [
        ("account_id", 99, "account does not match"),
        ("chain_id", 99, "chain does not match"),
        ("api_url", "https://wrong.invalid/v2", "API URL does not match"),
        ("dex_id", 99, "exchange does not match"),
        ("orders_gateway_address", "0x2222222222222222222222222222222222222222", "Orders Gateway does not match"),
        ("owner_wallet_address", "0x2222222222222222222222222222222222222222", "owner wallet does not match"),
    ],
)
async def test_adapter_rejects_mismatched_rest_client_identity_before_order_entry(
    plan: OrderPlan,
    profile: CanaryProfile,
    config_field: str,
    value: object,
    message: str,
) -> None:
    rest = FakeRestClient()
    setattr(rest.config, config_field, value)
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile)

    with pytest.raises(SdkAdapterError, match=message):
        await adapter.place_post_only_gtc(plan, 777)

    assert not rest.created


@pytest.mark.asyncio
async def test_adapter_rejects_loaded_market_id_mismatch_before_order_entry(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    rest = FakeRestClient()
    rest.market_ids[plan.market_symbol] = 99
    adapter = SdkLifecycleAdapter(rest, FakeWsOrders(), profile)

    with pytest.raises(SdkAdapterError, match="market ID does not match"):
        await adapter.place_post_only_gtc(plan, 777)

    assert not rest.created
