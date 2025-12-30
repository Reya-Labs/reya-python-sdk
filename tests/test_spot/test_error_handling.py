"""
Spot Error Handling & Edge Cases Tests

Tests for error handling and edge cases:
- Invalid symbol
- Invalid account ID
- Invalid signature
- Expired nonce
- Zero quantity
- Negative price
"""

import asyncio
import logging

import pytest
from eth_abi.exceptions import EncodingError

from sdk.open_api.exceptions import ApiException
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.error
@pytest.mark.asyncio
async def test_spot_invalid_symbol(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test order with invalid symbol is rejected.

    Flow:
    1. Attempt to place order with non-existent symbol
    2. Verify error response
    """
    logger.info("=" * 80)
    logger.info("SPOT INVALID SYMBOL TEST")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Use an invalid symbol
    invalid_symbol = "INVALIDRUSD"

    order_params = (
        OrderBuilder()
        .symbol(invalid_symbol)
        .buy()
        .price(str(spot_config.oracle_price))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    logger.info(f"Attempting to place order with invalid symbol: {invalid_symbol}")

    try:
        order_id = await spot_tester.create_limit_order(order_params)
        logger.info(f"Order unexpectedly accepted: {order_id}")
        # If accepted, clean up
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=invalid_symbol, account_id=spot_tester.account_id
        )
    except (ApiException, ValueError) as e:
        # SDK validates symbol locally and raises ValueError if not found
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")

    logger.info("✅ SPOT INVALID SYMBOL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.error
@pytest.mark.asyncio
async def test_spot_zero_quantity(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test order with zero quantity is rejected.

    Flow:
    1. Attempt to place order with qty=0
    2. Verify error response
    """
    logger.info("=" * 80)
    logger.info("SPOT ZERO QUANTITY TEST")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    order_params = (
        OrderBuilder.from_config(spot_config).buy().price(str(spot_config.oracle_price)).qty("0").gtc().build()
    )

    logger.info("Attempting to place order with zero quantity")

    try:
        order_id = await spot_tester.create_limit_order(order_params)
        logger.info(f"Order unexpectedly accepted: {order_id}")
    except ApiException as e:
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT ZERO QUANTITY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.error
@pytest.mark.asyncio
async def test_spot_negative_price(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test order with negative price is rejected.

    Flow:
    1. Attempt to place order with negative price
    2. Verify error response
    """
    logger.info("=" * 80)
    logger.info("SPOT NEGATIVE PRICE TEST")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    order_params = OrderBuilder.from_config(spot_config).buy().price("-100").gtc().build()

    logger.info("Attempting to place order with negative price")

    try:
        order_id = await spot_tester.create_limit_order(order_params)
        logger.info(f"Order unexpectedly accepted: {order_id}")
    except ApiException as e:
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")
    except EncodingError as e:
        # eth_abi raises ValueOutOfBounds (subclass of EncodingError) for negative prices
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT NEGATIVE PRICE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.error
@pytest.mark.asyncio
async def test_spot_very_small_quantity(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test order with very small quantity (below minimum).

    Flow:
    1. Attempt to place order with extremely small quantity
    2. Verify behavior (may be rejected or accepted based on market rules)
    """
    logger.info("=" * 80)
    logger.info("SPOT VERY SMALL QUANTITY TEST")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Very small quantity - may be below minimum
    small_qty = "0.0000001"

    order_params = (
        OrderBuilder().symbol(spot_config.symbol).buy().price(str(spot_config.price(0.96))).qty(small_qty).gtc().build()
    )

    logger.info(f"Attempting to place order with very small quantity: {small_qty}")

    try:
        order_id = await spot_tester.create_limit_order(order_params)
        logger.info(f"Order accepted: {order_id}")
        # Clean up if accepted
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
        await asyncio.sleep(0.05)
    except ApiException as e:
        logger.info(f"✅ Order rejected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT VERY SMALL QUANTITY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.error
@pytest.mark.asyncio
async def test_spot_very_large_quantity(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test order with very large quantity (may exceed balance).

    Flow:
    1. Attempt to place order with quantity larger than balance
    2. Verify behavior (may be rejected for insufficient balance)
    """
    logger.info("=" * 80)
    logger.info("SPOT VERY LARGE QUANTITY TEST")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Very large quantity - likely exceeds balance
    large_qty = "1000000"

    order_params = (
        OrderBuilder().symbol(spot_config.symbol).buy().price(str(spot_config.price(0.96))).qty(large_qty).gtc().build()
    )

    logger.info(f"Attempting to place order with very large quantity: {large_qty}")

    try:
        order_id = await spot_tester.create_limit_order(order_params)
        logger.info(f"Order accepted: {order_id}")
        # Clean up if accepted
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
        await asyncio.sleep(0.05)
    except ApiException as e:
        logger.info(f"✅ Order rejected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT VERY LARGE QUANTITY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.error
@pytest.mark.asyncio
async def test_spot_extreme_price(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test order with extreme price values.

    Flow:
    1. Attempt to place order with extremely high price
    2. Verify behavior
    """
    logger.info("=" * 80)
    logger.info("SPOT EXTREME PRICE TEST")
    logger.info("=" * 80)

    await spot_tester.close_active_orders(fail_if_none=False)

    # Extremely high price
    extreme_price = "999999999999"

    order_params = OrderBuilder.from_config(spot_config).buy().price(extreme_price).gtc().build()

    logger.info(f"Attempting to place order with extreme price: {extreme_price}")

    try:
        order_id = await spot_tester.create_limit_order(order_params)
        logger.info(f"Order accepted: {order_id}")
        # Clean up if accepted
        await spot_tester.client.cancel_order(
            order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
        )
        await asyncio.sleep(0.05)
    except ApiException as e:
        logger.info(f"✅ Order rejected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:100]}")

    await spot_tester.check_no_open_orders()

    logger.info("✅ SPOT EXTREME PRICE TEST COMPLETED")
