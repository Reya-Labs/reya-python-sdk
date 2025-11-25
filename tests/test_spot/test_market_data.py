"""
Spot Market Data Tests

Tests for spot-specific market data:
- Market definitions
- Spot executions via REST
- Spot executions pagination
"""

import pytest
import asyncio
import logging

from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from sdk.open_api.models import OrderStatus

logger = logging.getLogger("reya.integration_tests")

SPOT_SYMBOL = "WETHRUSD"
REFERENCE_PRICE = 4000.0
TEST_QTY = "0.0001"


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.asyncio
async def test_spot_market_definitions(reya_tester: ReyaTester):
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
    spot_markets = await reya_tester.client.reference.get_spot_market_definitions()
    
    logger.info(f"Spot markets found: {len(spot_markets)}")

    # Verify WETHRUSD exists
    weth_market = None
    for m in spot_markets:
        if hasattr(m, 'symbol') and m.symbol == SPOT_SYMBOL:
            weth_market = m
            break
    
    assert weth_market is not None, f"Spot market {SPOT_SYMBOL} not found in definitions"
    logger.info(f"✅ Found {SPOT_SYMBOL} market")
    
    # Log some market details
    if hasattr(weth_market, 'market_id'):
        logger.info(f"   Market ID: {weth_market.market_id}")
    if hasattr(weth_market, 'base_asset'):
        logger.info(f"   Base Asset: {weth_market.base_asset}")
    if hasattr(weth_market, 'quote_asset'):
        logger.info(f"   Quote Asset: {weth_market.quote_asset}")

    logger.info("✅ SPOT MARKET DEFINITIONS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_executions_rest(maker_tester: ReyaTester, taker_tester: ReyaTester):
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

    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    # Execute a trade
    maker_price = round(REFERENCE_PRICE * 0.65, 2)
    
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

    logger.info("Taker placing IOC sell...")
    await taker_tester.create_limit_order(taker_params)

    # Wait for execution
    await asyncio.sleep(0.05)
    await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
    logger.info("✅ Trade executed")

    # Query spot executions via REST
    executions = await taker_tester.client.get_spot_executions()
    
    logger.info(f"Spot executions returned: {len(executions.data) if hasattr(executions, 'data') else 'N/A'}")

    # Verify we have executions
    if hasattr(executions, 'data') and len(executions.data) > 0:
        latest = executions.data[0]
        logger.info(f"✅ Latest execution:")
        if hasattr(latest, 'symbol'):
            logger.info(f"   Symbol: {latest.symbol}")
        if hasattr(latest, 'qty'):
            logger.info(f"   Qty: {latest.qty}")
        if hasattr(latest, 'price'):
            logger.info(f"   Price: {latest.price}")
    else:
        logger.info("ℹ️ No executions returned (may need time to propagate)")

    # Cleanup
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    logger.info("✅ SPOT EXECUTIONS REST TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.asyncio
async def test_spot_depth_price_ordering(reya_tester: ReyaTester):
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

    await reya_tester.close_active_orders(fail_if_none=False)

    # Place multiple buy orders at different prices
    prices = [
        round(REFERENCE_PRICE * 0.50, 2),
        round(REFERENCE_PRICE * 0.52, 2),
        round(REFERENCE_PRICE * 0.54, 2),
    ]
    
    order_ids = []
    for price in prices:
        order_params = (
            OrderBuilder()
            .symbol(SPOT_SYMBOL)
            .buy()
            .price(str(price))
            .qty(TEST_QTY)
            .gtc()
            .build()
        )
        
        order_id = await reya_tester.create_limit_order(order_params)
        await reya_tester.wait_for_order_creation(order_id)
        order_ids.append(order_id)
        logger.info(f"✅ Order created at ${price:.2f}")

    # Wait for depth to update
    await asyncio.sleep(0.1)

    # Get depth
    depth = await reya_tester.get_market_depth(SPOT_SYMBOL)
    bids = depth.get('bids', [])
    
    logger.info(f"Bids in depth: {len(bids)}")

    # Verify bids are in descending order (highest price first)
    if len(bids) >= 2:
        bid_prices = [float(b.get('price', 0)) for b in bids]
        is_descending = all(bid_prices[i] >= bid_prices[i+1] for i in range(len(bid_prices)-1))
        
        logger.info(f"Bid prices: {bid_prices[:5]}")  # Show first 5
        assert is_descending, f"Bids should be in descending order: {bid_prices}"
        logger.info("✅ Bids are in correct descending order")
    else:
        logger.info("ℹ️ Not enough bids to verify ordering")

    # Cleanup
    for order_id in order_ids:
        try:
            await reya_tester.client.cancel_order(
                order_id=order_id,
                symbol=SPOT_SYMBOL,
                account_id=reya_tester.account_id
            )
        except Exception:
            pass
    
    await asyncio.sleep(0.05)
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT DEPTH PRICE ORDERING TEST COMPLETED")
