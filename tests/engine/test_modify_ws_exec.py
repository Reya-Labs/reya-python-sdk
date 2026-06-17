"""
modifyOrder over ws-exec — live e2e.

Same modify semantics as the REST surface, driven through
:class:`ReyaWsExecClient`. Gated on the same env as
tests/ws_exec/test_ws_exec.py: without `REYA_WS_EXEC_URL` + SPOT_*_1
credentials the module collects-and-skips.

Error envelopes surface as :class:`WsExecOperationError` with the structured
`RequestErrorCode` on `.code` — asserted directly. The message is additionally
pinned only where the code alone doesn't discriminate the rule
(INPUT_VALIDATION_ERROR), mirroring the REST suite.
"""

from __future__ import annotations

import asyncio
import os
import time
from decimal import Decimal

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from sdk.open_api.models.order import Order
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient, WsExecOperationError
from tests.helpers.builders.order_builder import full_state_modify_params

load_dotenv()

_REQUIRED_ENV = (
    "REYA_WS_EXEC_URL",
    "SPOT_PRIVATE_KEY_1",
    "SPOT_ACCOUNT_ID_1",
    "SPOT_WALLET_ADDRESS_1",
)
_MISSING_ENV = [_k for _k in _REQUIRED_ENV if not os.environ.get(_k)]

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.modify,
    pytest.mark.skipif(
        bool(_MISSING_ENV),
        reason="ws-exec modify tests need " + ", ".join(_REQUIRED_ENV) + "; missing: " + ", ".join(_MISSING_ENV),
    ),
]

SPOT_SYMBOL = "WETHRUSD"
# Far-out resting prices on an ETH-priced book (same convention as
# tests/ws_exec/test_ws_exec.py): the GTC rests until cancelled.
REST_PX = "1"
MODIFIED_PX = "1.5"
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


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def modify_ws_harness():
    """A started spot REST client + connected ws-exec client + market min qty."""
    config = TradingConfig.from_env_spot(account_number=1)
    rest = ReyaTradingClient(config)
    await rest.start()
    ws: ReyaWsExecClient | None = None
    # Everything after start() runs inside the try so a skip (or a connect
    # failure) never leaks the started REST client's aiohttp session.
    try:
        markets = {m.symbol: m for m in await rest.reference.get_spot_market_definitions()}
        if SPOT_SYMBOL not in markets:
            pytest.skip(f"{SPOT_SYMBOL} not found in /spotMarketDefinitions")
        ws = ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"])
        await ws.connect()
        yield rest, ws, str(markets[SPOT_SYMBOL].min_order_qty)
    finally:
        if ws is not None:
            await ws.close()
        await rest.close()


async def test_ws_exec_modify(modify_ws_harness):  # pylint: disable=redefined-outer-name
    """Happy modify over ws-exec: rest a GTC, modify px+qty in place,
    orderId preserved and openOrders (REST) reflects the new values."""
    rest, ws, min_qty = modify_ws_harness

    create = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_PX,
            qty=min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(rest, order_id)

        new_qty = str(Decimal(min_qty) * 2)
        response = await ws.modify_order(full_state_modify_params(order, limit_px=MODIFIED_PX, qty=new_qty))
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        print(f"  [ws-exec] modify OK orderId={response.order_id} status={response.status}")

        modified = await _wait_for_order_px_qty(rest, order_id, px=MODIFIED_PX, qty=new_qty)
        print(f"  [ws-exec] openOrders reflects px={modified.limit_px} qty={modified.qty}")
    finally:
        await ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)


async def test_ws_exec_modify_not_found_error_envelope(modify_ws_harness):  # pylint: disable=redefined-outer-name
    """Modifying a non-existent order surfaces the per-op error envelope as
    WsExecOperationError with code ORDER_NOT_FOUND."""
    _rest, ws, min_qty = modify_ws_harness

    params = ModifyOrderParameters(
        symbol=SPOT_SYMBOL,
        is_buy=True,
        limit_px=REST_PX,
        qty=min_qty,
        post_only=False,
        expires_after=0,
        time_in_force=TimeInForce.GTC,
        order_id=BOGUS_ORDER_ID,
    )
    with pytest.raises(WsExecOperationError) as exc_info:
        await ws.modify_order(params)
    assert exc_info.value.code == "ORDER_NOT_FOUND", f"Expected ORDER_NOT_FOUND, got {exc_info.value.code}"
    print(f"  [ws-exec] not-found modify rejected OK code={exc_info.value.code}")


