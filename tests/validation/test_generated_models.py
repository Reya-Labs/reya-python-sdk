"""Offline checks for generated API models that track specs PR 59."""

from __future__ import annotations

from typing import Any

import inspect

import pytest
from pydantic import ValidationError

import sdk.open_api as rest_open_api
from sdk.async_api.asset_oracle_prices_channel import AssetOraclePricesChannel
from sdk.async_api.asset_oracle_prices_update_payload import AssetOraclePricesUpdatePayload
from sdk.async_api.cancel_reason import CancelReason as WsInfoCancelReason
from sdk.async_api.execution_bust import ExecutionBust as WsInfoExecutionBust
from sdk.async_api.execution_type import ExecutionType as WsInfoExecutionType
from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.async_api.markets_summary_channel import MarketsSummaryChannel
from sdk.async_api.markets_summary_update_payload import MarketsSummaryUpdatePayload
from sdk.async_api.order import Order as WsInfoOrder
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.order_changes_subscribed_payload import OrderChangesSubscribedPayload
from sdk.async_api.order_status import OrderStatus as WsInfoOrderStatus
from sdk.async_api.spot_execution import SpotExecution as WsInfoSpotExecution
from sdk.async_exec_api.cancel_reason import CancelReason as WsExecCancelReason
from sdk.async_exec_api.create_order_request import CreateOrderRequest as WsExecCreateOrderRequest
from sdk.async_exec_api.create_order_response import CreateOrderResponse as WsExecCreateOrderResponse
from sdk.async_exec_api.modify_order_request import ModifyOrderRequest as WsExecModifyOrderRequest
from sdk.async_exec_api.modify_order_response import ModifyOrderResponse as WsExecModifyOrderResponse
from sdk.async_exec_api.order_status import OrderStatus as WsExecOrderStatus
from sdk.async_exec_api.request_error_code import RequestErrorCode as WsExecRequestErrorCode
from sdk.open_api import AssetOraclePrice as RestAssetOraclePrice
from sdk.open_api import CancelReason as RestCancelReason
from sdk.open_api import CreateOrderRequest as RestCreateOrderRequest
from sdk.open_api import CreateOrderResponse as RestCreateOrderResponse
from sdk.open_api import ExecutionBust as RestExecutionBust
from sdk.open_api import ExecutionType as RestExecutionType
from sdk.open_api import FeeTierParameters as RestFeeTierParameters
from sdk.open_api import ModifyOrderRequest as RestModifyOrderRequest
from sdk.open_api import ModifyOrderResponse as RestModifyOrderResponse
from sdk.open_api import Order as RestOrder
from sdk.open_api import OrderHistoryList as RestOrderHistoryList
from sdk.open_api import OrderStatus as RestOrderStatus
from sdk.open_api import RequestErrorCode as RestRequestErrorCode
from sdk.open_api import SpotExecution as RestSpotExecution
from sdk.open_api.api.market_data_api import MarketDataApi
from sdk.open_api.api.reference_data_api import ReferenceDataApi
from sdk.open_api.api.wallet_data_api import WalletDataApi
from sdk.reya_websocket.resources.prices import PricesResource
from sdk.reya_websocket.socket import ReyaSocket

pytestmark = pytest.mark.offline

PRO_405_REQUEST_ERROR_CODES = {
    "RATE_LIMITED_ERROR",
    "INSUFFICIENT_BALANCE_ERROR",
    "OPEN_ORDER_CAP_ERROR",
    "PRICE_QTY_BOUNDS_ERROR",
    "SERVICE_DISABLED_ERROR",
    "UNAUTHORIZED_ACCOUNT_ERROR",
    "TRADING_HALTED_ERROR",
    "DUPLICATE_CLIENT_ORDER_ID_ERROR",
}

CANCEL_REASONS = {
    "NO_LIQUIDITY",
    "IOC_REMAINDER",
    "SELF_TRADE_PREVENTION",
    "GTT_EXPIRED",
    "USER_CANCEL",
    "MASS_CANCEL",
    "CANCEL_ALL_AFTER",
    "FEED_RESET",
    "RISK_CANCELLED",
}

