"""Offline checks for generated API models that track specs PR 59."""

from __future__ import annotations

import pytest

import sdk.open_api as rest_open_api
from sdk.async_api.cancel_reason import CancelReason as WsInfoCancelReason
from sdk.async_api.order import Order as WsInfoOrder
from sdk.async_exec_api.cancel_reason import CancelReason as WsExecCancelReason
from sdk.async_exec_api.create_order_response import CreateOrderResponse as WsExecCreateOrderResponse
from sdk.async_exec_api.request_error_code import RequestErrorCode as WsExecRequestErrorCode
from sdk.open_api import CancelReason as RestCancelReason
from sdk.open_api import CreateOrderResponse as RestCreateOrderResponse
from sdk.open_api import RequestErrorCode as RestRequestErrorCode
from sdk.open_api.api.reference_data_api import ReferenceDataApi

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
}


def test_rest_request_error_code_exports_pro_405_codes() -> None:
    assert PRO_405_REQUEST_ERROR_CODES <= {code.value for code in RestRequestErrorCode}


def test_ws_exec_request_error_code_exports_pro_405_codes() -> None:
    assert PRO_405_REQUEST_ERROR_CODES <= {code.value for code in WsExecRequestErrorCode}


def test_cancel_reason_enums_share_specs_values() -> None:
    assert CANCEL_REASONS == {reason.value for reason in RestCancelReason}
    assert CANCEL_REASONS == {reason.value for reason in WsInfoCancelReason}
    assert CANCEL_REASONS == {reason.value for reason in WsExecCancelReason}


def test_rest_sdk_omits_removed_amm_liquidity_parameters_surface() -> None:
    assert not hasattr(rest_open_api, "LiquidityParameters")
    assert not hasattr(ReferenceDataApi, "get_liquidity_parameters")
    assert hasattr(ReferenceDataApi, "get_perp_market_definitions")


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


def test_ws_info_order_parses_cancel_reason_and_fill_range() -> None:
    order = WsInfoOrder.model_validate(
        {
            "exchangeId": 2,
            "symbol": "ETHRUSDPERP",
            "accountId": 12345,
            "orderId": "490346525705109504",
            "qty": "1",
            "execQty": "0",
            "cumQty": "0",
            "firstFillId": "9001",
            "fillCount": 1,
            "side": "B",
            "limitPx": "2500",
            "orderType": "LIMIT",
            "triggerPx": "0",
            "timeInForce": "GTC",
            "expiresAfter": 0,
            "reduceOnly": False,
            "postOnly": True,
            "status": "CANCELLED",
            "createdAt": 1745000000,
            "lastUpdateAt": 1745000001,
            "cancelReason": "CANCEL_ALL_AFTER",
            "cancelReasonMessage": "cancel-on-disconnect fired",
        }
    )

    assert order.cancel_reason is WsInfoCancelReason.CANCEL_ALL_AFTER
    assert order.cancel_reason_message == "cancel-on-disconnect fired"
    assert order.first_fill_id == "9001"
    assert order.fill_count == 1
    assert order.model_dump(mode="json", by_alias=True)["cancelReason"] == "CANCEL_ALL_AFTER"
