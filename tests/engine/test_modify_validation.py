"""
modifyOrder server-side validation tests — live e2e.

- EMPTY_MODIFY_ERROR: an exact restate (no field changed) is rejected,
- ORDER_NOT_FOUND: bogus targets, just-cancelled orders, and fully-filled
  orders,
- INPUT_VALIDATION_ERROR: zero px / zero qty / both-or-neither targeting /
  clientOrderId=0 — driven as RAW `ModifyOrderRequest`s through the generated
  OrderEntryApi because the typed `ModifyOrderParameters` path pre-empts the
  targeting mistakes client-side (pinned offline in
  tests/validation/test_client_guards.py). Mirrors the raw-request precedent
  in tests/spot/test_api_validation.py.
- INPUT_VALIDATION_ERROR via raw JSON POSTs: each of the four required
  modifiable fields omitted, plus negative limitPx/qty — the generated
  pydantic request model can't express an omitted required field or a
  negative qty, so these go over aiohttp directly (same precedent file).
- UNAUTHORIZED_SIGNATURE_ERROR: signature over one post-modify state, wire
  payload carrying another,
- MODIFY_ORDER_OTHER_ERROR: TP/SL trigger orders are not modifiable (the
  code is the handler's catch-all, so the discriminating message is pinned
  too).
"""

from typing import Any

import time
from decimal import Decimal

import aiohttp
import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.modify_order_request import ModifyOrderRequest
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, TimeInForceInt
from sdk.reya_rest_api.models.orders import ModifyOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder, TriggerOrderBuilder
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.order_lifecycle import assert_px_qty, rest_spot_gtc
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.modify, pytest.mark.validation]

PERP_SYMBOL = "ETHRUSDPERP"
BOGUS_ORDER_ID = 999_999_999_999_999_999


def _raw_modify_request(
    tester: ReyaTester,
    spot_config: SpotTestConfig,
    limit_px: str,
    qty: str,
    order_id: int | None = None,
    client_order_id: int | None = None,
) -> ModifyOrderRequest:
    """Build a raw ModifyOrderRequest with a REAL signature over exactly the
    values sent (post-modify state of a resting buy GTC), so the server-side
    INPUT validation — not signature recovery — is what rejects."""
    nonce = tester.get_next_nonce()
    deadline = int(time.time()) + 60
    signature = tester.client.signature_generator.sign_order(
        account_id=tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=tester.client.config.dex_id,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal(qty),
        limit_price=Decimal(limit_px),
        trigger_price=Decimal(0),
        time_in_force=int(TimeInForceInt.GTC),
        client_order_id=0,
        reduce_only=False,
        expires_after=0,
        nonce=nonce,
        deadline=deadline,
        post_only=False,
    )
    return ModifyOrderRequest(
        orderId=str(order_id) if order_id is not None else None,
        clientOrderId=client_order_id,
        symbol=spot_config.symbol,
        accountId=tester.account_id,
        # Restated immutables (full-restate) — exactly the values signed above,
        # so the signature stays valid and input validation is what rejects.
        exchangeId=tester.client.config.dex_id,
        isBuy=True,
        orderType="LIMIT",
        timeInForce="GTC",
        triggerPx=None,
        reduceOnly=False,
        limitPx=limit_px,
        qty=qty,
        postOnly=False,
        expiresAfter=0,
        signature=signature,
        nonce=str(nonce),
        signerWallet=tester.client.signer_wallet_address,
        deadline=deadline,
    )


def _strip_nones(payload: dict) -> dict:
    """Drop None entries — mirrors what the SDK transports put on the wire."""
    return {key: value for key, value in payload.items() if value is not None}