EXECUTION_TYPES = {"ORDER_MATCH", "LIQUIDATION", "ADL", "MARKET_CLOSE"}


def test_order_status_enums_do_not_expose_rejected() -> None:
    assert {status.value for status in RestOrderStatus} == {"OPEN", "FILLED", "CANCELLED"}
    assert {status.value for status in WsInfoOrderStatus} == {"OPEN", "FILLED", "CANCELLED"}
    assert {status.value for status in WsExecOrderStatus} == {"OPEN", "FILLED", "CANCELLED"}


def test_execution_type_enums_share_specs_values() -> None:
    """REST and wallet-info WS must expose the same public execution types."""
    assert {execution_type.value for execution_type in RestExecutionType} == EXECUTION_TYPES
    assert {execution_type.value for execution_type in WsInfoExecutionType} == EXECUTION_TYPES


@pytest.mark.parametrize(
    "method_name",
    [
        "get_market_depth",
        "get_market_depth_with_http_info",
        "get_market_depth_without_preload_content",
    ],
)
def test_market_depth_limit_preserves_positional_timeout(method_name: str) -> None:
    signature = inspect.signature(getattr(MarketDataApi, method_name))
    bound = signature.bind(None, "ETHRUSDPERP", 5.0, limit=10)

    assert bound.arguments["_request_timeout"] == 5.0
    assert bound.arguments["limit"] == 10
    assert signature.parameters["limit"].kind is inspect.Parameter.KEYWORD_ONLY


def test_rest_fee_tier_parameters_exposes_only_30_day_volume() -> None:
    tier = RestFeeTierParameters.from_dict(
        {
            "tierId": 2,
            "takerFee": "0.0003",
            "makerFee": "0",
            "volume30d": "10000000",
            "tierType": "REGULAR",
        }
    )

    assert tier is not None
    assert tier.volume30d == "10000000"
    assert not hasattr(tier, "volume14d")
    assert tier.to_dict() == {
        "tierId": 2,
        "takerFee": "0.0003",
        "makerFee": "0",
        "volume30d": "10000000",
        "tierType": "REGULAR",
    }


def test_rest_request_error_code_exports_pro_405_codes() -> None:
    assert PRO_405_REQUEST_ERROR_CODES <= {code.value for code in RestRequestErrorCode}


def test_ws_exec_request_error_code_exports_pro_405_codes() -> None:
    assert PRO_405_REQUEST_ERROR_CODES <= {code.value for code in WsExecRequestErrorCode}


def test_request_error_code_uses_error_suffix_convention() -> None:
    suffixed_codes = {
        "SYMBOL_NOT_FOUND_ERROR",
        "NO_ACCOUNTS_FOUND_ERROR",
        "NO_PRICES_FOUND_FOR_SYMBOL_ERROR",
        "ORDER_NOT_FOUND_ERROR",
    }
    unsuffixed_codes = {
        "SYMBOL_NOT_FOUND",
        "NO_ACCOUNTS_FOUND",
        "NO_PRICES_FOUND_FOR_SYMBOL",
        "ORDER_NOT_FOUND",
    }

    rest_codes = {code.value for code in RestRequestErrorCode}
    ws_exec_codes = {code.value for code in WsExecRequestErrorCode}

    assert suffixed_codes <= rest_codes
    assert not unsuffixed_codes & rest_codes
    assert suffixed_codes <= ws_exec_codes
    assert not unsuffixed_codes & ws_exec_codes


def test_cancel_reason_enums_share_specs_values() -> None:
    assert CANCEL_REASONS == {reason.value for reason in RestCancelReason}
    assert CANCEL_REASONS == {reason.value for reason in WsInfoCancelReason}
    assert CANCEL_REASONS == {reason.value for reason in WsExecCancelReason}


def _base_create_request_payload() -> dict[str, Any]:
    return {
        "exchangeId": 1,
        "symbol": "ETHRUSDPERP",
        "accountId": 12345,
        "isBuy": True,
        "limitPx": "2500",
        "orderType": "STOP_LOSS",
        "triggerPx": "2400",
        "signature": "0x" + "11" * 65,
        "nonce": "1778601294999111",
        "signerWallet": "0x1234567890123456789012345678901234567890",
        "deadline": 1747927089,
    }


