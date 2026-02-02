"""
Spot Balance Verification Tests

Tests for verifying balance changes after spot trades:
- Balance update after buy
- Balance update after sell
- WebSocket balance matches REST
- Maker/taker balance consistency

Note: Spot trading has ZERO fees, so we can verify exact balance changes:
- Base asset change = trade quantity
- RUSD change = trade quantity * execution price
"""

import asyncio
import logging
from decimal import Decimal

import pytest

from sdk.open_api.models import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


async def get_account_balances(tester: ReyaTester) -> dict:
    """Get current balances for a specific account."""
    balances = await tester.client.get_account_balances()
    # Filter by the tester's account_id
    account_balances = [b for b in balances if b.account_id == tester.account_id]
    return {b.asset: Decimal(b.real_balance) for b in account_balances}


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_update_after_buy(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test balance changes correctly after spot buy.

    This test requires a controlled environment to verify exact balance changes
    between our maker and taker accounts. When external liquidity exists,
    we skip to avoid unpredictable balance changes.

    When taker buys base asset:
    - Taker: Base asset balance increases, RUSD balance decreases
    - Maker: Base asset balance decreases, RUSD balance increases

    Flow:
    1. Check for external liquidity - skip if present
    2. Record initial balances
    3. Maker places sell order
    4. Taker buys with IOC
    5. Verify balance changes
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE UPDATE AFTER BUY TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists - this test needs controlled environment for balance verification
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping balance verification test: external liquidity exists. "
            "This test requires a controlled environment to verify exact balance changes."
        )

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)

    base_asset = spot_config.base_asset
    logger.info(
        f"Maker initial: {base_asset}={maker_balances_before.get(base_asset, 0)}, RUSD={maker_balances_before.get('RUSD', 0)}"
    )
    logger.info(
        f"Taker initial: {base_asset}={taker_balances_before.get(base_asset, 0)}, RUSD={taker_balances_before.get('RUSD', 0)}"
    )

    # Maker places GTC sell order
    maker_price = spot_config.price(1.04)  # High price to avoid other matches

    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()

    logger.info(f"Maker placing GTC sell: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker buys with IOC
    taker_price = maker_price

    taker_params = OrderBuilder.from_config(spot_config).buy().at_price(1.04).ioc().build()

    logger.info(f"Taker placing IOC buy: {spot_config.min_qty} @ ${taker_price:.2f}")
    await taker_tester.orders.create_limit(taker_params)

    # Wait for trade to settle
    await asyncio.sleep(0.05)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Wait for indexer to update balances (REST API may lag behind WebSocket)
    await asyncio.sleep(1.0)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)

    logger.info(
        f"Maker after: {base_asset}={maker_balances_after.get(base_asset, 0)}, RUSD={maker_balances_after.get('RUSD', 0)}"
    )
    logger.info(
        f"Taker after: {base_asset}={taker_balances_after.get(base_asset, 0)}, RUSD={taker_balances_after.get('RUSD', 0)}"
    )

    # Calculate changes
    qty = Decimal(spot_config.min_qty)
    execution_price = Decimal(str(maker_price))  # Trade executes at maker's price
    expected_rusd_change = qty * execution_price

    # Maker sold base asset, received RUSD
    maker_base_change = maker_balances_after.get(base_asset, Decimal(0)) - maker_balances_before.get(
        base_asset, Decimal(0)
    )
    maker_rusd_change = maker_balances_after.get("RUSD", Decimal(0)) - maker_balances_before.get("RUSD", Decimal(0))

    # Taker bought base asset, paid RUSD
    taker_base_change = taker_balances_after.get(base_asset, Decimal(0)) - taker_balances_before.get(
        base_asset, Decimal(0)
    )
    taker_rusd_change = taker_balances_after.get("RUSD", Decimal(0)) - taker_balances_before.get("RUSD", Decimal(0))

    logger.info(f"Maker changes: {base_asset}={maker_base_change}, RUSD={maker_rusd_change}")
    logger.info(f"Taker changes: {base_asset}={taker_base_change}, RUSD={taker_rusd_change}")
    logger.info(f"Expected: qty={qty}, price={execution_price}, rusd_change={expected_rusd_change}")

    # Verify EXACT balance changes (spot has zero fees)
    # Maker sold base asset: base asset decreases by qty, RUSD increases by qty * price
    assert maker_base_change == -qty, f"Maker {base_asset} change should be exactly -{qty}, got: {maker_base_change}"
    assert (
        maker_rusd_change == expected_rusd_change
    ), f"Maker RUSD change should be exactly +{expected_rusd_change}, got: {maker_rusd_change}"

    # Taker bought base asset: base asset increases by qty, RUSD decreases by qty * price
    assert taker_base_change == qty, f"Taker {base_asset} change should be exactly +{qty}, got: {taker_base_change}"
    assert (
        taker_rusd_change == -expected_rusd_change
    ), f"Taker RUSD change should be exactly -{expected_rusd_change}, got: {taker_rusd_change}"

    logger.info("✅ EXACT balance changes verified (zero fees confirmed)")
    logger.info("✅ SPOT BALANCE UPDATE AFTER BUY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_update_after_sell(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test balance changes correctly after spot sell.

    This test requires a controlled environment to verify exact balance changes
    between our maker and taker accounts. When external liquidity exists,
    we skip to avoid unpredictable balance changes.

    When maker sells base asset:
    - Maker: Base asset balance decreases, RUSD balance increases
    - Taker: Base asset balance increases, RUSD balance decreases

    Flow:
    1. Check for external liquidity - skip if present
    2. Record initial balances
    3. Maker places sell order
    4. Taker buys with IOC
    5. Verify balance changes
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE UPDATE AFTER SELL TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists - this test needs controlled environment for balance verification
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping balance verification test: external liquidity exists. "
            "This test requires a controlled environment to verify exact balance changes."
        )

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)

    base_asset = spot_config.base_asset
    logger.info(
        f"Maker initial: {base_asset}={maker_balances_before.get(base_asset, 0)}, RUSD={maker_balances_before.get('RUSD', 0)}"
    )
    logger.info(
        f"Taker initial: {base_asset}={taker_balances_before.get(base_asset, 0)}, RUSD={taker_balances_before.get('RUSD', 0)}"
    )

    # Maker places GTC sell order
    maker_price = spot_config.price(1.04)  # High price to avoid other matches

    maker_params = OrderBuilder.from_config(spot_config).sell().at_price(1.04).gtc().build()

    logger.info(f"Maker placing GTC sell: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker buys with IOC (taker has more RUSD)
    taker_price = maker_price

    taker_params = OrderBuilder.from_config(spot_config).buy().at_price(1.04).ioc().build()

    logger.info(f"Taker placing IOC buy: {spot_config.min_qty} @ ${taker_price:.2f}")
    await taker_tester.orders.create_limit(taker_params)

    # Wait for trade to settle
    await asyncio.sleep(0.05)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Wait for indexer to update balances (REST API may lag behind WebSocket)
    await asyncio.sleep(1.0)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)

    logger.info(
        f"Maker after: {base_asset}={maker_balances_after.get(base_asset, 0)}, RUSD={maker_balances_after.get('RUSD', 0)}"
    )
    logger.info(
        f"Taker after: {base_asset}={taker_balances_after.get(base_asset, 0)}, RUSD={taker_balances_after.get('RUSD', 0)}"
    )

    # Calculate changes
    qty = Decimal(spot_config.min_qty)
    execution_price = Decimal(str(maker_price))  # Trade executes at maker's price
    expected_rusd_change = qty * execution_price

    # Maker sold base asset, received RUSD
    maker_base_change = maker_balances_after.get(base_asset, Decimal(0)) - maker_balances_before.get(
        base_asset, Decimal(0)
    )
    maker_rusd_change = maker_balances_after.get("RUSD", Decimal(0)) - maker_balances_before.get("RUSD", Decimal(0))

    # Taker bought base asset, paid RUSD
    taker_base_change = taker_balances_after.get(base_asset, Decimal(0)) - taker_balances_before.get(
        base_asset, Decimal(0)
    )
    taker_rusd_change = taker_balances_after.get("RUSD", Decimal(0)) - taker_balances_before.get("RUSD", Decimal(0))

    logger.info(f"Maker changes: {base_asset}={maker_base_change}, RUSD={maker_rusd_change}")
    logger.info(f"Taker changes: {base_asset}={taker_base_change}, RUSD={taker_rusd_change}")
    logger.info(f"Expected: qty={qty}, price={execution_price}, rusd_change={expected_rusd_change}")

    # Verify EXACT balance changes (spot has zero fees)
    # Maker sold base asset: base asset decreases by qty, RUSD increases by qty * price
    assert maker_base_change == -qty, f"Maker {base_asset} change should be exactly -{qty}, got: {maker_base_change}"
    assert (
        maker_rusd_change == expected_rusd_change
    ), f"Maker RUSD change should be exactly +{expected_rusd_change}, got: {maker_rusd_change}"

    # Taker bought base asset: base asset increases by qty, RUSD decreases by qty * price
    assert taker_base_change == qty, f"Taker {base_asset} change should be exactly +{qty}, got: {taker_base_change}"
    assert (
        taker_rusd_change == -expected_rusd_change
    ), f"Taker RUSD change should be exactly -{expected_rusd_change}, got: {taker_rusd_change}"

    logger.info("✅ EXACT balance changes verified (zero fees confirmed)")
    logger.info("✅ SPOT BALANCE UPDATE AFTER SELL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_maker_taker_consistency(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test that maker and taker balances sum correctly (conservation of value).

    This test requires a controlled environment to verify exact balance conservation
    between our maker and taker accounts. When external liquidity exists,
    we skip to avoid unpredictable balance changes.

    Since spot trading has ZERO fees, the total base asset and RUSD across both
    accounts should be EXACTLY conserved.

    Flow:
    1. Check for external liquidity - skip if present
    2. Record total balances before trade
    3. Execute trade
    4. Verify total balances are exactly conserved (no fee tolerance needed)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE MAKER/TAKER CONSISTENCY TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists - this test needs controlled environment for balance verification
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping balance consistency test: external liquidity exists. "
            "This test requires a controlled environment to verify exact balance conservation."
        )

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)

    base_asset = spot_config.base_asset
    total_base_before = maker_balances_before.get(base_asset, Decimal(0)) + taker_balances_before.get(
        base_asset, Decimal(0)
    )
    total_rusd_before = maker_balances_before.get("RUSD", Decimal(0)) + taker_balances_before.get("RUSD", Decimal(0))

    logger.info(f"Total before: {base_asset}={total_base_before}, RUSD={total_rusd_before}")

    # Execute a trade
    _ = spot_config.price(0.97)  # maker_price - calculated for reference

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    await taker_tester.orders.create_limit(taker_params)
    await asyncio.sleep(0.05)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Wait for indexer to update balances (REST API may lag behind WebSocket)
    await asyncio.sleep(1.0)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)

    total_base_after = maker_balances_after.get(base_asset, Decimal(0)) + taker_balances_after.get(
        base_asset, Decimal(0)
    )
    total_rusd_after = maker_balances_after.get("RUSD", Decimal(0)) + taker_balances_after.get("RUSD", Decimal(0))

    logger.info(f"Total after: {base_asset}={total_base_after}, RUSD={total_rusd_after}")

    # Calculate differences
    base_diff = total_base_after - total_base_before
    rusd_diff = total_rusd_after - total_rusd_before

    logger.info(f"{base_asset} difference: {base_diff}")
    logger.info(f"RUSD difference: {rusd_diff}")

    # Spot has ZERO fees - both base asset and RUSD should be EXACTLY conserved
    assert base_diff == Decimal(0), f"{base_asset} not exactly conserved (zero fees expected): diff={base_diff}"
    logger.info(f"✅ {base_asset} exactly conserved (zero fees)")

    assert rusd_diff == Decimal(0), f"RUSD not exactly conserved (zero fees expected): diff={rusd_diff}"
    logger.info("✅ RUSD exactly conserved (zero fees)")

    logger.info("✅ SPOT BALANCE MAKER/TAKER CONSISTENCY TEST COMPLETED")
