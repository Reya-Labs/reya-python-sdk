"""modifyOrder over ws-exec — live e2e, parametrized [spot, perp].

Engine-side modify semantics (px/qty/flags read-back, immutable-match,
empty-modify, qty-below-filled, crossing/self-match) are proven
transport-independently via REST in tests/engine/test_modify_*.py (also
[spot, perp]). This module pins the ws-exec TRANSPORT's own modify surface over
BOTH markets via the shared ``ws_exec_market`` fixture: each happy-path,
immutable-mismatch, and error-envelope test modifies a resting order at a
far-from-touch price (``m.rest_buy_px``), which rests without crossing on either
market, so the transport is exercised identically on spot and perp.

Error envelopes surface as :class:`WsExecOperationError` with the structured
`RequestErrorCode` on `.code` — asserted directly. The message is additionally
pinned only where the code alone doesn't discriminate the rule
(INPUT_VALIDATION_ERROR), mirroring the REST suite.

Each market is skipped independently if its credentials (or REYA_WS_EXEC_URL)
are absent (see ``ws_exec_market``).
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal

import pytest

from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters
from sdk.reya_ws_exec import WsExecOperationError
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.ws_exec_market import WsExecMarket

pytestmark = [pytest.mark.e2e, pytest.mark.modify]

BOGUS_ORDER_ID = 999_999_999_999_999_999


async def _wait_for_open_order(rest: ReyaTradingClient, order_id: str, timeout_s: float = 10.0) -> Order:
    """Poll openOrders until `order_id` appears — the OrdersProvider cache
    consumes the ME's Redis stream asynchronously w.r.t. the ws-exec ack."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        open_orders = await rest.get_open_orders()
        order = next((o for o in open_orders if o.order_id == order_id), None)
        if order is not None:
            return order
        await asyncio.sleep(0.2)
    raise AssertionError(f"Order {order_id} not visible via REST within {timeout_s}s")


async def _wait_for_order_px_qty(
    rest: ReyaTradingClient, order_id: str, px: str, qty: str, timeout_s: float = 10.0
) -> Order:
    """Poll openOrders until `order_id` reflects the post-modify px/qty."""
    deadline = time.time() + timeout_s
    last: Order | None = None
    while time.time() < deadline:
        open_orders = await rest.get_open_orders()
        last = next((o for o in open_orders if o.order_id == order_id), None)
        if (
            last is not None
            and last.qty is not None
            and Decimal(last.limit_px) == Decimal(px)
            and Decimal(last.qty) == Decimal(qty)
        ):
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Order {order_id} did not reflect px={px} qty={qty} within {timeout_s}s; "
        f"last seen: px={last.limit_px if last else None} qty={last.qty if last else None}"
    )


async def _wait_for_order_post_only(
    rest: ReyaTradingClient, order_id: str, expected: bool, timeout_s: float = 10.0
) -> Order:
    """Poll openOrders until `order_id` reflects the post-modify postOnly flag."""
    deadline = time.time() + timeout_s
    last: Order | None = None
    while time.time() < deadline:
        open_orders = await rest.get_open_orders()
        last = next((o for o in open_orders if o.order_id == order_id), None)
        if last is not None and bool(last.post_only) == expected:
            return last
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Order {order_id} did not reflect postOnly={expected} within {timeout_s}s; "
        f"last seen: postOnly={last.post_only if last else None}"
    )


async def test_ws_exec_modify(ws_exec_market: WsExecMarket):
    """Happy modify over ws-exec: rest a GTC, modify px+qty in place,
    orderId preserved and openOrders (REST) reflects the new values."""
    m = ws_exec_market
    modified_px = str(Decimal(m.rest_buy_px) * Decimal("1.5"))

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(m.rest, order_id)

        new_qty = str(Decimal(m.min_qty) * 2)
        response = await m.ws.modify_order(full_state_modify_params(order, limit_px=modified_px, qty=new_qty))
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        print(f"  [ws-exec {m.market_type}] modify OK orderId={response.order_id} status={response.status}")

        modified = await _wait_for_order_px_qty(m.rest, order_id, px=modified_px, qty=new_qty)
        print(f"  [ws-exec {m.market_type}] openOrders reflects px={modified.limit_px} qty={modified.qty}")
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_modify_not_found_error_envelope(ws_exec_market: WsExecMarket):
    """Modifying a non-existent order surfaces the per-op error envelope as
    WsExecOperationError with code ORDER_NOT_FOUND_ERROR."""
    m = ws_exec_market

    params = ModifyOrderParameters(
        symbol=m.symbol,
        is_buy=True,
        limit_px=m.rest_buy_px,
        qty=m.min_qty,
        post_only=False,
        expires_after=0,
        time_in_force=TimeInForce.GTC,
        order_id=BOGUS_ORDER_ID,
    )
    with pytest.raises(WsExecOperationError) as exc_info:
        await m.ws.modify_order(params)
    assert exc_info.value.code == "ORDER_NOT_FOUND_ERROR", f"Expected ORDER_NOT_FOUND_ERROR, got {exc_info.value.code}"
    print(f"  [ws-exec {m.market_type}] not-found modify rejected OK code={exc_info.value.code}")