def _base_modify_request_payload() -> dict[str, Any]:
    return {
        "orderId": "490346525705109504",
        "symbol": "ETHRUSDPERP",
        "accountId": 12345,
        "exchangeId": 1,
        "isBuy": True,
        "orderType": "LIMIT",
        "timeInForce": "GTC",
        "reduceOnly": False,
        "limitPx": "2500",
        "qty": "0.5",
        "postOnly": False,
        "signature": "0x" + "22" * 65,
        "nonce": "1778601294999112",
        "signerWallet": "0x1234567890123456789012345678901234567890",
        "deadline": 1747927089,
    }


def _base_trigger_modify_request_payload() -> dict[str, Any]:
    """A STOP_LOSS trigger reprice: triggerPx restated, qty omitted (the signed
    quantity restates 0 = protect the whole position)."""
    payload = _base_modify_request_payload()
    payload.update({"orderType": "STOP_LOSS", "triggerPx": "2400"})
    del payload["qty"]
    return payload


def _base_order_response_payload() -> dict[str, Any]:
    return {
        "exchangeId": 2,
        "symbol": "ETHRUSDPERP",
        "accountId": 12345,
        "orderId": "490346525705109504",
        "clientOrderId": "42",
        "qty": "1",
        "execQty": "0",
        "cumQty": "0",
        "side": "B",
        "limitPx": "2500",
        "orderType": "LIMIT",
        "triggerPx": "0",
        "timeInForce": "GTC",
        "reduceOnly": False,
        "postOnly": True,
        "status": "OPEN",
        "createdAt": 1745000000,
        "lastUpdateAt": 1745000001,
    }


def _base_spot_execution_payload() -> dict[str, Any]:
    return {
        "exchangeId": 2,
        "symbol": "WETHRUSD",
        "takerAccountId": 12345,
        "makerAccountId": 67890,
        "takerOrderId": "490346525705109504",
        "makerOrderId": "490346525705109505",
        "side": "B",
        "qty": "1",
        "price": "2500",
        "takerFee": "0",
        "type": "ORDER_MATCH",
        "timestamp": 1745000000,
        "sequenceNumber": 99,
        "fillId": "9001",
    }


def _base_execution_bust_payload() -> dict[str, Any]:
    return {
        "exchangeId": 2,
        "symbol": "WETHRUSD",
        "takerAccountId": 12345,
        "makerAccountId": 67890,
        "takerOrderId": "490346525705109504",
        "makerOrderId": "490346525705109505",
        "side": "B",
        "qty": "1",
        "price": "2500",
        "reason": {
            "reasonName": "AccountBelowIM",
            "accountId": 12345,
            "delta": "-0.05",
            "shortfall": "0.05",
        },
        "timestamp": 1745000000,
        "sequenceNumber": 99,
        "fillId": "9001",
    }


def test_rest_create_order_request_accepts_trigger_without_qty() -> None:
    request = RestCreateOrderRequest.from_dict(_base_create_request_payload())

    assert request is not None
    assert request.qty is None
    serialized = request.to_dict()
    assert "qty" not in serialized
    assert "timeInForce" not in serialized


def test_ws_exec_create_order_request_accepts_trigger_without_qty() -> None:
    request = WsExecCreateOrderRequest.model_validate(_base_create_request_payload())

    assert request.qty is None
    serialized = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "qty" not in serialized
    assert "timeInForce" not in serialized


def test_rest_modify_order_request_accepts_omitted_expires_after() -> None:
    request = RestModifyOrderRequest.from_dict(_base_modify_request_payload())

    assert request is not None
    assert request.expires_after is None
    assert "expiresAfter" not in request.to_dict()


def test_ws_exec_modify_order_request_accepts_omitted_expires_after() -> None:
    request = WsExecModifyOrderRequest.model_validate(_base_modify_request_payload())

    assert request.expires_after is None
    assert "expiresAfter" not in request.model_dump(mode="json", by_alias=True, exclude_none=True)


