"""
modifyOrder server-side validation tests — live e2e.

- EMPTY_MODIFY_ERROR: an exact restate (no field changed) is rejected,
- ORDER_NOT_FOUND: bogus targets, just-cancelled orders, and fully-filled
  orders,
- INPUT_VALIDATION_ERROR: zero px / zero qty / neither id / clientOrderId=0 —
  driven as RAW `ModifyOrderRequest`s through the generated OrderEntryApi
  because the typed `ModifyOrderParameters` path pre-empts these client-side
  (pinned offline in tests/validation/test_client_guards.py). Mirrors the
  raw-request precedent in tests/spot/test_api_validation.py. (Supplying BOTH
  ids is valid under full-restate, so it is not a rejection case.)
- MODIFY_IMMUTABLE_MISMATCH (surfaced as INPUT_VALIDATION_ERROR): a restated
  immutable that doesn't match the resting order — driven raw with a valid
  signature over a flipped side.
- INPUT_VALIDATION_ERROR via raw JSON POSTs: each of the four required
  modifiable fields omitted, plus negative limitPx/qty — the generated
  pydantic request model can't express an omitted required field or a
  negative qty, so these go over aiohttp directly (same precedent file).
- UNAUTHORIZED_SIGNATURE_ERROR: signature over one post-modify state, wire
  payload carrying another,
- TP/SL trigger orders are not modifiable: the typed SDK restates
  orderType=LIMIT, mismatching the resting trigger order, so the engine
  rejects with MODIFY_IMMUTABLE_MISMATCH (INPUT_VALIDATION_ERROR).
"""

from typing import Any

import time
from decimal import Decimal

import aiohttp
import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.modify_order_request import ModifyOrderRequest
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
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


_TIF_TO_INT = {
    TimeInForce.GTC: int(TimeInForceInt.GTC),
    TimeInForce.GTT: int(TimeInForceInt.GTT),
    TimeInForce.IOC: int(TimeInForceInt.IOC),
}


