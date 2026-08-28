"""Injected Reya SDK adapter for the bounded PRO-657 canary lifecycle."""

from __future__ import annotations

from typing import Any, Protocol

import asyncio
import math
import threading
import time
from decimal import Decimal, InvalidOperation

from scripts.canary_lifecycle import OpenOrderUnverifiedError, OrderExpectation, OrderPlan
from scripts.canary_preflight import CanaryProfile
from sdk.async_api.cancel_reason import CancelReason as AsyncCancelReason
from sdk.async_api.order import Order as AsyncOrder
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.order_changes_subscribed_payload import OrderChangesSubscribedPayload
from sdk.async_api.order_status import OrderStatus as AsyncOrderStatus
from sdk.async_api.side import Side as AsyncSide
from sdk.async_api.time_in_force import TimeInForce as AsyncTimeInForce
from sdk.open_api.models.cancel_order_response import CancelOrderResponse
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.modify_order_response import ModifyOrderResponse
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters


class SdkAdapterError(RuntimeError):
    """The SDK response or observation did not prove the canary invariant."""


class RestClientConfig(Protocol):
    """Identity-bearing Reya trading configuration."""

    account_id: int | None
    api_url: str
    chain_id: int
    dex_id: int
    owner_wallet_address: str
    orders_gateway_address: str | None


class RestTradingClient(Protocol):
    """Subset of ReyaTradingClient used by the canary adapter."""

    @property
    def config(self) -> RestClientConfig:
        raise NotImplementedError

    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        raise NotImplementedError

    async def modify_order(self, params: ModifyOrderParameters) -> ModifyOrderResponse:
        raise NotImplementedError

    async def cancel_order(
        self,
        symbol: str,
        account_id: int | None = None,
        order_id: str | None = None,
        client_order_id: int | None = None,
    ) -> CancelOrderResponse:
        raise NotImplementedError

    async def get_open_orders(self) -> list[Order]:
        raise NotImplementedError

    def get_market_id_from_symbol(self, symbol: str) -> int:
        raise NotImplementedError


class WsOrderSource(Protocol):
    """Thread-safe latest-order lookup populated by the read WebSocket."""

    def get(self, order_id: str) -> AsyncOrder | None:
        raise NotImplementedError


class ThreadSafeWsOrderStore:
    """Minimal callback store for wallet orderChanges snapshots and updates."""

    def __init__(self, wallet_address: str) -> None:
        self._orders: dict[str, AsyncOrder] = {}
        self._lock = threading.Lock()
        self._expected_channel = f"/v2/wallet/{wallet_address}/orderChanges".lower()

    def ingest(self, message: object) -> None:
        """Ingest only typed orderChanges payloads; ignore unrelated channels."""
        if not isinstance(message, (OrderChangesSubscribedPayload, OrderChangeUpdatePayload)):
            return
        if message.channel.lower() != self._expected_channel:
            return
        if isinstance(message, OrderChangesSubscribedPayload):
            orders = message.contents.data
        else:
            orders = message.data
        with self._lock:
            for order in orders:
                self._put_if_newer(order)

    def on_message(self, _websocket: object, message: object) -> None:
        """Callback compatible with ReyaSocket(on_message=...)."""
        self.ingest(message)

    def get(self, order_id: str) -> AsyncOrder | None:
        with self._lock:
            return self._orders.get(order_id)

    def _put_if_newer(self, order: AsyncOrder) -> None:
        key = str(order.order_id)
        current = self._orders.get(key)
        if current is None:
            self._orders[key] = order
            return
        if order.sequence_number is None and current.sequence_number is not None:
            return
        if (
            order.sequence_number is not None
            and current.sequence_number is not None
            and order.sequence_number < current.sequence_number
        ):
            return
        self._orders[key] = order


