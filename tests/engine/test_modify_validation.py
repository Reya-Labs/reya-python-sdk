"""
modifyOrder server-side validation tests parametrized over [spot, perp] — live
e2e.

- EMPTY_MODIFY_ERROR: an exact restate (no field changed) is rejected,
- ORDER_NOT_FOUND_ERROR: bogus targets, just-cancelled orders, and fully-filled
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
- INVALID_NONCE_ERROR: a replayed (consumed) nonce,
- the signature envelope is required (missing signature / zero nonce / missing
  signerWallet → HTTP 400),
- cross-account ownership: account B cannot modify account A's resting order
  (authorization error).
- TP/SL trigger orders are not modifiable once the backend accepts native
  trigger creates. Trigger-order create/list/cancel/modify is tracked by
  PRO-475, so current devnet trigger-create rejection is an expected xfail.
  Trigger orders are a perp-only surface, so this case stays perp-pinned.

The modifyOrder validation surface (signature recovery, nonce single-use,
immutable-match, input validation, ownership) is transport- and
market-independent: the ws-exec transport reuses REST's signing path, and the
engine runs the same validation for spot and perp markets. Parametrizing over
[spot, perp] via the root-conftest ``market_config``/``market_type``/``maker``/
``taker`` fixtures pins this validation battery on BOTH markets (matching
test_modify_execution.py / test_modify_priority.py); each market's param
instance is skipped independently if its credentials are absent.
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
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import assert_px_qty, rest_gtc
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.modify, pytest.mark.validation]

PERP_SYMBOL = "ETHRUSDPERP"
BOGUS_ORDER_ID = 999_999_999_999_999_999
PRO_475_URL = "https://linear.app/reya-labs/issue/PRO-475/me-make-tpsl-trigger-orders-listable-cancellable-modifiable"
PRO_475_XFAIL_REASON = (
    "PRO-475: native TP/SL trigger-order storage/list/cancel/modify is not implemented yet; " f"{PRO_475_URL}"
)


_TIF_TO_INT = {
    TimeInForce.GTC: int(TimeInForceInt.GTC),
    TimeInForce.GTT: int(TimeInForceInt.GTT),
    TimeInForce.IOC: int(TimeInForceInt.IOC),
}


def _raw_modify_request(
    tester: ReyaTester,
    market_config: SpotTestConfig | PerpTestConfig,
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
        market_id=market_config.market_id,
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
        symbol=market_config.symbol,
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


@pytest.mark.asyncio
async def test_empty_modify_rejected(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
):
    """An exact restate of the current order state is EMPTY_MODIFY_ERROR."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)

    with pytest.raises(ApiException) as exc_info:
        await maker.client.modify_order(full_state_modify_params(order))
    error_msg = str(exc_info.value)
    assert "EMPTY_MODIFY_ERROR" in error_msg, f"[{market_type}] Expected EMPTY_MODIFY_ERROR, got: {error_msg[:200]}"
    logger.info(f"[{market_type}] ✅ Exact restate rejected with EMPTY_MODIFY_ERROR")

    still_open = await maker.data.open_order(order.order_id)
    assert still_open is not None, "Rejected empty modify must leave the order resting"

    await maker.orders.cancel(order_id=order.order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.check.no_open_orders()


@pytest.mark.asyncio
async def test_order_not_found(market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester):
    """Both a bogus orderId and a just-cancelled order resolve to ORDER_NOT_FOUND_ERROR."""
    bogus_params = ModifyOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=str(market_config.price(0.95)),
        qty=market_config.min_qty,
        post_only=False,
        expires_after=0,
        time_in_force=TimeInForce.GTC,
        order_id=BOGUS_ORDER_ID,
    )
    with pytest.raises(ApiException) as exc_info:
        await maker.client.modify_order(bogus_params)
    error_msg = str(exc_info.value)
    assert (
        "ORDER_NOT_FOUND_ERROR" in error_msg
    ), f"[{market_type}] Expected ORDER_NOT_FOUND_ERROR for bogus id, got: {error_msg[:200]}"
    logger.info(f"[{market_type}] ✅ Bogus orderId rejected with ORDER_NOT_FOUND_ERROR")

    # A real order that was JUST cancelled is equally not-found.
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)
    await maker.orders.cancel(order_id=order.order_id, symbol=market_config.symbol, account_id=maker.account_id)
    await maker.wait.for_order_state(order.order_id, OrderStatus.CANCELLED, timeout=10)

    with pytest.raises(ApiException) as exc_info:
        await maker.client.modify_order(full_state_modify_params(order, limit_px=str(market_config.price(0.96))))
    error_msg = str(exc_info.value)
    assert (
        "ORDER_NOT_FOUND_ERROR" in error_msg
    ), f"[{market_type}] Expected ORDER_NOT_FOUND_ERROR for cancelled order, got: {error_msg[:200]}"
    logger.info(f"[{market_type}] ✅ Just-cancelled order rejected with ORDER_NOT_FOUND_ERROR")

    await maker.check.no_open_orders()


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_modify_after_full_fill_not_found(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_cleanup_guard,  # pylint: disable=unused-argument
):
    """A FULLY-FILLED order is no longer a live resting order — modifying its
    stale snapshot resolves to ORDER_NOT_FOUND_ERROR. The only test in this module
    that produces a fill, so it wires the per-market settlement cleanup (spot
    balance guard / perp baseline restore)."""
    await skip_if_external_config_liquidity(market_config, maker, "A deterministic full fill needs an empty book.")
    queue_px = str(market_config.price(0.99))

    maker_order = await rest_gtc(maker, market_config, price_multiplier=0.99, is_buy=True)

    try:
        ioc = (
            OrderBuilder().symbol(market_config.symbol).sell().price(queue_px).qty(market_config.min_qty).ioc().build()
        )
        taker_order_id = await taker.orders.create_limit(ioc)
        assert taker_order_id is not None
        await maker.wait.for_order_state(maker_order.order_id, OrderStatus.FILLED, timeout=10)
        logger.info(f"[{market_type}] Maker order {maker_order.order_id} fully filled")

        with pytest.raises(ApiException) as exc_info:
            await maker.client.modify_order(
                full_state_modify_params(maker_order, limit_px=str(market_config.price(0.98)))
            )
        error_msg = str(exc_info.value)
        assert (
            "ORDER_NOT_FOUND_ERROR" in error_msg
        ), f"[{market_type}] Expected ORDER_NOT_FOUND_ERROR for filled order, got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ Fully-filled order rejected with ORDER_NOT_FOUND_ERROR")
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_invalid_values_raw(market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester):
    """Raw-request INPUT_VALIDATION_ERROR sweep against ONE resting order:
    zero px, zero qty, neither id, clientOrderId=0. The resting order must come
    through every rejection untouched. (Supplying BOTH orderId and clientOrderId
    is valid under full-restate — orderId targets, clientOrderId is the restated
    immutable — so it is not part of this rejection sweep.)"""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(market_config.price(0.96))
    good_qty = market_config.min_qty

    cases: list[tuple[str, dict[str, Any]]] = [
        ("zero px", {"limit_px": "0", "qty": good_qty, "order_id": order_id}),
        ("zero qty", {"limit_px": good_px, "qty": "0", "order_id": order_id}),
        ("neither id", {"limit_px": good_px, "qty": good_qty}),
        ("clientOrderId=0", {"limit_px": good_px, "qty": good_qty, "client_order_id": 0}),
    ]
    try:
        for label, kwargs in cases:
            request = _raw_modify_request(maker, market_config, **kwargs)
            with pytest.raises(ApiException) as exc_info:
                await maker.client.orders.modify_order(request)
            error_msg = str(exc_info.value)
            assert (
                "INPUT_VALIDATION_ERROR" in error_msg
            ), f"[{market_type}][{label}] expected INPUT_VALIDATION_ERROR, got: {error_msg[:200]}"
            logger.info(f"[{market_type}] ✅ [{label}] rejected with INPUT_VALIDATION_ERROR")

        untouched = await maker.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modifies must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_immutable_mismatch_raw(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
):
    """A restated immutable that doesn't match the resting order is rejected by
    the matching engine's immutable-match (MODIFY_IMMUTABLE_MISMATCH, surfaced
    as INPUT_VALIDATION_ERROR), and the resting order is left untouched. The
    signature is valid over the tampered side, so it is the immutable-match —
    not signature recovery — that rejects (full-restate end-to-end)."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)  # resting BUY
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    try:
        # Restate the side flipped (sell) against a resting buy — every other
        # field is correct and the signature is valid over the flipped side, so
        # only the engine's immutable-match can reject it.
        request = _raw_modify_request(
            maker,
            market_config,
            limit_px=str(market_config.price(0.96)),
            qty=market_config.min_qty,
            order_id=order_id,
            is_buy=False,
        )
        with pytest.raises(ApiException) as exc_info:
            await maker.client.orders.modify_order(request)
        error_msg = str(exc_info.value)
        assert (
            "INPUT_VALIDATION_ERROR" in error_msg
        ), f"[{market_type}] Expected INPUT_VALIDATION_ERROR (immutable mismatch), got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ Restated immutable mismatch (side) rejected with INPUT_VALIDATION_ERROR")

        untouched = await maker.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modify must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_required_fields_and_negative_values_raw(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
):
    """The GTC-applicable modifiable fields are REQUIRED on the wire (no
    omitted-means-inherited shorthand), and negative limitPx/qty are rejected
    — INPUT_VALIDATION_ERROR for every case, resting order untouched.

    Driven as raw JSON POSTs over aiohttp (precedent:
    tests/spot/test_api_validation.py) because the generated pydantic
    `ModifyOrderRequest` cannot express an omitted required field or a
    negative qty. Each payload starts from a fully-signed valid modify (fresh
    nonce per case) and mutates exactly one wire field; the API-layer
    validator rejects before signature recovery, so the signed-vs-wire
    mismatch never surfaces."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(market_config.price(0.96))

    url = f"{maker.client.config.api_url}/modifyOrder"
    omit_cases = ["limitPx", "qty", "postOnly"]
    negative_cases = [("limitPx", f"-{good_px}"), ("qty", f"-{market_config.min_qty}")]

    try:
        async with aiohttp.ClientSession() as session:
            for field_name in omit_cases:
                payload, _nonce = maker.client.build_modify_order_payload(
                    full_state_modify_params(order, limit_px=good_px)
                )
                request_body = _strip_nones(payload)
                del request_body[field_name]
                async with session.post(url, json=request_body) as resp:
                    body = await resp.text()
                    assert (
                        resp.status == 400
                    ), f"[{market_type}][omit {field_name}] expected HTTP 400, got {resp.status}: {body[:200]}"
                    assert (
                        "INPUT_VALIDATION_ERROR" in body
                    ), f"[{market_type}][omit {field_name}] expected INPUT_VALIDATION_ERROR, got: {body[:200]}"
                logger.info(f"[{market_type}] ✅ [omit {field_name}] rejected with INPUT_VALIDATION_ERROR")

            for field_name, bad_value in negative_cases:
                payload, _nonce = maker.client.build_modify_order_payload(
                    full_state_modify_params(order, limit_px=good_px)
                )
                request_body = _strip_nones(payload)
                request_body[field_name] = bad_value
                async with session.post(url, json=request_body) as resp:
                    body = await resp.text()
                    assert (
                        resp.status == 400
                    ), f"[{market_type}][negative {field_name}] expected HTTP 400, got {resp.status}: {body[:200]}"
                    assert (
                        "INPUT_VALIDATION_ERROR" in body
                    ), f"[{market_type}][negative {field_name}] expected INPUT_VALIDATION_ERROR, got: {body[:200]}"
                logger.info(f"[{market_type}] ✅ [negative {field_name}] rejected with INPUT_VALIDATION_ERROR")

        untouched = await maker.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modifies must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_tampered_signature(market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester):
    """Sign one post-modify state, send a different qty on the wire →
    UNAUTHORIZED_SIGNATURE_ERROR, order untouched."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None

    signed_qty = str(Decimal(market_config.min_qty) * 2)
    wire_qty = str(Decimal(market_config.min_qty) * 3)
    payload, _nonce = maker.client.build_modify_order_payload(full_state_modify_params(order, qty=signed_qty))
    payload["qty"] = wire_qty  # signature still covers signed_qty

    try:
        with pytest.raises(ApiException) as exc_info:
            await maker.client.orders.modify_order(ModifyOrderRequest(**payload))
        error_msg = str(exc_info.value)
        assert (
            "UNAUTHORIZED_SIGNATURE_ERROR" in error_msg
        ), f"[{market_type}] Expected UNAUTHORIZED_SIGNATURE_ERROR, got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ Tampered modify rejected with UNAUTHORIZED_SIGNATURE_ERROR")

        untouched = await maker.data.open_order(order.order_id)
        assert untouched is not None
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.perp
@pytest.mark.trigger
@pytest.mark.asyncio
async def test_trigger_order_not_modifiable(perp_maker_tester: ReyaTester, perp_market_config: PerpTestConfig):
    """TP/SL trigger orders cannot be modified. Under full-restate the typed SDK
    restates `orderType=LIMIT`, which mismatches the resting trigger order's
    type, so the matching engine rejects it via the immutable-match
    (`MODIFY_IMMUTABLE_MISMATCH`, surfaced as `INPUT_VALIDATION_ERROR`).

    Spot-pinned to perp by nature: TP/SL trigger orders are a perp-only surface,
    so there is no spot analogue to parametrize. The orderType immutable-match
    itself is market-independent (proven on both markets transitively by the
    full-restate raw-modify cases above)."""
    oracle_price = float(await perp_maker_tester.data.current_price(perp_market_config.symbol))
    far_trigger_px = str(round(oracle_price * 10, 2))

    trigger_params = (
        TriggerOrderBuilder()
        .symbol(perp_market_config.symbol)
        .sell()
        .trigger_price(far_trigger_px)
        .take_profit()
        .build()
    )
    try:
        response = await perp_maker_tester.orders.create_trigger(trigger_params)
    except ApiException as e:
        error_msg = str(e)
        if any(
            marker in error_msg
            for marker in (
                "qty must be greater than 0, got 0",
                "INPUT_VALIDATION_ERROR",
                "INTERNAL_SERVER_ERROR",
            )
        ):
            pytest.xfail(PRO_475_XFAIL_REASON)
        raise
    assert response.order_id is not None
    trigger_order_id = response.order_id

    try:
        modify_params = ModifyOrderParameters(
            symbol=perp_market_config.symbol,
            is_buy=False,
            limit_px=str(round(oracle_price * 9, 2)),
            qty=perp_market_config.min_qty,
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
                order_id=trigger_order_id,
                symbol=perp_market_config.symbol,
                account_id=perp_maker_tester.account_id,
            )
        except ApiException as e:
            logger.warning(f"Trigger order cleanup cancel failed (may already be gone): {e}")


@pytest.mark.parametrize("field_label", ["exchangeId", "timeInForce", "reduceOnly", "clientOrderId"])
@pytest.mark.asyncio
async def test_immutable_mismatch_fields_raw(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, field_label: str
):
    """Each restated immutable field that differs from the resting order is
    rejected by the ME immutable-match (MODIFY_IMMUTABLE_MISMATCH, surfaced as
    INPUT_VALIDATION_ERROR). Driven raw with a VALID signature over the flipped
    value so input/immutable validation — not signature recovery — rejects.
    Extends the isBuy-only `test_immutable_mismatch_raw` across the rest of the
    immutable set (orderType is covered by `test_trigger_order_not_modifiable`)."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)  # resting BUY GTC, cl_ord_id 0
    order_id = int(order.order_id)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(market_config.price(0.96))

    overrides: dict[str, dict[str, Any]] = {
        "exchangeId": {"exchange_id": maker.client.config.dex_id + 1},
        "timeInForce": {"time_in_force": TimeInForce.GTT, "expires_after": int(time.time()) + 3600},
        "reduceOnly": {"reduce_only": True},
        "clientOrderId": {"client_order_id": int(time.time() * 1_000_000)},
    }
    case_overrides = overrides[field_label]

    try:
        request = _raw_modify_request(
            maker, market_config, limit_px=good_px, qty=market_config.min_qty, order_id=order_id, **case_overrides
        )
        with pytest.raises(ApiException) as exc_info:
            await maker.client.orders.modify_order(request)
        error_msg = str(exc_info.value)
        assert (
            "INPUT_VALIDATION_ERROR" in error_msg
        ), f"[{market_type}][{field_label}] expected INPUT_VALIDATION_ERROR (immutable mismatch), got: {error_msg[:200]}"
        logger.info(
            f"[{market_type}] ✅ [{field_label}] restated-immutable mismatch rejected with INPUT_VALIDATION_ERROR"
        )

        untouched = await maker.data.open_order(order.order_id)
        assert untouched is not None, f"[{field_label}] rejected modify must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_modify_unauthorized_cross_account(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
):
    """Account B cannot modify account A's resting order: the ownership check
    (account_id mismatch) rejects it with an authorization error. The
    maker's order is left resting."""
    maker_order = await rest_gtc(maker, market_config, price_multiplier=0.95, is_buy=True)
    original_px, original_qty = maker_order.limit_px, maker_order.qty
    assert original_px is not None and original_qty is not None

    try:
        # taker (account B) restates maker's order, signed with B's key/account.
        with pytest.raises(ApiException) as exc_info:
            await taker.client.modify_order(
                full_state_modify_params(maker_order, limit_px=str(market_config.price(0.96)))
            )
        error_msg = str(exc_info.value)
        assert (
            "Unauthorized: order" in error_msg and "does not belong to account" in error_msg
        ), f"[{market_type}] expected cross-account ownership rejection, got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ Cross-account modify rejected with ownership error")

        untouched = await maker.data.open_order(maker_order.order_id)
        assert untouched is not None, "Maker's order must survive a cross-account modify attempt"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.cancel(
            order_id=maker_order.order_id, symbol=market_config.symbol, account_id=maker.account_id
        )
        await maker.check.no_open_orders()