def _raw_modify_request(
    tester: ReyaTester,
    spot_config: SpotTestConfig,
    limit_px: str,
    qty: str,
    order_id: int | None = None,
    client_order_id: int | None = None,
    is_buy: bool = True,
    exchange_id: int | None = None,
    time_in_force: TimeInForce = TimeInForce.GTC,
    expires_after: int = 0,
    reduce_only: bool = False,
    post_only: bool = False,
    trigger_px: str | None = None,
    nonce: int | None = None,
) -> ModifyOrderRequest:
    """Build a raw ModifyOrderRequest with a REAL signature over exactly the
    values sent (post-modify state of a resting buy GTC), so the server-side
    INPUT validation / immutable-match — not signature recovery — is what
    rejects. Every restated immutable (`is_buy`, `exchange_id`, `time_in_force`,
    `reduce_only`, `client_order_id`, `trigger_px`) is both signed and wired, so
    flipping any one forges an immutable mismatch with a still-valid signature.
    `nonce` can be pinned to replay a consumed nonce."""
    resolved_exchange_id = exchange_id if exchange_id is not None else tester.client.config.dex_id
    resolved_nonce = nonce if nonce is not None else tester.get_next_nonce()
    deadline = int(time.time()) + 60
    signed_cloid = client_order_id if client_order_id is not None else 0
    signature = tester.client.signature_generator.sign_order(
        account_id=tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=resolved_exchange_id,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=is_buy,
        qty=Decimal(qty),
        limit_price=Decimal(limit_px),
        trigger_price=Decimal(trigger_px) if trigger_px is not None else Decimal(0),
        time_in_force=_TIF_TO_INT[time_in_force],
        client_order_id=signed_cloid,
        reduce_only=reduce_only,
        expires_after=expires_after,
        nonce=resolved_nonce,
        deadline=deadline,
        post_only=post_only,
    )
    return ModifyOrderRequest(
        orderId=str(order_id) if order_id is not None else None,
        clientOrderId=str(client_order_id) if client_order_id is not None else None,
        symbol=spot_config.symbol,
        accountId=tester.account_id,
        # Restated immutables (full-restate) — exactly the values signed above,
        # so the signature stays valid and input validation is what rejects.
        exchangeId=resolved_exchange_id,
        isBuy=is_buy,
        orderType=OrderType.LIMIT,
        timeInForce=time_in_force,
        triggerPx=trigger_px,
        reduceOnly=reduce_only,
        limitPx=limit_px,
        qty=qty,
        postOnly=post_only,
        expiresAfter=expires_after,
        signature=signature,
        nonce=str(resolved_nonce),
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
    zero px, zero qty, neither id, clientOrderId=0. The resting order must come
    through every rejection untouched. (Supplying BOTH orderId and clientOrderId
    is valid under full-restate — orderId targets, clientOrderId is the restated
    immutable — so it is not part of this rejection sweep.)"""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(spot_config.price(0.96))
    good_qty = spot_config.min_qty

    cases: list[tuple[str, dict[str, Any]]] = [
        ("zero px", {"limit_px": "0", "qty": good_qty, "order_id": order_id}),
        ("zero qty", {"limit_px": good_px, "qty": "0", "order_id": order_id}),
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
async def test_immutable_mismatch_raw(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """A restated immutable that doesn't match the resting order is rejected by
    the matching engine's immutable-match (MODIFY_IMMUTABLE_MISMATCH, surfaced
    as INPUT_VALIDATION_ERROR), and the resting order is left untouched. The
    signature is valid over the tampered side, so it is the immutable-match —
    not signature recovery — that rejects (full-restate end-to-end)."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)  # resting BUY
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    try:
        # Restate the side flipped (sell) against a resting buy — every other
        # field is correct and the signature is valid over the flipped side, so
        # only the engine's immutable-match can reject it.
        request = _raw_modify_request(
            spot_tester,
            spot_config,
            limit_px=str(spot_config.price(0.96)),
            qty=spot_config.min_qty,
            order_id=order_id,
            is_buy=False,
        )
        with pytest.raises(ApiException) as exc_info:
            await spot_tester.client.orders.modify_order(request)
        error_msg = str(exc_info.value)
        assert (
            "INPUT_VALIDATION_ERROR" in error_msg
        ), f"Expected INPUT_VALIDATION_ERROR (immutable mismatch), got: {error_msg[:200]}"
        logger.info("✅ Restated immutable mismatch (side) rejected with INPUT_VALIDATION_ERROR")

        untouched = await spot_tester.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modify must leave the order resting"
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
    """TP/SL trigger orders cannot be modified. Under full-restate the typed SDK
    restates `orderType=LIMIT`, which mismatches the resting trigger order's
    type, so the matching engine rejects it via the immutable-match
    (`MODIFY_IMMUTABLE_MISMATCH`, surfaced as `INPUT_VALIDATION_ERROR`)."""
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
        assert (
            "INPUT_VALIDATION_ERROR" in error_msg
        ), f"Expected INPUT_VALIDATION_ERROR (immutable mismatch on orderType), got: {error_msg[:200]}"
        logger.info("✅ Trigger order modify rejected with INPUT_VALIDATION_ERROR (orderType immutable mismatch)")
    finally:
        try:
            await perp_maker_tester.client.cancel_order(
                order_id=trigger_order_id, symbol=PERP_SYMBOL, account_id=perp_maker_tester.account_id
            )
        except ApiException as e:
            logger.warning(f"Trigger order cleanup cancel failed (may already be gone): {e}")