async def test_ws_exec_modify_by_client_order_id(ws_exec_market: WsExecMarket):
    """Request-mapping fidelity: targeting the modify BY clientOrderId puts a
    distinct wire shape through WsModifyOrderRequest — `orderId` is None in the
    payload so `exclude_none` must DROP it from the frame, and the dispatcher
    must resolve the target from `clientOrderId` alone. The response still
    carries the preserved engine orderId, and the REST read-back proves the
    modify reached the real order (the engine-side targeting semantics are
    already proven via REST in test_modify_happy.py::test_modify_by_client_order_id)."""
    m = ws_exec_market
    modified_px = str(Decimal(m.rest_buy_px) * Decimal("1.5"))

    client_order_id = int(time.time() * 1_000_000)
    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
            client_order_id=client_order_id,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(m.rest, order_id)

        new_qty = str(Decimal(m.min_qty) * 2)
        # full_state_modify_params clears order_id when client_order_id is
        # overridden, so the wire payload omits orderId entirely; the resting
        # clientOrderId must also be restated into the signed envelope.
        response = await m.ws.modify_order(
            full_state_modify_params(
                order,
                client_order_id=client_order_id,
                resting_client_order_id=client_order_id,
                limit_px=modified_px,
                qty=new_qty,
            )
        )
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        print(f"  [ws-exec {m.market_type}] modify by clientOrderId={client_order_id} OK orderId={response.order_id}")

        modified = await _wait_for_order_px_qty(m.rest, order_id, px=modified_px, qty=new_qty)
        print(f"  [ws-exec {m.market_type}] openOrders reflects px={modified.limit_px} qty={modified.qty}")
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_modify_flags_and_expires_after_envelope(ws_exec_market: WsExecMarket):
    """Two transport concerns in one deterministic flow: (1) flag fidelity —
    postOnly False→True→False survives WsModifyOrderRequest's boolean
    serialization, read back via REST openOrders (the WS modify response
    carries no postOnly echo); (2) coupling guard — a non-zero expiresAfter on
    the resting GTC is rejected by the shared client coupling guard in
    build_modify_order_payload (reused by the ws-exec transport) with a
    ValueError BEFORE anything is signed or sent — GTC never expires; only GTT
    carries a lifetime — leaving the order untouched. The off-chain server
    enforces the same rule as defense-in-depth."""
    m = ws_exec_market

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(m.rest, order_id)

        # postOnly False -> True (a resting order can't cross, so always legal).
        response = await m.ws.modify_order(full_state_modify_params(order, post_only=True))
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        order = await _wait_for_order_post_only(m.rest, order_id, expected=True)
        print(f"  [ws-exec {m.market_type}] postOnly False -> True read back")

        # postOnly True -> False.
        response = await m.ws.modify_order(full_state_modify_params(order, post_only=False))
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        order = await _wait_for_order_post_only(m.rest, order_id, expected=False)
        print(f"  [ws-exec {m.market_type}] postOnly True -> False read back")

        # expiresAfter is TIF-bound: the resting order is GTC, so any non-zero
        # expiresAfter is rejected by the shared client coupling guard in
        # build_modify_order_payload with a ValueError before signing/sending.
        future_expiry = int(time.time()) + 3600
        with pytest.raises(ValueError, match="GTC orders must not expire"):
            await m.ws.modify_order(full_state_modify_params(order, expires_after=future_expiry))
        print(f"  [ws-exec {m.market_type}] non-zero expiresAfter on GTC rejected client-side before send")

        untouched = await _wait_for_open_order(m.rest, order_id)
        assert int(untouched.expires_after or 0) == 0, f"expiresAfter must stay 0: {untouched.expires_after}"
        assert not untouched.post_only, f"Rejected modify must not flip postOnly: {untouched.post_only}"
        print(f"  [ws-exec {m.market_type}] order untouched after the rejected expiresAfter modify")
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_empty_modify_error_envelope(ws_exec_market: WsExecMarket):
    """Business-rejection envelope breadth: an exact restate (no field
    changed) maps through the ws-exec per-op error envelope as
    WsExecOperationError EMPTY_MODIFY_ERROR — a second, code-specific
    modifyOrder rejection beyond ORDER_NOT_FOUND_ERROR, deterministic and with no
    counterparty needed. The resting order survives the rejection."""
    m = ws_exec_market

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(m.rest, order_id)

        with pytest.raises(WsExecOperationError) as exc_info:
            await m.ws.modify_order(full_state_modify_params(order))
        assert exc_info.value.code == "EMPTY_MODIFY_ERROR", f"Expected EMPTY_MODIFY_ERROR, got {exc_info.value.code}"
        print(f"  [ws-exec {m.market_type}] exact restate rejected OK code={exc_info.value.code}")

        still_open = await _wait_for_open_order(m.rest, order_id)
        assert still_open is not None, "Rejected empty modify must leave the order resting"
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_immutable_mismatch_envelope(ws_exec_market: WsExecMarket):
    """Validation-rejection envelope breadth over ws-exec: a restated immutable
    that doesn't match the resting order (here the side is flipped) is rejected
    by the ME immutable-match, surfaced through the per-op error envelope as
    WsExecOperationError INPUT_VALIDATION_ERROR. The signature is valid over the
    flipped side, so it is the immutable-match — not signature recovery — that
    rejects. The resting order survives."""
    m = ws_exec_market

    create = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol,
            is_buy=True,
            limit_px=m.rest_buy_px,
            qty=m.min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(m.rest, order_id)

        # Restate the side flipped (sell) against the resting buy — the SDK signs
        # over the flipped side, so the engine's immutable-match rejects it.
        with pytest.raises(WsExecOperationError) as exc_info:
            await m.ws.modify_order(full_state_modify_params(order, is_buy=False))
        assert (
            exc_info.value.code == "INPUT_VALIDATION_ERROR"
        ), f"Expected INPUT_VALIDATION_ERROR (immutable mismatch), got {exc_info.value.code}"
        print(f"  [ws-exec {m.market_type}] immutable (side) mismatch rejected OK code={exc_info.value.code}")

        still_open = await _wait_for_open_order(m.rest, order_id)
        assert still_open is not None, "Rejected immutable-mismatch modify must leave the order resting"
    finally:
        await m.ws.cancel_order(order_id=order_id, symbol=m.symbol, account_id=m.rest.config.account_id)


