"""
Spot WebSocket Event Verification Tests

Tests for verifying WebSocket events during spot trading:
- Order changes on create
- Order changes on fill
- Order changes on cancel
- Spot executions
- Balance updates

These tests verify both that events are received AND that the event
content matches expectations using centralized assertion helpers.
"""

import logging

import pytest

from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.reya_tester import limit_order_params_to_order

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_spot_executions(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket spotExecutions event received on trade.

    Supports both empty and non-empty order books:
    - If external bid liquidity exists, taker sells into it
    - If no external liquidity, maker provides bid liquidity first

    Flow:
    1. Check for external liquidity
    2. If needed, maker places GTC order
    3. Taker fills with IOC order
    4. Verify spotExecutions event received with correct details
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS SPOT EXECUTIONS TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Clear WebSocket tracking
    taker_tester.ws.last_spot_execution = None

    # Check for external liquidity
    await spot_config.refresh_order_book(maker_tester.data)

    maker_order_id = None
    usable_bid_price = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask_price = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    if usable_bid_price is not None:
        # External bid liquidity exists - taker sells into it
        fill_price = float(usable_bid_price)
        logger.info(f"Using external bid liquidity at ${fill_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()
        logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${fill_price:.2f}")
        expected_side = "A"  # Taker was selling
    elif usable_ask_price is not None:
        # External ask liquidity exists - taker buys from it
        fill_price = float(usable_ask_price)
        logger.info(f"Using external ask liquidity at ${fill_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(fill_price)).ioc().build()
        logger.info(f"Taker placing IOC buy: {spot_config.min_qty} @ ${fill_price:.2f}")
        expected_side = "B"  # Taker was buying
    else:
        # No external liquidity - provide our own
        fill_price = spot_config.price(0.97)

        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()

        logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${fill_price:.2f}")
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker order created: {maker_order_id}")

        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(fill_price)).ioc().build()
        logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${fill_price:.2f}")
        expected_side = "A"  # Taker was selling

    taker_order_id = await taker_tester.orders.create_limit(taker_params)

    # Wait for spot execution event via WebSocket (strict matching on order_id and all fields)
    expected_order = limit_order_params_to_order(taker_params, taker_tester.account_id)
    execution = await taker_tester.wait.for_spot_execution(taker_order_id, expected_order, timeout=5)

    # Verify spot execution details
    assert execution is not None, "No spot execution event received via WebSocket"
    assert execution.symbol == spot_config.symbol
    assert execution.side.value == expected_side
    assert execution.qty == spot_config.min_qty
    # Price may differ slightly due to order book changes, just verify it's within circuit breaker range
    exec_price = float(execution.price)
    assert (
        spot_config.circuit_breaker_floor <= exec_price <= spot_config.circuit_breaker_ceiling
    ), f"Fill price ${exec_price} should be within circuit breaker range"
    logger.info(f"✅ Spot execution received: {execution.order_id}")

    # Verify no open orders
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT WS SPOT EXECUTIONS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_spot_ws_balance_updates(spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    Test WebSocket accountBalances event received on trade.

    This test verifies that balance update events are received via WebSocket
    after a trade executes. Works with external liquidity by using IOC orders
    that match against external bids.

    Flow:
    1. Record initial balance update count
    2. Execute a trade (IOC sell against external bids or maker order)
    3. Verify balance update events received via WebSocket
    """
    logger.info("=" * 80)
    logger.info(f"SPOT WS BALANCE UPDATES TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Check current order book state
    await spot_config.refresh_order_book(taker_tester.data)

    # Record initial balance update count for taker
    taker_initial_count = taker_tester.ws.get_balance_update_count()
    logger.info(f"Initial taker balance update count: {taker_initial_count}")

    # Determine trade price based on liquidity
    if spot_config.has_usable_bid_liquidity:
        # External bids exist - taker sells to external
        bid_price = spot_config.best_bid_price
        assert bid_price is not None
        trade_price = float(bid_price)
        logger.info(f"Using external bid liquidity at ${trade_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(bid_price)).ioc().build()
        expected_asset = "RUSD"  # Taker sells ETH, receives RUSD

        # Taker executes IOC sell
        logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${trade_price:.2f}")
        await taker_tester.orders.create_limit(taker_params)
    elif spot_config.has_usable_ask_liquidity:
        # External asks exist - taker buys from external
        ask_price = spot_config.best_ask_price
        assert ask_price is not None
        trade_price = float(ask_price)
        logger.info(f"Using external ask liquidity at ${trade_price:.2f}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(ask_price)).ioc().build()
        expected_asset = "ETH"  # Taker buys ETH, spends RUSD

        # Taker executes IOC buy
        logger.info(f"Taker placing IOC buy: {spot_config.min_qty} @ ${trade_price:.2f}")
        await taker_tester.orders.create_limit(taker_params)
    else:
        # No external liquidity - maker places order, taker fills
        trade_price = spot_config.price(0.97)
        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
        logger.info(f"Maker placing GTC buy: {spot_config.min_qty} @ ${trade_price:.2f}")
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)
        logger.info(f"✅ Maker order created: {maker_order_id}")
        taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()
        expected_asset = "RUSD"  # Taker sells ETH, receives RUSD

        # Taker executes IOC sell
        logger.info(f"Taker placing IOC sell: {spot_config.min_qty} @ ${trade_price:.2f}")
        await taker_tester.orders.create_limit(taker_params)

    # Wait for balance updates via WebSocket
    await taker_tester.wait.for_balance_updates(taker_initial_count, min_updates=1, timeout=5.0)

    # Verify taker received balance updates
    taker_tester.check.ws_balance_updates_received(
        initial_update_count=taker_initial_count,
        min_updates=1,
        expected_assets=[expected_asset],
    )

    # Verify no open orders
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ SPOT WS BALANCE UPDATES TEST COMPLETED")
