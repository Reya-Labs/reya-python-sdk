"""
Spot Balance Verification Tests

Tests for verifying balance changes after spot trades:
- Balance update after buy
- Balance update after sell
- WebSocket balance matches REST
- Maker/taker balance consistency

Note: Spot trading has ZERO fees, so we can verify exact balance changes:
- ETH change = trade quantity
- RUSD change = trade quantity * execution price
"""

import pytest
import asyncio
import logging
from decimal import Decimal

from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from sdk.open_api.models import OrderStatus

logger = logging.getLogger("reya.integration_tests")

SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 500.0
TEST_QTY = "0.01"  # Minimum order base for market ID 5


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
async def test_spot_balance_update_after_buy(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test balance changes correctly after spot buy.
    
    When taker buys ETH:
    - Taker: ETH balance increases, RUSD balance decreases
    - Maker: ETH balance decreases, RUSD balance increases
    
    Flow:
    1. Record initial balances
    2. Maker places sell order
    3. Taker buys with IOC
    4. Verify balance changes
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE UPDATE AFTER BUY TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)
    
    logger.info(f"Maker initial: ETH={maker_balances_before.get('ETH', 0)}, RUSD={maker_balances_before.get('RUSD', 0)}")
    logger.info(f"Taker initial: ETH={taker_balances_before.get('ETH', 0)}, RUSD={taker_balances_before.get('RUSD', 0)}")

    # Maker places GTC sell order
    maker_price = round(REFERENCE_PRICE * 1.50, 2)  # High price to avoid other matches
    
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC sell: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker buys with IOC
    taker_price = round(maker_price * 1.01, 2)
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    logger.info(f"Taker placing IOC buy: {TEST_QTY} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for trade to settle
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Wait for indexer to update balances (REST API may lag behind WebSocket)
    await asyncio.sleep(1.0)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)
    
    logger.info(f"Maker after: ETH={maker_balances_after.get('ETH', 0)}, RUSD={maker_balances_after.get('RUSD', 0)}")
    logger.info(f"Taker after: ETH={taker_balances_after.get('ETH', 0)}, RUSD={taker_balances_after.get('RUSD', 0)}")

    # Calculate changes
    qty = Decimal(TEST_QTY)
    execution_price = Decimal(str(maker_price))  # Trade executes at maker's price
    expected_rusd_change = qty * execution_price

    # Maker sold ETH, received RUSD
    maker_eth_change = maker_balances_after.get('ETH', Decimal(0)) - maker_balances_before.get('ETH', Decimal(0))
    maker_rusd_change = maker_balances_after.get('RUSD', Decimal(0)) - maker_balances_before.get('RUSD', Decimal(0))

    # Taker bought ETH, paid RUSD
    taker_eth_change = taker_balances_after.get('ETH', Decimal(0)) - taker_balances_before.get('ETH', Decimal(0))
    taker_rusd_change = taker_balances_after.get('RUSD', Decimal(0)) - taker_balances_before.get('RUSD', Decimal(0))

    logger.info(f"Maker changes: ETH={maker_eth_change}, RUSD={maker_rusd_change}")
    logger.info(f"Taker changes: ETH={taker_eth_change}, RUSD={taker_rusd_change}")
    logger.info(f"Expected: qty={qty}, price={execution_price}, rusd_change={expected_rusd_change}")

    # Verify EXACT balance changes (spot has zero fees)
    # Maker sold ETH: ETH decreases by qty, RUSD increases by qty * price
    assert maker_eth_change == -qty, \
        f"Maker ETH change should be exactly -{qty}, got: {maker_eth_change}"
    assert maker_rusd_change == expected_rusd_change, \
        f"Maker RUSD change should be exactly +{expected_rusd_change}, got: {maker_rusd_change}"

    # Taker bought ETH: ETH increases by qty, RUSD decreases by qty * price
    assert taker_eth_change == qty, \
        f"Taker ETH change should be exactly +{qty}, got: {taker_eth_change}"
    assert taker_rusd_change == -expected_rusd_change, \
        f"Taker RUSD change should be exactly -{expected_rusd_change}, got: {taker_rusd_change}"

    logger.info("✅ EXACT balance changes verified (zero fees confirmed)")
    logger.info("✅ SPOT BALANCE UPDATE AFTER BUY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_update_after_sell(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test balance changes correctly after spot sell.

    When maker sells ETH (maker has more ETH):
    - Maker: ETH balance decreases, RUSD balance increases
    - Taker: ETH balance increases, RUSD balance decreases

    Flow:
    1. Record initial balances
    2. Maker places sell order
    3. Taker buys with IOC
    4. Verify balance changes
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE UPDATE AFTER SELL TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)

    logger.info(f"Maker initial: ETH={maker_balances_before.get('ETH', 0)}, RUSD={maker_balances_before.get('RUSD', 0)}")
    logger.info(f"Taker initial: ETH={taker_balances_before.get('ETH', 0)}, RUSD={taker_balances_before.get('RUSD', 0)}")

    # Maker places GTC sell order (maker has more ETH)
    maker_price = round(REFERENCE_PRICE * 1.50, 2)  # High price to avoid other matches

    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC sell: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker buys with IOC (taker has more RUSD)
    taker_price = round(maker_price * 1.01, 2)

    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    logger.info(f"Taker placing IOC buy: {TEST_QTY} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for trade to settle
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Wait for indexer to update balances (REST API may lag behind WebSocket)
    await asyncio.sleep(1.0)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)

    logger.info(f"Maker after: ETH={maker_balances_after.get('ETH', 0)}, RUSD={maker_balances_after.get('RUSD', 0)}")
    logger.info(f"Taker after: ETH={taker_balances_after.get('ETH', 0)}, RUSD={taker_balances_after.get('RUSD', 0)}")

    # Calculate changes
    qty = Decimal(TEST_QTY)
    execution_price = Decimal(str(maker_price))  # Trade executes at maker's price
    expected_rusd_change = qty * execution_price

    # Maker sold ETH, received RUSD
    maker_eth_change = maker_balances_after.get('ETH', Decimal(0)) - maker_balances_before.get('ETH', Decimal(0))
    maker_rusd_change = maker_balances_after.get('RUSD', Decimal(0)) - maker_balances_before.get('RUSD', Decimal(0))

    # Taker bought ETH, paid RUSD
    taker_eth_change = taker_balances_after.get('ETH', Decimal(0)) - taker_balances_before.get('ETH', Decimal(0))
    taker_rusd_change = taker_balances_after.get('RUSD', Decimal(0)) - taker_balances_before.get('RUSD', Decimal(0))

    logger.info(f"Maker changes: ETH={maker_eth_change}, RUSD={maker_rusd_change}")
    logger.info(f"Taker changes: ETH={taker_eth_change}, RUSD={taker_rusd_change}")
    logger.info(f"Expected: qty={qty}, price={execution_price}, rusd_change={expected_rusd_change}")

    # Verify EXACT balance changes (spot has zero fees)
    # Maker sold ETH: ETH decreases by qty, RUSD increases by qty * price
    assert maker_eth_change == -qty, \
        f"Maker ETH change should be exactly -{qty}, got: {maker_eth_change}"
    assert maker_rusd_change == expected_rusd_change, \
        f"Maker RUSD change should be exactly +{expected_rusd_change}, got: {maker_rusd_change}"

    # Taker bought ETH: ETH increases by qty, RUSD decreases by qty * price
    assert taker_eth_change == qty, \
        f"Taker ETH change should be exactly +{qty}, got: {taker_eth_change}"
    assert taker_rusd_change == -expected_rusd_change, \
        f"Taker RUSD change should be exactly -{expected_rusd_change}, got: {taker_rusd_change}"

    logger.info("✅ EXACT balance changes verified (zero fees confirmed)")
    logger.info("✅ SPOT BALANCE UPDATE AFTER SELL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_maker_taker_consistency(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test that maker and taker balances sum correctly (conservation of value).

    Since spot trading has ZERO fees, the total ETH and RUSD across both
    accounts should be EXACTLY conserved.

    Flow:
    1. Record total balances before trade
    2. Execute trade
    3. Verify total balances are exactly conserved (no fee tolerance needed)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE MAKER/TAKER CONSISTENCY TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)

    total_eth_before = maker_balances_before.get('ETH', Decimal(0)) + taker_balances_before.get('ETH', Decimal(0))
    total_rusd_before = maker_balances_before.get('RUSD', Decimal(0)) + taker_balances_before.get('RUSD', Decimal(0))

    logger.info(f"Total before: ETH={total_eth_before}, RUSD={total_rusd_before}")

    # Execute a trade
    maker_price = round(REFERENCE_PRICE * 0.60, 2)
    
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)

    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(round(maker_price * 0.99, 2)))
        .qty(TEST_QTY)
        .ioc()
        .build()
    )

    await taker_tester.create_limit_order(taker_params)
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Wait for indexer to update balances (REST API may lag behind WebSocket)
    await asyncio.sleep(1.0)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)

    total_eth_after = maker_balances_after.get('ETH', Decimal(0)) + taker_balances_after.get('ETH', Decimal(0))
    total_rusd_after = maker_balances_after.get('RUSD', Decimal(0)) + taker_balances_after.get('RUSD', Decimal(0))

    logger.info(f"Total after: ETH={total_eth_after}, RUSD={total_rusd_after}")

    # Calculate differences
    eth_diff = total_eth_after - total_eth_before
    rusd_diff = total_rusd_after - total_rusd_before

    logger.info(f"ETH difference: {eth_diff}")
    logger.info(f"RUSD difference: {rusd_diff}")

    # Spot has ZERO fees - both ETH and RUSD should be EXACTLY conserved
    assert eth_diff == Decimal(0), \
        f"ETH not exactly conserved (zero fees expected): diff={eth_diff}"
    logger.info("✅ ETH exactly conserved (zero fees)")

    assert rusd_diff == Decimal(0), \
        f"RUSD not exactly conserved (zero fees expected): diff={rusd_diff}"
    logger.info("✅ RUSD exactly conserved (zero fees)")

    logger.info("✅ SPOT BALANCE MAKER/TAKER CONSISTENCY TEST COMPLETED")