async def test_ws_exec_self_match_modify_cancelled(ws_exec_market: WsExecMarket):
    """Crossing-modify path over ws-exec WITHOUT a fill: rest an ask above and a
    bid below (same account), then modify the bid up to the ask so it crosses
    the account's OWN order → self-match prevention CANCELS the modified bid.
    The ws response carries status CANCELLED (the only modify outcome that
    yields CANCELLED) and no settlement occurs, so the shared account balances
    are untouched. The resting ask survives.

    Spot-pinned: the cross is engineered with both legs far BELOW an ETH-priced
    spot book (ask "2" above bid "1") so the bid->ask cross only ever reaches the
    account's OWN ask; on perp that same construction (a sell far below mark)
    would execute against the real book instead of resting. The engine self-match
    semantics are proven [spot, perp] via REST in
    test_modify_execution.py::test_crossing_modify_self_match_cancelled."""
    m = ws_exec_market
    if m.market_type != "spot":
        pytest.skip(
            "self-match crossing modify needs near-mark resting prices on perp (the shared harness exposes only a "
            "single far-from-touch rest price); engine SMP proven [spot, perp] via REST "
            "(test_modify_execution.py::test_crossing_modify_self_match_cancelled)"
        )
    ask_px = "2"
    bid_px = "1"

    # Resting ask ABOVE the bid (both far below the ETH-priced book's market, so
    # the bid->ask cross only ever reaches the account's OWN ask, never external).
    ask = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol, is_buy=False, limit_px=ask_px, qty=m.min_qty, time_in_force=TimeInForce.GTC
        )
    )
    bid = await m.ws.create_limit_order(
        LimitOrderParameters(
            symbol=m.symbol, is_buy=True, limit_px=bid_px, qty=m.min_qty, time_in_force=TimeInForce.GTC
        )
    )
    assert ask.order_id is not None and bid.order_id is not None

    try:
        bid_order = await _wait_for_open_order(m.rest, bid.order_id)
        response = await m.ws.modify_order(full_state_modify_params(bid_order, limit_px=ask_px))
        assert response.order_id == bid.order_id, f"orderId must be preserved: {response.order_id} != {bid.order_id}"
        assert response.status.value == "CANCELLED", f"self-matching modify must be CANCELLED, got {response.status}"
        print(f"  [ws-exec {m.market_type}] self-matching modify cancelled OK status={response.status}")
    finally:
        # The bid is cancelled by SMP; cancel the surviving ask (best-effort).
        try:
            await m.ws.cancel_order(order_id=ask.order_id, symbol=m.symbol, account_id=m.rest.config.account_id)
        except WsExecOperationError:
            pass