async def test_ws_exec_modify_by_client_order_id(modify_ws_harness):  # pylint: disable=redefined-outer-name
    """Request-mapping fidelity: targeting the modify BY clientOrderId puts a
    distinct wire shape through WsModifyOrderRequest — `orderId` is None in the
    payload so `exclude_none` must DROP it from the frame, and the dispatcher
    must resolve the target from `clientOrderId` alone. The response still
    carries the preserved engine orderId, and the REST read-back proves the
    modify reached the real order (the engine-side targeting semantics are
    already proven via REST in test_modify_happy.py::test_modify_by_client_order_id)."""
    rest, ws, min_qty = modify_ws_harness

    client_order_id = int(time.time() * 1_000_000)
    create = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_PX,
            qty=min_qty,
            time_in_force=TimeInForce.GTC,
            client_order_id=client_order_id,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(rest, order_id)

        new_qty = str(Decimal(min_qty) * 2)
        # full_state_modify_params clears order_id when client_order_id is
        # overridden, so the wire payload omits orderId entirely; the resting
        # clientOrderId must also be restated into the signed envelope.
        response = await ws.modify_order(
            full_state_modify_params(
                order,
                client_order_id=client_order_id,
                resting_client_order_id=client_order_id,
                limit_px=MODIFIED_PX,
                qty=new_qty,
            )
        )
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        print(f"  [ws-exec] modify by clientOrderId={client_order_id} OK orderId={response.order_id}")

        modified = await _wait_for_order_px_qty(rest, order_id, px=MODIFIED_PX, qty=new_qty)
        print(f"  [ws-exec] openOrders reflects px={modified.limit_px} qty={modified.qty}")
    finally:
        await ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)


async def test_ws_exec_modify_flags_and_expires_after_envelope(
    modify_ws_harness,
):  # pylint: disable=redefined-outer-name
    """Two transport concerns in one deterministic flow: (1) flag fidelity —
    postOnly False→True→False survives WsModifyOrderRequest's boolean
    serialization, read back via REST openOrders (the WS modify response
    carries no postOnly echo); (2) coupling guard — a non-zero expiresAfter on
    the resting GTC is rejected by the shared client coupling guard in
    build_modify_order_payload (reused by the ws-exec transport) with a
    ValueError BEFORE anything is signed or sent — GTC never expires; only GTT
    carries a lifetime — leaving the order untouched. The off-chain server
    enforces the same rule as defense-in-depth."""
    rest, ws, min_qty = modify_ws_harness

    create = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_PX,
            qty=min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(rest, order_id)

        # postOnly False -> True (a resting order can't cross, so always legal).
        response = await ws.modify_order(full_state_modify_params(order, post_only=True))
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        order = await _wait_for_order_post_only(rest, order_id, expected=True)
        print("  [ws-exec] postOnly False -> True read back")

        # postOnly True -> False.
        response = await ws.modify_order(full_state_modify_params(order, post_only=False))
        assert response.order_id == order_id, f"orderId must be preserved: {response.order_id} != {order_id}"
        order = await _wait_for_order_post_only(rest, order_id, expected=False)
        print("  [ws-exec] postOnly True -> False read back")

        # expiresAfter is TIF-bound: the resting order is GTC, so any non-zero
        # expiresAfter is rejected by the shared client coupling guard in
        # build_modify_order_payload with a ValueError before signing/sending.
        future_expiry = int(time.time()) + 3600
        with pytest.raises(ValueError, match="GTC orders must not expire"):
            await ws.modify_order(full_state_modify_params(order, expires_after=future_expiry))
        print("  [ws-exec] non-zero expiresAfter on GTC rejected client-side before send")

        untouched = await _wait_for_open_order(rest, order_id)
        assert int(untouched.expires_after or 0) == 0, f"expiresAfter must stay 0: {untouched.expires_after}"
        assert not untouched.post_only, f"Rejected modify must not flip postOnly: {untouched.post_only}"
        print("  [ws-exec] order untouched after the rejected expiresAfter modify")
    finally:
        await ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)