def test_rest_modify_order_request_accepts_trigger_without_qty() -> None:
    request = RestModifyOrderRequest.from_dict(_base_trigger_modify_request_payload())

    assert request is not None
    assert request.qty is None
    serialized = request.to_dict()
    assert "qty" not in serialized
    assert serialized["triggerPx"] == "2400"


def test_ws_exec_modify_order_request_accepts_trigger_without_qty() -> None:
    request = WsExecModifyOrderRequest.model_validate(_base_trigger_modify_request_payload())

    assert request.qty is None
    serialized = request.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "qty" not in serialized
    assert serialized["triggerPx"] == "2400"


def test_rest_order_response_omits_non_gtt_expires_after() -> None:
    order = RestOrder.from_dict(_base_order_response_payload())

    assert order is not None
    assert order.expires_after is None
    assert order.client_order_id == "42"
    assert "expiresAfter" not in order.to_dict()
    assert order.to_dict()["clientOrderId"] == "42"


def test_rest_order_response_includes_gtt_expires_after() -> None:
    order = RestOrder.from_dict(
        {
            **_base_order_response_payload(),
            "timeInForce": "GTT",
            "expiresAfter": 1745000600,
        }
    )

    assert order is not None
    assert order.expires_after == 1745000600
    assert order.to_dict()["expiresAfter"] == 1745000600


def test_rest_order_history_list_parses_filled_order_projection() -> None:
    history = RestOrderHistoryList.from_dict(
        {
            "data": [
                {
                    **_base_order_response_payload(),
                    "status": "FILLED",
                    "execQty": "1",
                    "cumQty": "1",
                    "firstFillId": "1869408744486469632",
                    "fillCount": 1,
                    "sequenceNumber": 123456,
                }
            ],
            "meta": {
                "limit": 100,
                "count": 1,
                "startTime": 1745000001,
                "endTime": 1745000000,
            },
        }
    )

    assert history is not None
    assert history.meta.count == 1
    assert history.data[0].status == RestOrderStatus.FILLED
    assert history.data[0].first_fill_id == "1869408744486469632"
    assert history.data[0].fill_count == 1
    assert history.data[0].sequence_number == 123456
    assert history.to_dict()["data"][0]["firstFillId"] == "1869408744486469632"
    assert history.to_dict()["data"][0]["sequenceNumber"] == 123456


def test_rest_order_history_list_parses_non_fill_event_projection() -> None:
    history = RestOrderHistoryList.from_dict(
        {
            "data": [
                {
                    **_base_order_response_payload(),
                    "orderId": "490346525705109505",
                    "status": "OPEN",
                    "execQty": "0",
                    "cumQty": "0",
                    "sequenceNumber": 123457,
                    "lastUpdateAt": 1745000002,
                }
            ],
            "meta": {
                "limit": 100,
                "count": 1,
                "startTime": 1745000002,
                "endTime": 1745000002,
            },
        }
    )

    assert history is not None
    assert history.data[0].sequence_number == 123457
    assert history.data[0].first_fill_id is None
    assert history.data[0].fill_count is None
    serialized_order = history.to_dict()["data"][0]
    assert serialized_order["sequenceNumber"] == 123457
    assert "firstFillId" not in serialized_order
    assert "fillCount" not in serialized_order


