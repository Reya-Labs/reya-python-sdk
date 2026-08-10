# pylint: disable=redefined-outer-name
"""Rate-Limit v1 §3 — the edge whitelist gate, and its risk-off carve-out.

The gate covers **create and modify only**. A wallet that is not in
``rl_whitelist`` cannot open or grow exposure — those are rejected 403-class
with ``NOT_WHITELISTED_ERROR``. The gate is owner-keyed and runs before
signature verification, so a correctly-signed order from a non-whitelisted
owner is still rejected.

**Cancel, cancelAll and cancelAllAfter are deliberately NOT gated.** Removing a
wallet from the whitelist is graceful offboarding, not a punishment: it stops
new exposure while leaving resting orders live and cancellable. Gating risk-off
would trap a de-whitelisted owner in its book — unable to add to OR unwind its
position, with its dead-man's switch going stale. Those paths are therefore
asserted NEGATIVELY: whatever the deployment answers, it must not be the gate
verdict.

Reads are unaffected throughout.
"""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import ModifyOrderParameters
from tests.rate_limits.rl_actions import (
    RlMarket,
    assert_not_whitelist_gated,
    resolve_market,
    resting_order,
    wire,
)
from tests.rate_limits.rl_config import (
    HTTP_FORBIDDEN,
    NOT_WHITELISTED_ERROR,
    RateLimitSuiteConfig,
    requires_rate_limits,
)
from tests.rate_limits.rl_errors import RestReject, assert_no_retry_after, capture_rest_reject

logger = logging.getLogger("reya.rate_limits")

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]

#: An order id that cannot exist. On a GATED op the gate rejects before the ME
#: ever looks it up, so the verdict must be the whitelist one and not
#: ORDER_NOT_FOUND. On a NON-gated op the same id is expected to produce an
#: order-not-found-class error — which is a pass, since it proves the request
#: reached the order layer at all.
ABSENT_ORDER_ID = 1

#: cancelAllAfter is probed with the DISARM value: a server-side no-op on an
#: unarmed account, so the probe can never leave an armed countdown behind that
#: would mass-cancel a later test's orders.
COD_DISARM_TIMEOUT_MS = 0


@pytest_asyncio.fixture(loop_scope="session", scope="module")
async def gate_market(rl_non_whitelisted_client: ReyaTradingClient, rl_suite_config: RateLimitSuiteConfig) -> RlMarket:
    """Resolve the market through the NON-whitelisted client.

    Doubles as evidence that reference reads work for a gated wallet.
    """
    return await resolve_market(rl_non_whitelisted_client, rl_suite_config.symbol)


def _assert_gate_reject(reject: RestReject, label: str) -> None:
    assert reject.code == NOT_WHITELISTED_ERROR, f"[{label}] expected {NOT_WHITELISTED_ERROR}; got {reject.describe()}"
    assert (
        reject.status == HTTP_FORBIDDEN
    ), f"[{label}] expected HTTP {HTTP_FORBIDDEN} (403-class); got {reject.describe()}"
    assert_no_retry_after(reject, label)


# ---- Gated: the exposure-increasing ops ------------------------------------


async def test_create_order_is_gated(rl_non_whitelisted_client: ReyaTradingClient, gate_market: RlMarket) -> None:
    """createOrder from a non-whitelisted owner → 403 NOT_WHITELISTED_ERROR."""
    reject = await capture_rest_reject(
        rl_non_whitelisted_client.create_limit_order(resting_order(gate_market)),
        "non-whitelisted create",
    )
    logger.info("whitelist gate (create): %s", reject.describe())
    _assert_gate_reject(reject, "non-whitelisted create")


async def test_modify_order_is_gated(rl_non_whitelisted_client: ReyaTradingClient, gate_market: RlMarket) -> None:
    """modifyOrder is gated too — a modify can grow exposure (qty-up / reprice).

    Targeted at an order id that cannot exist: the gate runs before the order
    lookup, so the verdict must be the whitelist one, not ORDER_NOT_FOUND.
    """
    reject = await capture_rest_reject(
        rl_non_whitelisted_client.modify_order(
            ModifyOrderParameters(
                symbol=gate_market.symbol,
                is_buy=True,
                limit_px=wire(gate_market.resting_buy_price),
                qty=wire(gate_market.min_qty),
                post_only=False,
                expires_after=None,
                time_in_force=TimeInForce.GTC,
                order_id=ABSENT_ORDER_ID,
            )
        ),
        "non-whitelisted modify",
    )
    logger.info("whitelist gate (modify): %s", reject.describe())
    _assert_gate_reject(reject, "non-whitelisted modify")


