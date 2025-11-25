"""
Spot Balance Verification Tests

Tests for verifying balance changes after spot trades:
- Balance update after buy
- Balance update after sell
- WebSocket balance matches REST
- Maker/taker balance consistency
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
REFERENCE_PRICE = 4000.0
TEST_QTY = "0.0001"


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
        .reduce_only(False)
        .build()
    )

    logger.info(f"Taker placing IOC buy: {TEST_QTY} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for trade to settle
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)
    
    logger.info(f"Maker after: ETH={maker_balances_after.get('ETH', 0)}, RUSD={maker_balances_after.get('RUSD', 0)}")
    logger.info(f"Taker after: ETH={taker_balances_after.get('ETH', 0)}, RUSD={taker_balances_after.get('RUSD', 0)}")

    # Calculate changes
    qty = Decimal(TEST_QTY)
    
    # Maker sold ETH, received RUSD
    maker_eth_change = maker_balances_after.get('ETH', 0) - maker_balances_before.get('ETH', 0)
    maker_rusd_change = maker_balances_after.get('RUSD', 0) - maker_balances_before.get('RUSD', 0)
    
    # Taker bought ETH, paid RUSD
    taker_eth_change = taker_balances_after.get('ETH', 0) - taker_balances_before.get('ETH', 0)
    taker_rusd_change = taker_balances_after.get('RUSD', 0) - taker_balances_before.get('RUSD', 0)

    logger.info(f"Maker changes: ETH={maker_eth_change}, RUSD={maker_rusd_change}")
    logger.info(f"Taker changes: ETH={taker_eth_change}, RUSD={taker_rusd_change}")

    # Verify directions (allow for fees)
    assert maker_eth_change < 0, f"Maker should have less ETH after selling, got change: {maker_eth_change}"
    assert maker_rusd_change > 0, f"Maker should have more RUSD after selling, got change: {maker_rusd_change}"
    assert taker_eth_change > 0, f"Taker should have more ETH after buying, got change: {taker_eth_change}"
    assert taker_rusd_change < 0, f"Taker should have less RUSD after buying, got change: {taker_rusd_change}"

    logger.info("✅ Balance changes verified correctly")
    logger.info("✅ SPOT BALANCE UPDATE AFTER BUY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_update_after_sell(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test balance changes correctly after spot sell.
    
    When taker sells ETH:
    - Taker: ETH balance decreases, RUSD balance increases
    - Maker: ETH balance increases, RUSD balance decreases
    
    Flow:
    1. Record initial balances
    2. Maker places buy order
    3. Taker sells with IOC
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

    # Maker places GTC buy order
    maker_price = round(REFERENCE_PRICE * 0.60, 2)  # Low price to avoid other matches
    
    maker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .buy()
        .price(str(maker_price))
        .qty(TEST_QTY)
        .gtc()
        .build()
    )

    logger.info(f"Maker placing GTC buy: {TEST_QTY} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.create_limit_order(maker_params)
    await maker_tester.wait_for_order_creation(maker_order_id)
    logger.info(f"✅ Maker order created: {maker_order_id}")

    # Taker sells with IOC
    taker_price = round(maker_price * 0.99, 2)
    
    taker_params = (
        OrderBuilder()
        .symbol(SPOT_SYMBOL)
        .sell()
        .price(str(taker_price))
        .qty(TEST_QTY)
        .ioc()
        .reduce_only(False)
        .build()
    )

    logger.info(f"Taker placing IOC sell: {TEST_QTY} @ ${taker_price:.2f}")
    await taker_tester.create_limit_order(taker_params)

    # Wait for trade to settle
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)
    
    logger.info(f"Maker after: ETH={maker_balances_after.get('ETH', 0)}, RUSD={maker_balances_after.get('RUSD', 0)}")
    logger.info(f"Taker after: ETH={taker_balances_after.get('ETH', 0)}, RUSD={taker_balances_after.get('RUSD', 0)}")

    # Calculate changes
    # Maker bought ETH, paid RUSD
    maker_eth_change = maker_balances_after.get('ETH', 0) - maker_balances_before.get('ETH', 0)
    maker_rusd_change = maker_balances_after.get('RUSD', 0) - maker_balances_before.get('RUSD', 0)
    
    # Taker sold ETH, received RUSD
    taker_eth_change = taker_balances_after.get('ETH', 0) - taker_balances_before.get('ETH', 0)
    taker_rusd_change = taker_balances_after.get('RUSD', 0) - taker_balances_before.get('RUSD', 0)

    logger.info(f"Maker changes: ETH={maker_eth_change}, RUSD={maker_rusd_change}")
    logger.info(f"Taker changes: ETH={taker_eth_change}, RUSD={taker_rusd_change}")

    # Verify directions (allow for fees)
    assert maker_eth_change > 0, f"Maker should have more ETH after buying, got change: {maker_eth_change}"
    assert maker_rusd_change < 0, f"Maker should have less RUSD after buying, got change: {maker_rusd_change}"
    assert taker_eth_change < 0, f"Taker should have less ETH after selling, got change: {taker_eth_change}"
    assert taker_rusd_change > 0, f"Taker should have more RUSD after selling, got change: {taker_rusd_change}"

    logger.info("✅ Balance changes verified correctly")
    logger.info("✅ SPOT BALANCE UPDATE AFTER SELL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.balance
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_balance_maker_taker_consistency(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test that maker and taker balances sum correctly (conservation of value).
    
    The total ETH and RUSD across both accounts should be conserved
    (minus any fees).
    
    Flow:
    1. Record total balances before trade
    2. Execute trade
    3. Verify total balances are conserved (within fee tolerance)
    """
    logger.info("=" * 80)
    logger.info(f"SPOT BALANCE MAKER/TAKER CONSISTENCY TEST: {SPOT_SYMBOL}")
    logger.info("=" * 80)

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Record initial balances
    maker_balances_before = await get_account_balances(maker_tester)
    taker_balances_before = await get_account_balances(taker_tester)
    
    total_eth_before = maker_balances_before.get('ETH', 0) + taker_balances_before.get('ETH', 0)
    total_rusd_before = maker_balances_before.get('RUSD', 0) + taker_balances_before.get('RUSD', 0)
    
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
        .reduce_only(False)
        .build()
    )

    await taker_tester.create_limit_order(taker_params)
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    # Get balances after trade
    maker_balances_after = await get_account_balances(maker_tester)
    taker_balances_after = await get_account_balances(taker_tester)
    
    total_eth_after = maker_balances_after.get('ETH', 0) + taker_balances_after.get('ETH', 0)
    total_rusd_after = maker_balances_after.get('RUSD', 0) + taker_balances_after.get('RUSD', 0)
    
    logger.info(f"Total after: ETH={total_eth_after}, RUSD={total_rusd_after}")

    # ETH should be conserved exactly (no ETH fees typically)
    eth_diff = abs(total_eth_after - total_eth_before)
    logger.info(f"ETH difference: {eth_diff}")
    
    # RUSD might have small fee deduction
    rusd_diff = total_rusd_before - total_rusd_after  # Positive if fees taken
    logger.info(f"RUSD difference (fees): {rusd_diff}")

    # ETH should be exactly conserved
    assert eth_diff < Decimal('0.000001'), f"ETH not conserved: diff={eth_diff}"
    logger.info("✅ ETH conserved across accounts")

    # RUSD should be conserved or slightly reduced (fees)
    assert rusd_diff >= 0, f"RUSD increased unexpectedly: diff={rusd_diff}"
    logger.info("✅ RUSD conserved (minus fees)")

    logger.info("✅ SPOT BALANCE MAKER/TAKER CONSISTENCY TEST COMPLETED")