class SdkLifecycleAdapter:
    """Maps the lifecycle protocol to exact Reya REST requests and observations."""

    def __init__(
        self,
        rest_client: RestTradingClient,
        ws_orders: WsOrderSource,
        profile: CanaryProfile,
        *,
        poll_interval_s: float = 0.1,
    ) -> None:
        if not math.isfinite(poll_interval_s) or poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be finite and greater than zero")
        self._rest = rest_client
        self._ws_orders = ws_orders
        self._profile = profile
        self._poll_interval_s = poll_interval_s
        self._client_order_ids: dict[str, int] = {}

    async def place_post_only_gtc(self, plan: OrderPlan, client_order_id: int) -> str:
        self._require_plan_identity(plan)
        response = await self._rest.create_limit_order(
            LimitOrderParameters(
                symbol=plan.market_symbol,
                is_buy=plan.is_buy,
                limit_px=format(plan.initial_limit_px, "f"),
                qty=format(plan.quantity, "f"),
                time_in_force=TimeInForce.GTC,
                reduce_only=False,
                post_only=True,
                client_order_id=client_order_id,
            )
        )
        if response.status == OrderStatus.OPEN:
            self._client_order_ids[response.order_id] = client_order_id
            if response.client_order_id != str(client_order_id):
                raise OpenOrderUnverifiedError(response.order_id, "place response did not preserve the client order ID")
        self._require_response(
            response,
            expected_status=OrderStatus.OPEN,
            operation="place",
        )
        order_id = response.order_id
        return order_id

    async def modify_post_only_gtc(self, order_id: str, plan: OrderPlan, client_order_id: int) -> None:
        self._require_plan_identity(plan)
        self._require_owned_client_order_id(order_id, client_order_id)
        response = await self._rest.modify_order(
            ModifyOrderParameters(
                symbol=plan.market_symbol,
                is_buy=plan.is_buy,
                limit_px=format(plan.modified_limit_px, "f"),
                qty=format(plan.quantity, "f"),
                post_only=True,
                expires_after=None,
                time_in_force=TimeInForce.GTC,
                order_id=int(order_id),
                client_order_id=client_order_id,
                reduce_only=False,
            )
        )
        self._require_response(
            response,
            expected_status=OrderStatus.OPEN,
            expected_order_id=order_id,
            expected_client_order_id=client_order_id,
            operation="modify",
        )

    async def cancel_order(self, order_id: str, plan: OrderPlan) -> None:
        self._require_plan_identity(plan)
        client_order_id = self._client_order_ids.get(order_id)
        if client_order_id is None:
            raise SdkAdapterError("refusing to cancel an order not owned by this adapter run")
        response = await self._rest.cancel_order(
            symbol=plan.market_symbol,
            account_id=plan.account_id,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        self._require_response(
            response,
            expected_status=OrderStatus.CANCELLED,
            expected_order_id=order_id,
            operation="cancel",
        )

    async def wait_rest(self, expectation: OrderExpectation, plan: OrderPlan, timeout_s: float) -> None:
        self._require_plan_identity(plan)
        self._require_observation_owned(expectation.order_id)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            orders = await self._rest.get_open_orders()
            observed = next((order for order in orders if order.order_id == expectation.order_id), None)
            if expectation.status == "CANCELLED":
                if observed is None:
                    return
            elif observed is not None and self._rest_order_matches(observed, expectation, plan):
                return
            await self._sleep_until_next_poll(deadline)
        raise SdkAdapterError(f"REST did not prove {expectation.status} for the exact canary order")

    async def wait_ws(self, expectation: OrderExpectation, plan: OrderPlan, timeout_s: float) -> None:
        self._require_plan_identity(plan)
        self._require_observation_owned(expectation.order_id)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            observed = self._ws_orders.get(expectation.order_id)
            if observed is not None and self._ws_order_matches(observed, expectation, plan):
                return
            await self._sleep_until_next_poll(deadline)
        raise SdkAdapterError(f"read WebSocket did not prove {expectation.status} for the exact canary order")

    async def _sleep_until_next_poll(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(self._poll_interval_s, remaining))

    def _require_owned_client_order_id(self, order_id: str, client_order_id: int) -> None:
        if self._client_order_ids.get(order_id) != client_order_id:
            raise SdkAdapterError("order/client ownership does not match this adapter run")

    def _require_observation_owned(self, order_id: str) -> None:
        if order_id not in self._client_order_ids:
            raise SdkAdapterError("refusing to observe an order not owned by this adapter run")

    def _require_plan_identity(self, plan: OrderPlan) -> None:
        config = self._rest.config
        identity = self._profile.identity
        policy = self._profile.policy
        if plan.market_symbol != policy.market_symbol or plan.market_id != policy.market_id:
            raise SdkAdapterError("canary plan market does not match the validated profile")
        if config.chain_id != identity.chain_id:
            raise SdkAdapterError("REST client chain does not match the validated profile")
        if config.api_url.rstrip("/") != identity.api_url.rstrip("/"):
            raise SdkAdapterError("REST client API URL does not match the validated profile")
        if config.dex_id != identity.exchange_id:
            raise SdkAdapterError("REST client exchange does not match the validated profile")
        if (config.orders_gateway_address or "").lower() != identity.orders_gateway.lower():
            raise SdkAdapterError("REST client Orders Gateway does not match the validated profile")
        if self._rest.get_market_id_from_symbol(plan.market_symbol) != plan.market_id:
            raise SdkAdapterError("REST client market ID does not match the canary plan")
        if config.account_id != plan.account_id:
            raise SdkAdapterError("REST client account does not match the canary plan")
        if config.owner_wallet_address.lower() != plan.wallet_address.lower():
            raise SdkAdapterError("REST client owner wallet does not match the canary plan")

    @staticmethod
    def _require_response(
        response: CreateOrderResponse | ModifyOrderResponse | CancelOrderResponse,
        *,
        expected_status: OrderStatus,
        operation: str,
        expected_order_id: str | None = None,
        expected_client_order_id: int | None = None,
    ) -> None:
        if response.status != expected_status:
            raise SdkAdapterError(f"{operation} response was not {expected_status.value}")
        if expected_order_id is not None and response.order_id != expected_order_id:
            raise SdkAdapterError(f"{operation} response changed the canonical order ID")
        if expected_client_order_id is not None and response.client_order_id != str(expected_client_order_id):
            raise SdkAdapterError(f"{operation} response did not preserve the client order ID")

    def _rest_order_matches(self, order: Order, expectation: OrderExpectation, plan: OrderPlan) -> bool:
        client_order_id = self._client_order_ids.get(expectation.order_id)
        return bool(
            client_order_id is not None
            and order.status == OrderStatus.OPEN
            and order.account_id == plan.account_id
            and order.symbol == plan.market_symbol
            and order.side == (Side.B if plan.is_buy else Side.A)
            and order.time_in_force == TimeInForce.GTC
            and order.post_only is True
            and order.reduce_only is False
            and order.client_order_id == str(client_order_id)
            and _decimal_equal(order.limit_px, expectation.limit_px)
            and _decimal_equal(order.qty, expectation.quantity)
        )

    def _ws_order_matches(self, order: AsyncOrder, expectation: OrderExpectation, plan: OrderPlan) -> bool:
        client_order_id = self._client_order_ids.get(expectation.order_id)
        if (
            client_order_id is None
            or str(order.order_id) != expectation.order_id
            or order.client_order_id != str(client_order_id)
            or order.account_id != plan.account_id
            or order.symbol != plan.market_symbol
        ):
            return False
        if expectation.status == "CANCELLED":
            return order.status == AsyncOrderStatus.CANCELLED and order.cancel_reason == AsyncCancelReason.USER_CANCEL
        return bool(
            order.status == AsyncOrderStatus.OPEN
            and order.side == (AsyncSide.B if plan.is_buy else AsyncSide.A)
            and order.time_in_force == AsyncTimeInForce.GTC
            and order.post_only is True
            and order.reduce_only is False
            and order.client_order_id == str(client_order_id)
            and _decimal_equal(order.limit_px, expectation.limit_px)
            and _decimal_equal(order.qty, expectation.quantity)
        )


def _decimal_equal(actual: Any, expected: Decimal | None) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    try:
        return Decimal(str(actual)) == expected
    except (InvalidOperation, ValueError):
        return False
