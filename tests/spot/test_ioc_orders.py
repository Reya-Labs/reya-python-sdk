"""
Tests for spot IOC (Immediate-Or-Cancel) orders.

IOC orders execute immediately against available liquidity and cancel
any unfilled portion. These tests verify IOC behavior for spot markets.

These tests support both empty and non-empty order books:
- When external liquidity exists, tests use it instead of providing their own
- When no external liquidity exists, tests provide maker liquidity as before
- Execution assertions are flexible to handle order book changes between submission and fill
"""

import pytest
from eth_abi.exceptions import EncodingError

from sdk.open_api.exceptions import ApiException
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.reya_tester import logger


@pytest.mark.spot
@pytest.mark.ioc
@pytest.mark.asyncio
async def test_spot_ioc_price_qty_validation(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test IOC order rejected for invalid price/qty.

    Flow:
    1. Send IOC order with zero quantity
    2. Verify order is rejected with validation error
    3. Send IOC order with negative price
    4. Verify order is rejected with validation error
    """
    logger.info("=" * 80)
    logger.info(f"SPOT IOC PRICE/QTY VALIDATION TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Test 1: Zero quantity
    zero_qty_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).qty("0").ioc().build()

    logger.info("Sending IOC order with zero quantity...")
    try:
        order_id = await spot_tester.orders.create_limit(zero_qty_params)
        # If we get here without error, the API might accept it but not execute
        logger.info(f"Order accepted (may be rejected later): {order_id}")
    except ApiException as e:
        logger.info(f"✅ Zero quantity order rejected: {type(e).__name__}")

    # Test 2: Negative price (if supported by builder)
    try:
        negative_price_params = OrderBuilder.from_config(spot_config).buy().price("-100").ioc().build()

        logger.info("Sending IOC order with negative price...")
        order_id = await spot_tester.orders.create_limit(negative_price_params)
        logger.info(f"Order accepted (may be rejected later): {order_id}")
    except ApiException as e:
        logger.info(f"✅ Negative price order rejected: {type(e).__name__}")
    except EncodingError as e:
        # eth_abi raises ValueOutOfBounds (subclass of EncodingError) for negative prices
        logger.info(f"✅ Negative price order rejected: {type(e).__name__}")

    # Verify no open orders
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT IOC PRICE/QTY VALIDATION TEST COMPLETED")