@pytest.mark.spot
@pytest.mark.asyncio
async def test_empty_modify_rejected(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """An exact restate of the current order state is EMPTY_MODIFY_ERROR."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)

    with pytest.raises(ApiException) as exc_info:
        await spot_tester.client.modify_order(full_state_modify_params(order))
    error_msg = str(exc_info.value)
    assert "EMPTY_MODIFY_ERROR" in error_msg, f"Expected EMPTY_MODIFY_ERROR, got: {error_msg[:200]}"
    logger.info("✅ Exact restate rejected with EMPTY_MODIFY_ERROR")

    still_open = await spot_tester.data.open_order(order.order_id)
    assert still_open is not None, "Rejected empty modify must leave the order resting"

    await spot_tester.orders.cancel(
        order_id=order.order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_order_not_found(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """Both a bogus orderId and a just-cancelled order resolve to ORDER_NOT_FOUND."""
    bogus_params = ModifyOrderParameters(
        symbol=spot_config.symbol,
        is_buy=True,
        limit_px=str(spot_config.price(0.95)),
        qty=spot_config.min_qty,
        post_only=False,
        expires_after=0,
        time_in_force=TimeInForce.GTC,
        order_id=BOGUS_ORDER_ID,
    )
    with pytest.raises(ApiException) as exc_info:
        await spot_tester.client.modify_order(bogus_params)
    error_msg = str(exc_info.value)
    assert "ORDER_NOT_FOUND" in error_msg, f"Expected ORDER_NOT_FOUND for bogus id, got: {error_msg[:200]}"
    logger.info("✅ Bogus orderId rejected with ORDER_NOT_FOUND")

    # A real order that was JUST cancelled is equally not-found.
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    await spot_tester.orders.cancel(
        order_id=order.order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
    )
    await spot_tester.wait.for_order_state(order.order_id, OrderStatus.CANCELLED, timeout=10)

    with pytest.raises(ApiException) as exc_info:
        await spot_tester.client.modify_order(full_state_modify_params(order, limit_px=str(spot_config.price(0.96))))
    error_msg = str(exc_info.value)
    assert "ORDER_NOT_FOUND" in error_msg, f"Expected ORDER_NOT_FOUND for cancelled order, got: {error_msg[:200]}"
    logger.info("✅ Just-cancelled order rejected with ORDER_NOT_FOUND")

    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_modify_after_full_fill_not_found(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """A FULLY-FILLED order is no longer a live resting order — modifying its
    stale snapshot resolves to ORDER_NOT_FOUND."""
    await skip_if_external_config_liquidity(spot_config, maker_tester, "A deterministic full fill needs an empty book.")
    queue_px = str(spot_config.price(0.99))

    maker_order = await rest_spot_gtc(maker_tester, spot_config, price_multiplier=0.99, is_buy=True)

    try:
        ioc = OrderBuilder.from_config(spot_config).sell().price(queue_px).qty(spot_config.min_qty).ioc().build()
        taker_order_id = await taker_tester.orders.create_limit(ioc)
        assert taker_order_id is not None
        await maker_tester.wait.for_order_state(maker_order.order_id, OrderStatus.FILLED, timeout=10)
        logger.info(f"Maker order {maker_order.order_id} fully filled")

        with pytest.raises(ApiException) as exc_info:
            await maker_tester.client.modify_order(
                full_state_modify_params(maker_order, limit_px=str(spot_config.price(0.98)))
            )
        error_msg = str(exc_info.value)
        assert "ORDER_NOT_FOUND" in error_msg, f"Expected ORDER_NOT_FOUND for filled order, got: {error_msg[:200]}"
        logger.info("✅ Fully-filled order rejected with ORDER_NOT_FOUND")
    finally:
        await maker_tester.orders.close_all(fail_if_none=False)
        await taker_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.asyncio
async def test_invalid_values_raw(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """Raw-request INPUT_VALIDATION_ERROR sweep against ONE resting order:
    zero px, zero qty, both targeting ids, neither id, clientOrderId=0. The
    resting order must come through every rejection untouched."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(spot_config.price(0.96))
    good_qty = spot_config.min_qty

    cases: list[tuple[str, dict[str, Any]]] = [
        ("zero px", {"limit_px": "0", "qty": good_qty, "order_id": order_id}),
        ("zero qty", {"limit_px": good_px, "qty": "0", "order_id": order_id}),
        ("both ids", {"limit_px": good_px, "qty": good_qty, "order_id": order_id, "client_order_id": 777}),
        ("neither id", {"limit_px": good_px, "qty": good_qty}),
        ("clientOrderId=0", {"limit_px": good_px, "qty": good_qty, "client_order_id": 0}),
    ]
    try:
        for label, kwargs in cases:
            request = _raw_modify_request(spot_tester, spot_config, **kwargs)
            with pytest.raises(ApiException) as exc_info:
                await spot_tester.client.orders.modify_order(request)
            error_msg = str(exc_info.value)
            assert (
                "INPUT_VALIDATION_ERROR" in error_msg
            ), f"[{label}] expected INPUT_VALIDATION_ERROR, got: {error_msg[:200]}"
            logger.info(f"✅ [{label}] rejected with INPUT_VALIDATION_ERROR")

        untouched = await spot_tester.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modifies must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.asyncio
async def test_required_fields_and_negative_values_raw(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """The four modifiable fields are REQUIRED on the wire (no
    omitted-means-inherited shorthand), and negative limitPx/qty are rejected
    — INPUT_VALIDATION_ERROR for every case, resting order untouched.

    Driven as raw JSON POSTs over aiohttp (precedent:
    tests/spot/test_api_validation.py) because the generated pydantic
    `ModifyOrderRequest` cannot express an omitted required field or a
    negative qty. Each payload starts from a fully-signed valid modify (fresh
    nonce per case) and mutates exactly one wire field; the API-layer
    validator rejects before signature recovery, so the signed-vs-wire
    mismatch never surfaces."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(spot_config.price(0.96))

    url = f"{spot_tester.client.config.api_url}/modifyOrder"
    omit_cases = ["limitPx", "qty", "postOnly", "expiresAfter"]
    negative_cases = [("limitPx", f"-{good_px}"), ("qty", f"-{spot_config.min_qty}")]

    try:
        async with aiohttp.ClientSession() as session:
            for field_name in omit_cases:
                payload, _nonce = spot_tester.client.build_modify_order_payload(
                    full_state_modify_params(order, limit_px=good_px)
                )
                request_body = _strip_nones(payload)
                del request_body[field_name]
                async with session.post(url, json=request_body) as resp:
                    body = await resp.text()
                    assert resp.status == 400, f"[omit {field_name}] expected HTTP 400, got {resp.status}: {body[:200]}"
                    assert (
                        "INPUT_VALIDATION_ERROR" in body
                    ), f"[omit {field_name}] expected INPUT_VALIDATION_ERROR, got: {body[:200]}"
                logger.info(f"✅ [omit {field_name}] rejected with INPUT_VALIDATION_ERROR")

            for field_name, bad_value in negative_cases:
                payload, _nonce = spot_tester.client.build_modify_order_payload(
                    full_state_modify_params(order, limit_px=good_px)
                )
                request_body = _strip_nones(payload)
                request_body[field_name] = bad_value
                async with session.post(url, json=request_body) as resp:
                    body = await resp.text()
                    assert (
                        resp.status == 400
                    ), f"[negative {field_name}] expected HTTP 400, got {resp.status}: {body[:200]}"
                    assert (
                        "INPUT_VALIDATION_ERROR" in body
                    ), f"[negative {field_name}] expected INPUT_VALIDATION_ERROR, got: {body[:200]}"
                logger.info(f"✅ [negative {field_name}] rejected with INPUT_VALIDATION_ERROR")

        untouched = await spot_tester.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modifies must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.asyncio
async def test_tampered_signature(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """Sign one post-modify state, send a different qty on the wire →
    UNAUTHORIZED_SIGNATURE_ERROR, order untouched."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None

    signed_qty = str(Decimal(spot_config.min_qty) * 2)
    wire_qty = str(Decimal(spot_config.min_qty) * 3)
    payload, _nonce = spot_tester.client.build_modify_order_payload(full_state_modify_params(order, qty=signed_qty))
    payload["qty"] = wire_qty  # signature still covers signed_qty

    try:
        with pytest.raises(ApiException) as exc_info:
            await spot_tester.client.orders.modify_order(ModifyOrderRequest(**payload))
        error_msg = str(exc_info.value)
        assert (
            "UNAUTHORIZED_SIGNATURE_ERROR" in error_msg
        ), f"Expected UNAUTHORIZED_SIGNATURE_ERROR, got: {error_msg[:200]}"
        logger.info("✅ Tampered modify rejected with UNAUTHORIZED_SIGNATURE_ERROR")

        untouched = await spot_tester.data.open_order(order.order_id)
        assert untouched is not None
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.perp
@pytest.mark.trigger
@pytest.mark.asyncio
async def test_trigger_order_not_modifiable(perp_maker_tester: ReyaTester):
    """TP/SL trigger orders cannot be modified — MODIFY_ORDER_OTHER_ERROR.

    The code is the handler's catch-all (also returned for rate limiting,
    kill-switches, balance and px/qty spacing failures), so the discriminating
    message is pinned too."""
    market_def = await perp_maker_tester.get_market_definition(PERP_SYMBOL)
    min_qty = str(market_def.min_order_qty)
    oracle_price = float(await perp_maker_tester.data.current_price(PERP_SYMBOL))
    far_trigger_px = str(round(oracle_price * 10, 2))

    trigger_params = (
        TriggerOrderBuilder()
        .symbol(PERP_SYMBOL)
        .sell()
        .qty(min_qty)
        .trigger_price(far_trigger_px)
        .take_profit()
        .build()
    )
    response = await perp_maker_tester.orders.create_trigger(trigger_params)
    assert response.order_id is not None
    trigger_order_id = response.order_id

    try:
        modify_params = ModifyOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=str(round(oracle_price * 9, 2)),
            qty=min_qty,
            post_only=False,
            expires_after=0,
            time_in_force=TimeInForce.GTC,
            order_id=int(trigger_order_id),
            trigger_px=far_trigger_px,
        )
        with pytest.raises(ApiException) as exc_info:
            await perp_maker_tester.client.modify_order(modify_params)
        error_msg = str(exc_info.value)
        assert "MODIFY_ORDER_OTHER_ERROR" in error_msg, f"Expected MODIFY_ORDER_OTHER_ERROR, got: {error_msg[:200]}"
        assert (
            "Only LIMIT orders can be modified" in error_msg
        ), f"Expected the trigger-modify message, got: {error_msg[:200]}"
        logger.info(
            "✅ Trigger order modify rejected with MODIFY_ORDER_OTHER_ERROR / 'Only LIMIT orders can be modified'"
        )
    finally:
        try:
            await perp_maker_tester.client.cancel_order(
                order_id=trigger_order_id, symbol=PERP_SYMBOL, account_id=perp_maker_tester.account_id
            )
        except ApiException as e:
            logger.warning(f"Trigger order cleanup cancel failed (may already be gone): {e}")
