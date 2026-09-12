"""Generated unions must preserve actual REST/WS payloads and reject flags."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from pydantic import ValidationError

from sdk.async_exec_api.limit_modify_order_request import LimitModifyOrderRequest as WsLimit
from sdk.async_exec_api.trigger_modify_order_request import TriggerModifyOrderRequest as WsTrigger
from sdk.open_api.models.limit_modify_order_request import LimitModifyOrderRequest
from sdk.open_api.models.modify_order_request import ModifyOrderRequest
from sdk.open_api.models.trigger_modify_order_request import TriggerModifyOrderRequest
from sdk.reya_rest_api.client import ReyaTradingClient
from sdk.reya_rest_api.models.orders import ModifyOrderParameters
from sdk.reya_ws_exec.client import ReyaWsExecClient

pytestmark = pytest.mark.offline


def payload(order_type: str) -> dict:
    body = {
        "orderId": "123",
        "symbol": "ETHRUSDPERP",
        "accountId": 1,
        "exchangeId": 1,
        "isBuy": False,
        "limitPx": "2500",
        "timeInForce": "GTC",
        "orderType": order_type,
        "signature": "0x1234",
        "nonce": "1",
        "signerWallet": "0x" + "11" * 20,
        "deadline": 1900000000,
    }
    if order_type == "LIMIT":
        body.update(qty="1", postOnly=False, reduceOnly=False)
    else:
        body["triggerPx"] = "2510"
    return body


@pytest.mark.parametrize("order_type", ["LIMIT", "STOP_LOSS", "TAKE_PROFIT"])
async def test_real_client_senders_preserve_union_payload(order_type: str) -> None:
    expected = payload(order_type)
    client = Mock(spec=ReyaTradingClient)
    client.build_modify_order_payload.return_value = (expected, 1)
    client.orders.modify_order = AsyncMock()
    params = Mock(spec=ModifyOrderParameters)

    await ReyaTradingClient.modify_order(client, params)
    request = client.orders.modify_order.call_args.args[0]
    assert request.actual_instance is not None
    assert request.to_dict() == expected

    ws = ReyaWsExecClient(rest_client=client, ws_url="ws://localhost")
    # Capture serialization without opening a connection.
    with patch.object(ws, "_send_and_await", new_callable=AsyncMock) as sender:
        sender.return_value = {"status": "OPEN", "orderId": "123"}
        await ws.modify_order(params)
        operation, request = sender.call_args.args
    assert operation == "modifyOrder"
    assert request.model_dump(mode="json", by_alias=True, exclude_none=True) == expected


@pytest.mark.parametrize("order_type", ["LIMIT", "STOP_LOSS", "TAKE_PROFIT"])
def test_generated_rest_union_round_trip(order_type: str) -> None:
    body = payload(order_type)
    # from_dict must not introduce null flags while resolving the oneOf branch.
    assert ModifyOrderRequest.from_dict(body).to_dict() == body


@pytest.mark.parametrize("model", [TriggerModifyOrderRequest, WsTrigger])
@pytest.mark.parametrize("order_type", ["STOP_LOSS", "TAKE_PROFIT"])
@pytest.mark.parametrize("field", ["postOnly", "reduceOnly"])
@pytest.mark.parametrize("value", [False, True, None])
def test_generated_trigger_models_reject_present_flags(model, order_type, field, value) -> None:
    with pytest.raises(ValidationError, match="must be omitted"):
        model(**{**payload(order_type), field: value})


@pytest.mark.parametrize("model", [LimitModifyOrderRequest, WsLimit])
@pytest.mark.parametrize("field", ["postOnly", "reduceOnly"])
def test_generated_limit_models_require_flags(model, field) -> None:
    body = payload("LIMIT")
    del body[field]
    with pytest.raises(ValidationError):
        model(**body)