@pytest.mark.asyncio
async def test_modify_replayed_nonce_rejected(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
):
    """A nonce is single-use: a second modify reusing a consumed nonce is
    rejected with INVALID_NONCE_ERROR. Driven raw so the nonce is pinned across
    both requests; the first modify (a real px change) consumes the nonce."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)
    order_id = int(order.order_id)
    px1 = str(market_config.price(0.96))
    px2 = str(market_config.price(0.97))
    nonce = maker.get_next_nonce()

    try:
        first = _raw_modify_request(
            maker, market_config, limit_px=px1, qty=market_config.min_qty, order_id=order_id, nonce=nonce
        )
        await maker.client.orders.modify_order(first)
        logger.info(f"[{market_type}] ✅ First modify accepted (nonce {nonce} consumed)")

        replay = _raw_modify_request(
            maker, market_config, limit_px=px2, qty=market_config.min_qty, order_id=order_id, nonce=nonce
        )
        with pytest.raises(ApiException) as exc_info:
            await maker.client.orders.modify_order(replay)
        error_msg = str(exc_info.value)
        assert (
            "INVALID_NONCE_ERROR" in error_msg
        ), f"[{market_type}] expected INVALID_NONCE_ERROR on a replayed nonce, got: {error_msg[:200]}"
        logger.info(f"[{market_type}] ✅ Replayed nonce rejected with INVALID_NONCE_ERROR")
    finally:
        await maker.orders.close_all(fail_if_none=False)


@pytest.mark.asyncio
async def test_modify_signature_envelope_raw(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester
):
    """The signature envelope is required + validated: a missing signature, a
    zero nonce, and a missing signerWallet are each rejected (HTTP 400). Driven
    as raw JSON POSTs (the typed model can't express an omitted/empty envelope
    field), mutating exactly one envelope field per case off a fully-valid
    signed modify; the resting order survives every rejection."""
    order = await rest_gtc(maker, market_config, price_multiplier=0.95)
    original_px, original_qty = order.limit_px, order.qty
    assert original_px is not None and original_qty is not None
    good_px = str(market_config.price(0.96))
    url = f"{maker.client.config.api_url}/modifyOrder"

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
                payload, _nonce = maker.client.build_modify_order_payload(
                    full_state_modify_params(order, limit_px=good_px)
                )
                request_body = _strip_nones(payload)
                mutate(request_body)
                async with session.post(url, json=request_body) as resp:
                    body = await resp.text()
                    assert (
                        resp.status == 400
                    ), f"[{market_type}][{label}] expected HTTP 400, got {resp.status}: {body[:200]}"
                logger.info(f"[{market_type}] ✅ [{label}] rejected with HTTP 400")

        untouched = await maker.data.open_order(order.order_id)
        assert untouched is not None, "Rejected modifies must leave the order resting"
        assert_px_qty(untouched, expected_px=original_px, expected_qty=original_qty)
    finally:
        await maker.orders.close_all(fail_if_none=False)