def test_rest_order_history_pagination_meta_supports_inclusive_end_time_dedup() -> None:
    first_page = RestOrderHistoryList.from_dict(
        {
            "data": [
                {
                    **_base_order_response_payload(),
                    "orderId": "490346525705109506",
                    "sequenceNumber": 2002,
                    "lastUpdateAt": 1745000002,
                },
                {
                    **_base_order_response_payload(),
                    "orderId": "490346525705109505",
                    "sequenceNumber": 2001,
                    "lastUpdateAt": 1745000001,
                },
            ],
            "meta": {
                "limit": 2,
                "count": 2,
                "startTime": 1745000002,
                "endTime": 1745000001,
            },
        }
    )
    second_page = RestOrderHistoryList.from_dict(
        {
            "data": [
                {
                    **_base_order_response_payload(),
                    "orderId": "490346525705109505",
                    "sequenceNumber": 2001,
                    "lastUpdateAt": 1745000001,
                },
                {
                    **_base_order_response_payload(),
                    "orderId": "490346525705109504",
                    "sequenceNumber": 2000,
                    "lastUpdateAt": 1745000000,
                },
            ],
            "meta": {
                "limit": 2,
                "count": 2,
                "startTime": 1745000001,
                "endTime": 1745000000,
            },
        }
    )

    assert first_page is not None
    assert second_page is not None
    assert first_page.meta.end_time == 1745000001
    assert second_page.data[0].last_update_at == first_page.meta.end_time
    assert second_page.data[0].sequence_number == first_page.data[-1].sequence_number

    seen_sequence_numbers = {order.sequence_number for order in first_page.data}
    deduped_second_page = [order for order in second_page.data if order.sequence_number not in seen_sequence_numbers]

    assert [order.order_id for order in deduped_second_page] == ["490346525705109504"]
    assert second_page.to_dict()["meta"]["endTime"] == 1745000000


def test_rest_open_order_snapshot_omits_order_sequence_number() -> None:
    order = RestOrder.from_dict(_base_order_response_payload())

    assert order is not None
    assert order.sequence_number is None
    assert "sequenceNumber" not in order.to_dict()


def test_ws_info_order_response_omits_non_gtt_expires_after() -> None:
    order = WsInfoOrder.model_validate(_base_order_response_payload())

    assert order.expires_after is None
    assert order.client_order_id == "42"
    serialized = order.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "expiresAfter" not in serialized
    assert serialized["clientOrderId"] == "42"


def test_ws_info_order_response_includes_gtt_expires_after() -> None:
    order = WsInfoOrder.model_validate(
        {
            **_base_order_response_payload(),
            "timeInForce": "GTT",
            "expiresAfter": 1745000600,
        }
    )

    assert order.expires_after == 1745000600
    serialized = order.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized["expiresAfter"] == 1745000600


def test_rest_sdk_omits_removed_amm_liquidity_parameters_surface() -> None:
    assert not hasattr(rest_open_api, "LiquidityParameters")
    assert not hasattr(ReferenceDataApi, "get_liquidity_parameters")
    assert not hasattr(ReferenceDataApi, "get_market_definitions")
    assert not hasattr(ReferenceDataApi, "get_market_definitions_with_http_info")
    assert hasattr(ReferenceDataApi, "get_perp_market_definitions")


def test_rest_sdk_exposes_asset_oracle_prices_surface() -> None:
    assert hasattr(rest_open_api, "AssetOraclePrice")
    assert not hasattr(rest_open_api, "CollateralOraclePrice")
    assert hasattr(MarketDataApi, "get_asset_oracle_prices")
    assert not hasattr(MarketDataApi, "get_collateral_oracle_prices")

    price = RestAssetOraclePrice.from_dict({"asset": "ETH", "oraclePrice": "2500", "updatedAt": 1747927089946})

    assert price is not None
    assert price.asset == "ETH"
    assert price.oracle_price == "2500"
    assert price.to_dict() == {
        "asset": "ETH",
        "oraclePrice": "2500",
        "updatedAt": 1747927089946,
    }
    assert "poolPrice" not in price.to_dict()


def test_rest_sdk_exposes_wallet_order_history_surface() -> None:
    assert hasattr(rest_open_api, "OrderHistoryList")
    assert hasattr(WalletDataApi, "get_wallet_order_history")
    assert hasattr(WalletDataApi, "get_wallet_order_history_with_http_info")


def test_ws_info_asset_oracle_prices_payload_parses_without_pool_price() -> None:
    payload = AssetOraclePricesUpdatePayload.model_validate(
        {
            "type": "channel_data",
            "timestamp": 1747927089946,
            "channel": "/v2/assetOraclePrices",
            "data": [
                {
                    "asset": "ETH",
                    "oraclePrice": "2500",
                    "updatedAt": 1747927089946,
                }
            ],
        }
    )

    assert payload.channel == AssetOraclePricesChannel.SLASH_V2_SLASH_ASSET_ORACLE_PRICES
    assert payload.data[0].asset == "ETH"
    assert payload.data[0].oracle_price == "2500"
    serialized = payload.model_dump(mode="json", by_alias=True)
    assert serialized["data"][0] == {
        "asset": "ETH",
        "oraclePrice": "2500",
        "updatedAt": 1747927089946,
    }
    assert "poolPrice" not in serialized["data"][0]


