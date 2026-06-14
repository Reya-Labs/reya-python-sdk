"""Good-Till-Time (GTT) lifecycle tests parametrized over [spot, perp] — live e2e.

GTT rests like GTC but the matching engine auto-reaps it at ``expiresAfter``
(GTC rests until cancelled; IOC never rests). These tests prove the full GTT
path on devnet:
- a GTT rests (OPEN), is reachable via REST carrying its ``expiresAfter``, and
  is cancellable,
- a GTT with a near-future expiry is AUTO-CANCELLED by the reaper at
  ``expiresAfter`` with no explicit cancel,
- a resting GTT can be modified to refresh its expiry (stays resting); a modify
  dropping the expiry to 0 is rejected client-side (the GTC/GTT coupling),
- a resting GTT that gets filled SETTLES on-chain — the fill calldata
  reproduces the signed ``timeInForce=GTT``, so settlement must NOT bust.

``maker`` is the aggressor (the account whose order crosses); ``taker`` is the
resting counterparty — matching the ``settlement_probe`` convention.
"""

from __future__ import annotations

import time

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import full_state_modify_params
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import rest_gtt, wait_for_order_fields
from tests.helpers.reya_tester import logger
from tests.helpers.settlement import SettlementProbe

pytestmark = [pytest.mark.e2e, pytest.mark.gtt]

# Comfortable lifetime for tests that must NOT expire mid-run (rest / modify /
# fill all complete in well under a second).
GTT_LIFETIME_S = 300


@pytest.mark.asyncio
async def test_gtt_rests_open_and_cancellable(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A GTT placed away from the touch rests (OPEN), is reachable via REST
    carrying its ``expiresAfter``, and is cancellable."""
    expires_after = int(time.time()) + GTT_LIFETIME_S
    order = await rest_gtt(maker, market_config, price_multiplier=0.5, expires_after=expires_after)

    assert order.status == OrderStatus.OPEN, f"[{market_type}] a GTT must rest OPEN, got {order.status}"
    assert (
        int(order.expires_after or 0) == expires_after
    ), f"[{market_type}] GTT must carry its expiresAfter: {order.expires_after} != {expires_after}"

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=order.order_id)
    await maker.wait.for_order_state(order.order_id, OrderStatus.CANCELLED)
    logger.info(f"[{market_type}] ✅ GTT rested OPEN with expiresAfter and was cancelled")


@pytest.mark.asyncio
async def test_gtt_reaped_at_expiry(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A resting GTT is AUTO-CANCELLED by the matching engine's reaper at
    ``expiresAfter`` — no explicit cancel. A short deadline + expiry (the
    coupling requires ``expiresAfter`` strictly after the deadline) keeps the
    reap inside the poll window."""
    now = int(time.time())
    deadline = now + 25
    expires_after = now + 35  # strictly after the deadline; reaped shortly after
    params = LimitOrderParameters(
        symbol=market_config.symbol,
        is_buy=True,
        limit_px=str(market_config.price(0.5)),
        qty=market_config.min_qty,
        time_in_force=TimeInForce.GTT,
        expires_after=expires_after,
        deadline=deadline,
    )
    order_id = await maker.orders.create_limit(params)
    assert order_id is not None, f"[{market_type}] GTT creation must return an order_id"
    await maker.wait.for_order_creation(order_id)

    resting = await wait_for_order_fields(maker, order_id)
    assert resting.status == OrderStatus.OPEN, f"[{market_type}] GTT must rest before its expiry"

    # Do NOT cancel — the reaper must auto-cancel it at expiresAfter.
    await maker.wait.for_order_state(order_id, OrderStatus.CANCELLED, timeout=90)
    logger.info(f"[{market_type}] ✅ GTT auto-reaped at expiresAfter (no explicit cancel)")


@pytest.mark.asyncio
async def test_gtt_modify_refresh_expiry(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
) -> None:
    """A resting GTT can be modified to a NEW future ``expiresAfter`` (stays
    resting with the refreshed lifetime); a modify dropping the expiry to 0 is
    rejected client-side (a GTT must carry a lifetime — 0 would be GTC)."""
    expires_after = int(time.time()) + GTT_LIFETIME_S
    order = await rest_gtt(maker, market_config, price_multiplier=0.5, expires_after=expires_after)

    new_expires_after = int(time.time()) + GTT_LIFETIME_S + 120
    response = await maker.client.modify_order(full_state_modify_params(order, expires_after=new_expires_after))
    assert response.order_id == order.order_id, f"[{market_type}] orderId must be preserved through modify"

    refreshed = await wait_for_order_fields(maker, order.order_id, expires_after=new_expires_after)
    assert refreshed.status == OrderStatus.OPEN, f"[{market_type}] GTT must stay resting after an expiry refresh"

    # Dropping the expiry to 0 on a resting GTT is rejected client-side before
    # signing — that would turn it into a never-expiring (GTC) order.
    with pytest.raises(ValueError, match="GTT orders require a non-zero expires_after"):
        await maker.client.modify_order(full_state_modify_params(refreshed, expires_after=0))

    await maker.client.cancel_order(symbol=market_config.symbol, account_id=maker.account_id, order_id=order.order_id)
    await maker.wait.for_order_state(order.order_id, OrderStatus.CANCELLED)
    logger.info(f"[{market_type}] ✅ GTT expiry refreshed via modify; drop-to-0 rejected client-side")


@pytest.mark.asyncio
@pytest.mark.maker_taker
@pytest.mark.usefixtures("settlement_cleanup_guard")
async def test_gtt_resting_fill_settles_on_chain(
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    maker: ReyaTester,
    taker: ReyaTester,
    settlement_probe: SettlementProbe,
) -> None:
    """A resting GTT that gets filled settles on-chain: the fill calldata
    reproduces the signed ``timeInForce=GTT``, so settlement must NOT bust
    InvalidSignature. The resting counterparty (``taker``) rests a GTT SELL;
    the aggressor (``maker``) crosses it with an IOC BUY; settlement lands
    (spot balance deltas / perp signed-position deltas) with no execution
    busts (asserted session-wide by ``execution_busts_guard``)."""
    await skip_if_external_config_liquidity(market_config, maker, "Engineered cross needs an empty book.")
    qty = market_config.min_qty
    cross_px = str(market_config.price(0.99))
    expires_after = int(time.time()) + GTT_LIFETIME_S

    await settlement_probe.capture_baseline()

    seller_order = await rest_gtt(
        taker, market_config, price_multiplier=0.99, expires_after=expires_after, is_buy=False
    )
    buyer_order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=market_config.symbol,
            is_buy=True,
            limit_px=cross_px,
            qty=qty,
            time_in_force=TimeInForce.IOC,
        )
    )
    assert buyer_order_id is not None, f"[{market_type}] aggressor IOC must return an order_id"

    await taker.wait.for_order_state(seller_order.order_id, OrderStatus.FILLED, timeout=10)
    await settlement_probe.assert_settled(qty=qty, price=cross_px)
    logger.info(f"[{market_type}] ✅ a resting GTT filled and settled on-chain (no bust)")