async def test_create_for_an_unresolvable_account_is_gated(
    rl_unknown_owner_client: ReyaTradingClient, gate_market: RlMarket
) -> None:
    """An ``accountId`` that resolves to NO owner → 403 NOT_WHITELISTED_ERROR.

    The gate is owner-keyed, so "no owner" must fail closed onto the same
    verdict as "owner not in the whitelist" — the one row of the §3 verb table
    a client can reach without fault injection. Failing open here would let an
    unknown id skip the gate entirely.
    """
    reject = await capture_rest_reject(
        rl_unknown_owner_client.create_limit_order(resting_order(gate_market)),
        "unknown-owner create",
    )
    logger.info("whitelist gate (unknown owner): %s", reject.describe())
    _assert_gate_reject(reject, "unknown-owner create")


# ---- Not gated: risk-off always flows --------------------------------------


async def test_cancel_order_is_not_gated(rl_non_whitelisted_client: ReyaTradingClient, gate_market: RlMarket) -> None:
    """cancelOrder is NOT whitelist-gated — risk-off always flows.

    The probe targets an order id that cannot exist, so the expected outcome on
    a correct deployment is an order-not-found-class rejection (or a success on
    a tolerant edge). Either is a pass; only the gate verdict fails.
    """
    reject = await assert_not_whitelist_gated(
        rl_non_whitelisted_client.cancel_order(
            order_id=str(ABSENT_ORDER_ID),
            symbol=gate_market.symbol,
            account_id=rl_non_whitelisted_client.config.account_id,
        ),
        "non-whitelisted cancel",
    )
    logger.info("cancel not gated (outcome: %s)", reject.describe() if reject else "accepted")


async def test_cancel_all_is_not_gated(rl_non_whitelisted_client: ReyaTradingClient, gate_market: RlMarket) -> None:
    """cancelAll is NOT whitelist-gated.

    A de-whitelisted owner must be able to flatten its whole book in one call.
    With no resting orders the expected outcome is an empty-but-successful
    cancelAll; a rejection passes too, as long as it is not the gate verdict.
    """
    reject = await assert_not_whitelist_gated(
        rl_non_whitelisted_client.mass_cancel(
            symbol=gate_market.symbol,
            account_id=rl_non_whitelisted_client.config.account_id,
        ),
        "non-whitelisted cancelAll",
    )
    logger.info("cancelAll not gated (outcome: %s)", reject.describe() if reject else "accepted")


async def test_cancel_all_after_is_not_gated(rl_non_whitelisted_client: ReyaTradingClient) -> None:
    """cancelAllAfter is NOT whitelist-gated — the dead-man's switch stays alive.

    Probed with ``timeoutMs=0`` (disarm), a server-side no-op on an unarmed
    account, so this can never leave an armed countdown behind.
    """
    reject = await assert_not_whitelist_gated(
        rl_non_whitelisted_client.cancel_all_after(
            timeout_ms=COD_DISARM_TIMEOUT_MS,
            account_id=rl_non_whitelisted_client.config.account_id,
        ),
        "non-whitelisted cancelAllAfter (disarm)",
    )
    logger.info("cancelAllAfter not gated (outcome: %s)", reject.describe() if reject else "accepted")


# ---- Reads -----------------------------------------------------------------


async def test_reads_are_not_gated(rl_non_whitelisted_client: ReyaTradingClient, gate_market: RlMarket) -> None:
    """Market data and the wallet's own open orders work for a gated wallet."""
    definitions = await rl_non_whitelisted_client.reference.get_spot_market_definitions()
    assert any(item.symbol == gate_market.symbol for item in definitions)

    oracle_prices = await rl_non_whitelisted_client.markets.get_asset_oracle_prices()
    assert oracle_prices, "market-data reads must work for a non-whitelisted wallet"

    open_orders = await rl_non_whitelisted_client.get_open_orders()
    assert isinstance(open_orders, list)
    logger.info("non-whitelisted wallet reads OK: %d open orders visible", len(open_orders))