class _RecordingSocket:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, dict[str, Any]]] = []

    def send_subscribe(self, channel: str, **kwargs: Any) -> None:
        self.messages.append(("subscribe", channel, kwargs))

    def send_unsubscribe(self, channel: str, **kwargs: Any) -> None:
        self.messages.append(("unsubscribe", channel, kwargs))


def test_reya_socket_routes_asset_oracle_prices_channel() -> None:
    assert ReyaSocket.CHANNEL_PAYLOAD_MAP["/v2/assetOraclePrices"] is AssetOraclePricesUpdatePayload

    socket = _RecordingSocket()
    prices = PricesResource(socket)  # type: ignore[arg-type]

    prices.asset_oracle_prices.subscribe(batched=True)
    prices.asset_oracle_prices.unsubscribe()

    assert socket.messages == [
        ("subscribe", "/v2/assetOraclePrices", {"batched": True}),
        ("unsubscribe", "/v2/assetOraclePrices", {}),
    ]


def test_reya_socket_routes_preferred_perp_markets_summary_channel() -> None:
    # pylint: disable=protected-access
    assert ReyaSocket.CHANNEL_PAYLOAD_MAP["/v2/perpMarkets/summary"] is MarketsSummaryUpdatePayload
    assert "/v2/markets/summary" not in ReyaSocket.CHANNEL_PAYLOAD_MAP

    socket = ReyaSocket()
    assert socket._get_payload_type("/v2/perpMarket/BTCRUSDPERP/summary") is MarketSummaryUpdatePayload
    assert socket._get_payload_type("/v2/market/BTCRUSDPERP/summary") is None

    payload = MarketsSummaryUpdatePayload.model_validate(
        {
            "type": "channel_data",
            "timestamp": 1747927089946,
            "channel": "/v2/perpMarkets/summary",
            "data": [],
        }
    )

    assert payload.channel is MarketsSummaryChannel.SLASH_V2_SLASH_PERP_MARKETS_SLASH_SUMMARY


def test_reya_socket_parses_order_changes_subscribe_ack_with_snapshot_cursor() -> None:
    socket = ReyaSocket()

    message = socket._parse_message(  # pylint: disable=protected-access
        {
            "type": "subscribed",
            "channel": "/v2/wallet/0x1234567890123456789012345678901234567890/orderChanges",
            "contents": {
                "data": [_base_order_response_payload()],
                "snapshotSequenceNumber": 123456,
            },
        }
    )

    assert isinstance(message, OrderChangesSubscribedPayload)
    assert message.contents.snapshot_sequence_number == 123456
    assert message.contents.data[0].sequence_number is None


def test_rest_create_order_response_parses_cancel_reason_and_fill_range() -> None:
    response = RestCreateOrderResponse.from_dict(
        {
            "status": "CANCELLED",
            "execQty": "0",
            "cumQty": "0",
            "orderId": "490346525705109504",
            "clientOrderId": "42",
            "cancelReason": "SELF_TRADE_PREVENTION",
            "cancelReasonMessage": "self-trade prevention cancelled taker",
            "firstFillId": "9001",
            "fillCount": 1,
        }
    )

    assert response is not None
    assert response.cancel_reason == RestCancelReason.SELF_TRADE_PREVENTION
    assert response.cancel_reason_message == "self-trade prevention cancelled taker"
    assert response.first_fill_id == "9001"
    assert response.fill_count == 1
    assert response.to_dict()["cancelReason"] == "SELF_TRADE_PREVENTION"
    assert response.to_dict()["firstFillId"] == "9001"


def test_rest_create_order_response_requires_order_id() -> None:
    with pytest.raises(ValidationError):
        RestCreateOrderResponse.from_dict({"status": "CANCELLED"})


