"""
Spot pre-trade checks — live e2e.

The API checks the SPOT asset balance before accepting an IOC (the perp
analogue is a margin check — a different rule, covered by perp suites).
Moved from test_api_validation.py when that file was extracted into
tests/api_contract/: these two tests are spot PHYSICS, not envelope
validation, so they stay under tests/spot/ and its balance guard.
"""

from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.reya_tester import logger


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_ioc_insufficient_balance_buy(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an IOC buy order exceeding RUSD balance is rejected.

    IOC orders have pre-trade balance validation to prevent failed executions.
    Gets the actual RUSD balance and tries to exceed it by a small amount.
    """
    logger.info("=" * 80)
    logger.info("SPOT IOC INSUFFICIENT BALANCE (BUY) TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Get the actual RUSD balance for this account
    balances = await spot_tester.client.get_account_balances()
    rusd_balance = None
    for b in balances:
        if b.account_id == spot_tester.account_id and b.asset == "RUSD":
            rusd_balance = Decimal(b.real_balance)
            break

    if rusd_balance is None or rusd_balance <= 0:
        pytest.skip("No RUSD balance available for this test")
    assert rusd_balance is not None  # narrow after the skip above

    logger.info(f"Current RUSD balance: {rusd_balance}")

    # Calculate qty that would require slightly more RUSD than available
    # At spot_config.oracle_price, we need (rusd_balance / price) + small_extra ETH
    order_price = Decimal(str(spot_config.oracle_price))
    max_qty_at_price = rusd_balance / order_price
    # Request 10% more than we can afford
    exceeding_qty = str((max_qty_at_price * Decimal("1.1")).quantize(Decimal("0.01")))

    order_params = (
        OrderBuilder().symbol(spot_config.symbol).buy().price(str(order_price)).qty(exceeding_qty).ioc().build()
    )

    required_rusd = Decimal(exceeding_qty) * order_price
    logger.info(f"Sending IOC buy for {exceeding_qty} ETH @ ${order_price}")
    logger.info(f"Required RUSD: {required_rusd}, Available: {rusd_balance}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        pytest.fail(f"Order exceeding balance should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        assert "INSUFFICIENT_BALANCE_ERROR" in error_msg, f"Expected INSUFFICIENT_BALANCE_ERROR, got: {e}"
        assert "Insufficient balance" in error_msg, f"Expected 'Insufficient balance' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT IOC INSUFFICIENT BALANCE (BUY) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_ioc_insufficient_balance_sell(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an IOC sell order exceeding base asset balance is rejected.

    IOC orders have pre-trade balance validation to prevent failed executions.
    Gets the actual base asset balance and tries to exceed it by a small amount.
    """
    logger.info("=" * 80)
    logger.info("SPOT IOC INSUFFICIENT BALANCE (SELL) TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Get the actual base asset balance for this account
    base_asset = spot_config.base_asset
    balances = await spot_tester.client.get_account_balances()
    asset_balance = None
    for b in balances:
        if b.account_id == spot_tester.account_id and b.asset == base_asset:
            asset_balance = Decimal(b.real_balance)
            break

    if asset_balance is None or asset_balance <= 0:
        pytest.skip(f"No {base_asset} balance available for this test")
    assert asset_balance is not None  # narrow after the skip above

    logger.info(f"Current {base_asset} balance: {asset_balance}")

    # Request 10% more than we have, quantized to qty_step_size
    qty_step = Decimal(spot_config.qty_step_size) if hasattr(spot_config, "qty_step_size") else Decimal("0.01")
    exceeding_qty = str((asset_balance * Decimal("1.1")).quantize(qty_step))
    # Round price to tick size
    order_price = str(spot_config.price(1.0))

    order_params = OrderBuilder().symbol(spot_config.symbol).sell().price(order_price).qty(exceeding_qty).ioc().build()

    logger.info(f"Sending IOC sell for {exceeding_qty} {base_asset} @ ${order_price}")
    logger.info(f"Required {base_asset}: {exceeding_qty}, Available: {asset_balance}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        pytest.fail(f"Order exceeding balance should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        assert "INSUFFICIENT_BALANCE_ERROR" in error_msg, f"Expected INSUFFICIENT_BALANCE_ERROR, got: {e}"
        assert "Insufficient balance" in error_msg, f"Expected 'Insufficient balance' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT IOC INSUFFICIENT BALANCE (SELL) TEST COMPLETED")