async def test_ws_exec_empty_modify_error_envelope(modify_ws_harness):  # pylint: disable=redefined-outer-name
    """Business-rejection envelope breadth: an exact restate (no field
    changed) maps through the ws-exec per-op error envelope as
    WsExecOperationError EMPTY_MODIFY_ERROR — a second, code-specific
    modifyOrder rejection beyond ORDER_NOT_FOUND, deterministic and with no
    counterparty needed. The resting order survives the rejection."""
    rest, ws, min_qty = modify_ws_harness

    create = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_PX,
            qty=min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(rest, order_id)

        with pytest.raises(WsExecOperationError) as exc_info:
            await ws.modify_order(full_state_modify_params(order))
        assert exc_info.value.code == "EMPTY_MODIFY_ERROR", f"Expected EMPTY_MODIFY_ERROR, got {exc_info.value.code}"
        print(f"  [ws-exec] exact restate rejected OK code={exc_info.value.code}")

        still_open = await _wait_for_open_order(rest, order_id)
        assert still_open is not None, "Rejected empty modify must leave the order resting"
    finally:
        await ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)


async def test_ws_exec_immutable_mismatch_envelope(modify_ws_harness):  # pylint: disable=redefined-outer-name
    """Validation-rejection envelope breadth over ws-exec: a restated immutable
    that doesn't match the resting order (here the side is flipped) is rejected
    by the ME immutable-match, surfaced through the per-op error envelope as
    WsExecOperationError INPUT_VALIDATION_ERROR. The signature is valid over the
    flipped side, so it is the immutable-match — not signature recovery — that
    rejects. The resting order survives."""
    rest, ws, min_qty = modify_ws_harness

    create = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL,
            is_buy=True,
            limit_px=REST_PX,
            qty=min_qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert create.order_id is not None
    order_id = create.order_id

    try:
        order = await _wait_for_open_order(rest, order_id)

        # Restate the side flipped (sell) against the resting buy — the SDK signs
        # over the flipped side, so the engine's immutable-match rejects it.
        with pytest.raises(WsExecOperationError) as exc_info:
            await ws.modify_order(full_state_modify_params(order, is_buy=False))
        assert (
            exc_info.value.code == "INPUT_VALIDATION_ERROR"
        ), f"Expected INPUT_VALIDATION_ERROR (immutable mismatch), got {exc_info.value.code}"
        print(f"  [ws-exec] immutable (side) mismatch rejected OK code={exc_info.value.code}")

        still_open = await _wait_for_open_order(rest, order_id)
        assert still_open is not None, "Rejected immutable-mismatch modify must leave the order resting"
    finally:
        await ws.cancel_order(order_id=order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)


async def test_ws_exec_self_match_modify_cancelled(modify_ws_harness):  # pylint: disable=redefined-outer-name
    """Crossing-modify path over ws-exec WITHOUT a fill: rest an ask above and a
    bid below (same account), then modify the bid up to the ask so it crosses
    the account's OWN order → self-match prevention CANCELS the modified bid.
    The ws response carries status CANCELLED (the only modify outcome that
    yields CANCELLED) and no settlement occurs, so the shared account balances
    are untouched. The resting ask survives."""
    rest, ws, min_qty = modify_ws_harness
    ask_px = "2"
    bid_px = "1"

    # Resting ask ABOVE the bid (both far below the ETH-priced book's market, so
    # the bid->ask cross only ever reaches the account's OWN ask, never external).
    ask = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL, is_buy=False, limit_px=ask_px, qty=min_qty, time_in_force=TimeInForce.GTC
        )
    )
    bid = await ws.create_limit_order(
        LimitOrderParameters(
            symbol=SPOT_SYMBOL, is_buy=True, limit_px=bid_px, qty=min_qty, time_in_force=TimeInForce.GTC
        )
    )
    assert ask.order_id is not None and bid.order_id is not None

    try:
        bid_order = await _wait_for_open_order(rest, bid.order_id)
        response = await ws.modify_order(full_state_modify_params(bid_order, limit_px=ask_px))
        assert response.order_id == bid.order_id, f"orderId must be preserved: {response.order_id} != {bid.order_id}"
        assert response.status.value == "CANCELLED", f"self-matching modify must be CANCELLED, got {response.status}"
        print(f"  [ws-exec] self-matching modify cancelled OK status={response.status}")
    finally:
        # The bid is cancelled by SMP; cancel the surviving ask (best-effort).
        try:
            await ws.cancel_order(order_id=ask.order_id, symbol=SPOT_SYMBOL, account_id=rest.config.account_id)
        except WsExecOperationError:
            pass