def test_rest_modify_order_response_parses_cancel_reason_and_fill_range() -> None:
    response = RestModifyOrderResponse.from_dict(
        {
            "status": "CANCELLED",
            "execQty": "0.25",
            "cumQty": "1.25",
            "orderId": "490346525705109504",
            "clientOrderId": "42",
            "cancelReason": "SELF_TRADE_PREVENTION",
            "cancelReasonMessage": "self-trade prevention cancelled taker",
            "firstFillId": "9002",
            "fillCount": 1,
        }
    )

    assert response is not None
    assert response.cancel_reason == RestCancelReason.SELF_TRADE_PREVENTION
    assert response.cancel_reason_message == "self-trade prevention cancelled taker"
    assert response.first_fill_id == "9002"
    assert response.fill_count == 1
    assert response.to_dict()["cancelReason"] == "SELF_TRADE_PREVENTION"
    assert response.to_dict()["firstFillId"] == "9002"


def test_rest_modify_order_response_omits_exec_qty_when_absent() -> None:
    response = RestModifyOrderResponse.from_dict(
        {
            "status": "OPEN",
            "cumQty": "0",
            "orderId": "490346525705109504",
            "clientOrderId": "42",
        }
    )

    assert response is not None
    assert response.exec_qty is None
    assert "execQty" not in response.to_dict()


def test_ws_exec_create_order_response_parses_cancel_reason_and_fill_range() -> None:
    response = WsExecCreateOrderResponse.model_validate(
        {
            "status": "CANCELLED",
            "execQty": "0",
            "cumQty": "0",
            "orderId": "490346525705109504",
            "clientOrderId": "42",
            "cancelReason": "IOC_REMAINDER",
            "cancelReasonMessage": "IOC remainder cancelled",
            "firstFillId": "9001",
            "fillCount": 2,
        }
    )

    assert response.cancel_reason is WsExecCancelReason.IOC_REMAINDER
    assert response.cancel_reason_message == "IOC remainder cancelled"
    assert response.first_fill_id == "9001"
    assert response.fill_count == 2
    assert response.model_dump(mode="json", by_alias=True)["cancelReason"] == "IOC_REMAINDER"


def test_ws_exec_create_order_response_requires_order_id() -> None:
    with pytest.raises(ValidationError):
        WsExecCreateOrderResponse.model_validate({"status": "CANCELLED"})


def test_ws_exec_modify_order_response_parses_cancel_reason_and_fill_range() -> None:
    response = WsExecModifyOrderResponse.model_validate(
        {
            "status": "CANCELLED",
            "execQty": "0.25",
            "cumQty": "1.25",
            "orderId": "490346525705109504",
            "clientOrderId": "42",
            "cancelReason": "SELF_TRADE_PREVENTION",
            "cancelReasonMessage": "self-trade prevention cancelled taker",
            "firstFillId": "9002",
            "fillCount": 1,
        }
    )

    assert response.cancel_reason is WsExecCancelReason.SELF_TRADE_PREVENTION
    assert response.cancel_reason_message == "self-trade prevention cancelled taker"
    assert response.first_fill_id == "9002"
    assert response.fill_count == 1
    assert response.model_dump(mode="json", by_alias=True)["firstFillId"] == "9002"


def test_ws_exec_modify_order_response_omits_exec_qty_when_absent() -> None:
    response = WsExecModifyOrderResponse.model_validate(
        {
            "status": "OPEN",
            "cumQty": "0",
            "orderId": "490346525705109504",
            "clientOrderId": "42",
        }
    )

    assert response.exec_qty is None
    assert "execQty" not in response.model_dump(mode="json", by_alias=True, exclude_none=True)


def test_rest_spot_execution_uses_taker_field_names() -> None:
    execution = RestSpotExecution.from_dict(_base_spot_execution_payload())

    assert execution is not None
    assert execution.taker_account_id == 12345
    assert execution.taker_order_id == "490346525705109504"
    assert execution.taker_fee == "0"
    assert not hasattr(execution, "account_id")
    assert not hasattr(execution, "order_id")
    assert not hasattr(execution, "fee")
    serialized = execution.to_dict()
    assert serialized["takerAccountId"] == 12345
    assert serialized["takerOrderId"] == "490346525705109504"
    assert serialized["takerFee"] == "0"


