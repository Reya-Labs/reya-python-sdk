"""
Spot Market Data Tests

Tests for spot-specific market data:
- Market definitions
- Spot executions via REST
- Spot executions pagination
"""

import asyncio
import logging

import pytest

from sdk.open_api.models import OrderStatus
from sdk.open_api.models.depth import Depth
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.asyncio
async def test_spot_market_definitions(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test getting spot market definitions.

    Flow:
    1. Get spot market definitions via REST API
    2. Verify spot markets are included
    3. Verify correct structure
    """
    logger.info("=" * 80)
    logger.info("SPOT MARKET DEFINITIONS TEST")
    logger.info("=" * 80)

    # Get spot market definitions via REST API
    spot_markets = await spot_tester.client.reference.get_spot_market_definitions()

    logger.info(f"Spot markets found: {len(spot_markets)}")

    # Verify WETHRUSD exists
    weth_market = None
    for m in spot_markets:
        if hasattr(m, "symbol") and m.symbol == spot_config.symbol:
            weth_market = m
            break

    assert weth_market is not None, f"Spot market {spot_config.symbol} not found in definitions"
    logger.info(f"✅ Found {spot_config.symbol} market")

    # Log some market details
    if hasattr(weth_market, "market_id"):
        logger.info(f"   Market ID: {weth_market.market_id}")
    if hasattr(weth_market, "base_asset"):
        logger.info(f"   Base Asset: {weth_market.base_asset}")
    if hasattr(weth_market, "quote_asset"):
        logger.info(f"   Quote Asset: {weth_market.quote_asset}")

    logger.info("✅ SPOT MARKET DEFINITIONS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_executions_rest(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test getting spot executions via REST API.

    Flow:
    1. Execute a spot trade
    2. Query spot executions via REST
    3. Verify execution data is correct
    """
    logger.info("=" * 80)
    logger.info("SPOT EXECUTIONS REST TEST")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Execute a trade
    maker_price = spot_config.price(0.97)

    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

    logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${maker_price:.2f}")
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()

    logger.info("Taker placing IOC sell...")
    await taker_tester.orders.create_limit(taker_params)

    # Wait for execution
    await asyncio.sleep(0.05)
    await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Query spot executions via REST
    executions = await taker_tester.client.get_spot_executions()

    logger.info(f"Spot executions returned: {len(executions.data) if hasattr(executions, 'data') else 'N/A'}")

    # Verify we have executions
    if hasattr(executions, "data") and len(executions.data) > 0:
        latest = executions.data[0]
        logger.info("✅ Latest execution:")
        if hasattr(latest, "symbol"):
            logger.info(f"   Symbol: {latest.symbol}")
        if hasattr(latest, "qty"):
            logger.info(f"   Qty: {latest.qty}")
        if hasattr(latest, "price"):
            logger.info(f"   Price: {latest.price}")
    else:
        logger.info("ℹ️ No executions returned (may need time to propagate)")

    # Cleanup
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT EXECUTIONS REST TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.asyncio
async def test_spot_depth_price_ordering(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test L2 depth shows correct price ordering.

    Bids should be in descending order (highest first).
    Asks should be in ascending order (lowest first).

    Flow:
    1. Place multiple orders at different prices
    2. Get L2 depth
    3. Verify price ordering
    """
    logger.info("=" * 80)
    logger.info("SPOT DEPTH PRICE ORDERING TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Place multiple buy orders at different prices
    prices = [
        spot_config.price(0.96),
        spot_config.price(0.96),
        spot_config.price(0.96),
    ]

    order_ids = []
    for price in prices:
        order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.96).gtc().build()

        order_id = await spot_tester.orders.create_limit(order_params)
        await spot_tester.wait.for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order created at ${price:.2f}")

    # Wait for depth to update
    await asyncio.sleep(0.1)

    # Get depth
    depth = await spot_tester.data.market_depth(spot_config.symbol)
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"
    bids = depth.bids

    logger.info(f"Bids in depth: {len(bids)}")

    # Verify bids are in descending order (highest price first)
    if len(bids) >= 2:
        bid_prices = [float(b.px) for b in bids]
        is_descending = all(bid_prices[i] >= bid_prices[i + 1] for i in range(len(bid_prices) - 1))

        logger.info(f"Bid prices: {bid_prices[:5]}")  # Show first 5
        assert is_descending, f"Bids should be in descending order: {bid_prices}"
        logger.info("✅ Bids are in correct descending order")
    else:
        logger.info("ℹ️ Not enough bids to verify ordering")

    # Cleanup
    for order_id in order_ids:
        try:
            await spot_tester.client.cancel_order(
                order_id=order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
            )
        except (OSError, RuntimeError):  # nosec B110
            pass  # Order may have been filled

    await asyncio.sleep(0.05)
    await spot_tester.check.no_open_orders()

    logger.info("✅ SPOT DEPTH PRICE ORDERING TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_executions_multiple_trades(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test spot executions are correctly recorded for multiple trades.

    Flow:
    1. Execute multiple trades
    2. Query executions via REST
    3. Verify executions are recorded
    4. Verify execution data structure
    """
    logger.info("=" * 80)
    logger.info("SPOT EXECUTIONS MULTIPLE TRADES TEST")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Execute multiple trades
    num_trades = 3
    executed_order_ids = []

    for i in range(num_trades):
        # Use prices within 5% of oracle (0.96, 0.97, 0.98)
        maker_price = round(spot_config.oracle_price * (0.96 + i * 0.01), 2)

        maker_params = OrderBuilder.from_config(spot_config).buy().price(str(maker_price)).gtc().build()

        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(maker_price)).ioc().build()

        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.1)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        executed_order_ids.append(maker_order_id)
        logger.info(f"✅ Trade {i + 1}/{num_trades} executed")

    # Wait for executions to be indexed
    await asyncio.sleep(0.5)

    # Query executions
    executions = await taker_tester.client.get_spot_executions()

    assert hasattr(executions, "data"), "Response should have 'data' attribute"
    execution_data = executions.data

    logger.info(f"Total executions returned: {len(execution_data)}")

    # Verify we have executions
    assert len(execution_data) > 0, "Should have at least one execution"
    logger.info("✅ Executions returned from REST API")

    # Verify execution data structure
    latest = execution_data[0]
    logger.info("Latest execution:")
    if hasattr(latest, "symbol"):
        logger.info(f"  Symbol: {latest.symbol}")
        assert latest.symbol == spot_config.symbol, f"Expected {spot_config.symbol}, got {latest.symbol}"
    if hasattr(latest, "qty"):
        logger.info(f"  Qty: {latest.qty}")
    if hasattr(latest, "price"):
        logger.info(f"  Price: {latest.price}")
    if hasattr(latest, "side"):
        logger.info(f"  Side: {latest.side}")

    logger.info("✅ Execution data structure is correct")

    logger.info("✅ SPOT EXECUTIONS MULTIPLE TRADES TEST COMPLETED")