@pytest.mark.spot
@pytest.mark.parametrize("field_label", ["exchangeId", "timeInForce", "reduceOnly", "clientOrderId"])
@pytest.mark.asyncio
async def test_immutable_mismatch_fields_raw(spot_config: SpotTestConfig, spot_tester: ReyaTester, field_label: str):
    """Each restated immutable field that differs from the resting order is
    rejected by the ME immutable-match (MODIFY_IMMUTABLE_MISMATCH, surfaced as
    INPUT_VALIDATION_ERROR). Driven raw with a VALID signature over the flipped
    value so input/immutable validation — not signature recovery — rejects.
    Extends the isBuy-only `test_immutable_mismatch_raw` across the rest of the
    immutable set (orderType is covered by `test_trigger_order_not_modifiable`)."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)  # resting BUY GTC, cl_ord_id 0
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(spot_config.price(0.96))

    overrides: dict[str, dict[str, Any]] = {
        "exchangeId": {"exchange_id": spot_tester.client.config.dex_id + 1},
        "timeInForce": {"time_in_force": TimeInForce.GTT, "expires_after": int(time.time()) + 3600},
        "reduceOnly": {"reduce_only": True},
        "clientOrderId": {"client_order_id": int(time.time() * 1_000_000)},
    }
    case_overrides = overrides[field_label]

    try:
        request = _raw_modify_request(
            spot_tester, spot_config, limit_px=good_px, qty=spot_config.min_qty, order_id=order_id, **case_overrides
        )
        with pytest.raises(ApiException) as exc_info:
            await spot_tester.client.orders.modify_order(request)
        error_msg = str(exc_info.value)
        assert (
            "INPUT_VALIDATION_ERROR" in error_msg
        ), f"[{field_label}] expected INPUT_VALIDATION_ERROR (immutable mismatch), got: {error_msg[:200]}"
        logger.info(f"✅ [{field_label}] restated-immutable mismatch rejected with INPUT_VALIDATION_ERROR")

        untouched = await spot_tester.data.open_order(order.order_id)
        assert untouched is not None, f"[{field_label}] rejected modify must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_modify_unauthorized_cross_account(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """Account B cannot modify account A's resting order: the ME ownership check
    (account_id mismatch) rejects it, surfaced as MODIFY_ORDER_OTHER_ERROR. The
    maker's order is left resting."""
    maker_order = await rest_spot_gtc(maker_tester, spot_config, price_multiplier=0.95, is_buy=True)
    original_px, original_qty = maker_order.limit_px, maker_order.qty
    assert original_px is not None and original_qty is not None

    try:
        # taker (account B) restates maker's order, signed with B's key/account.
        with pytest.raises(ApiException) as exc_info:
            await taker_tester.client.modify_order(
                full_state_modify_params(maker_order, limit_px=str(spot_config.price(0.96)))
            )
        error_msg = str(exc_info.value)
        assert (
            "MODIFY_ORDER_OTHER_ERROR" in error_msg
        ), f"expected MODIFY_ORDER_OTHER_ERROR (cross-account UNAUTHORIZED), got: {error_msg[:200]}"
        logger.info("✅ Cross-account modify rejected with MODIFY_ORDER_OTHER_ERROR")

        untouched = await maker_tester.data.open_order(maker_order.order_id)
        assert untouched is not None, "Maker's order must survive a cross-account modify attempt"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker_tester.orders.cancel(
            order_id=maker_order.order_id, symbol=spot_config.symbol, account_id=maker_tester.account_id
        )
        await maker_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_modify_replayed_nonce_rejected(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """A nonce is single-use: a second modify reusing a consumed nonce is
    rejected with INVALID_NONCE_ERROR. Driven raw so the nonce is pinned across
    both requests; the first modify (a real px change) consumes the nonce."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    order_id = int(order.order_id)
    px1 = str(spot_config.price(0.96))
    px2 = str(spot_config.price(0.97))
    nonce = spot_tester.get_next_nonce()

    try:
        first = _raw_modify_request(
            spot_tester, spot_config, limit_px=px1, qty=spot_config.min_qty, order_id=order_id, nonce=nonce
        )
        await spot_tester.client.orders.modify_order(first)
        logger.info(f"✅ First modify accepted (nonce {nonce} consumed)")

        replay = _raw_modify_request(
            spot_tester, spot_config, limit_px=px2, qty=spot_config.min_qty, order_id=order_id, nonce=nonce
        )
        with pytest.raises(ApiException) as exc_info:
            await spot_tester.client.orders.modify_order(replay)
        error_msg = str(exc_info.value)
        assert (
            "INVALID_NONCE_ERROR" in error_msg
        ), f"expected INVALID_NONCE_ERROR on a replayed nonce, got: {error_msg[:200]}"
        logger.info("✅ Replayed nonce rejected with INVALID_NONCE_ERROR")
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)


@pytest.mark.spot
@pytest.mark.asyncio
async def test_modify_signature_envelope_raw(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """The signature envelope is required + validated: a missing signature, a
    zero nonce, and a missing signerWallet are each rejected (HTTP 400). Driven
    as raw JSON POSTs (the typed model can't express an omitted/empty envelope
    field), mutating exactly one envelope field per case off a fully-valid
    signed modify; the resting order survives every rejection."""
    order = await rest_spot_gtc(spot_tester, spot_config, price_multiplier=0.95)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(spot_config.price(0.96))
    url = f"{spot_tester.client.config.api_url}/modifyOrder"

    def _omit_signature(body: dict) -> None:
        body.pop("signature", None)

    def _zero_nonce(body: dict) -> None:
        body["nonce"] = "0"

    def _omit_signer(body: dict) -> None:
        body.pop("signerWallet", None)

    cases = [
        ("missing signature", _omit_signature),
        ("zero nonce", _zero_nonce),
        ("missing signerWallet", _omit_signer),
    ]

    try:
        async with aiohttp.ClientSession() as session:
            for label, mutate in cases:
                payload, _nonce = spot_tester.client.build_modify_order_payload(
                    full_state_modify_params(order, limit_px=good_px)
                )
                request_body = _strip_nones(payload)
                mutate(request_body)
                async with session.post(url, json=request_body) as resp:
                    body = await resp.text()
                    assert resp.status == 400, f"[{label}] expected HTTP 400, got {resp.status}: {body[:200]}"
                logger.info(f"✅ [{label}] rejected with HTTP 400")

        untouched = await spot_tester.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modifies must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await spot_tester.orders.close_all(fail_if_none=False)