def test_ws_info_spot_execution_uses_taker_field_names() -> None:
    execution = WsInfoSpotExecution.model_validate(_base_spot_execution_payload())

    assert execution.taker_account_id == 12345
    assert execution.taker_order_id == "490346525705109504"
    assert execution.taker_fee == "0"
    assert not hasattr(execution, "account_id")
    assert not hasattr(execution, "order_id")
    assert not hasattr(execution, "fee")
    serialized = execution.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized["takerAccountId"] == 12345
    assert serialized["takerOrderId"] == "490346525705109504"
    assert serialized["takerFee"] == "0"


def test_rest_execution_bust_uses_taker_field_names() -> None:
    bust = RestExecutionBust.from_dict(_base_execution_bust_payload())

    assert bust is not None
    assert bust.taker_account_id == 12345
    assert bust.taker_order_id == "490346525705109504"
    assert not hasattr(bust, "account_id")
    assert not hasattr(bust, "order_id")
    serialized = bust.to_dict()
    assert serialized["takerAccountId"] == 12345
    assert serialized["takerOrderId"] == "490346525705109504"
    assert serialized["reason"]["reasonName"] == "AccountBelowIM"


def test_ws_info_execution_bust_uses_taker_field_names() -> None:
    bust = WsInfoExecutionBust.model_validate(_base_execution_bust_payload())

    assert bust.taker_account_id == 12345
    assert bust.taker_order_id == "490346525705109504"
    assert not hasattr(bust, "account_id")
    assert not hasattr(bust, "order_id")
    serialized = bust.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized["takerAccountId"] == 12345
    assert serialized["takerOrderId"] == "490346525705109504"
    assert serialized["reason"]["reasonName"] == "AccountBelowIM"


def test_ws_info_order_parses_cancel_reason_and_fill_range() -> None:
    order = WsInfoOrder.model_validate(
        {
            **_base_order_response_payload(),
            "firstFillId": "9001",
            "fillCount": 1,
            "sequenceNumber": 123457,
            "status": "CANCELLED",
            "cancelReason": "CANCEL_ALL_AFTER",
            "cancelReasonMessage": "cancel-on-disconnect fired",
        }
    )

    assert order.cancel_reason is WsInfoCancelReason.CANCEL_ALL_AFTER
    assert order.cancel_reason_message == "cancel-on-disconnect fired"
    assert order.first_fill_id == "9001"
    assert order.fill_count == 1
    assert order.sequence_number == 123457
    serialized = order.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized["cancelReason"] == "CANCEL_ALL_AFTER"
    assert serialized["sequenceNumber"] == 123457
    assert "expiresAfter" not in serialized


def test_ws_info_order_changes_update_payload_parses_sequence_numbers() -> None:
    payload = OrderChangeUpdatePayload.model_validate(
        {
            "type": "channel_data",
            "timestamp": 1745000002,
            "channel": "/v2/wallet/0x1234567890123456789012345678901234567890/orderChanges",
            "data": [
                {
                    **_base_order_response_payload(),
                    "status": "FILLED",
                    "execQty": "1",
                    "cumQty": "1",
                    "sequenceNumber": 123458,
                }
            ],
        }
    )

    assert payload.data[0].sequence_number == 123458
    serialized = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized["data"][0]["sequenceNumber"] == 123458


def test_ws_info_order_changes_subscribed_payload_parses_snapshot_cursor() -> None:
    payload = OrderChangesSubscribedPayload.model_validate(
        {
            "type": "subscribed",
            "channel": "/v2/wallet/0x1234567890123456789012345678901234567890/orderChanges",
            "contents": {
                "data": [_base_order_response_payload()],
                "snapshotSequenceNumber": 123456,
            },
        }
    )

    assert payload.contents.snapshot_sequence_number == 123456
    assert payload.contents.data[0].sequence_number is None
    serialized = payload.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert serialized["contents"]["snapshotSequenceNumber"] == 123456
    assert "sequenceNumber" not in serialized["contents"]["data"][0]
