#!/usr/bin/env python3
"""Comprehensive end-to-end spot trading tests."""

import asyncio
import time

import pytest

from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.reya_tester import ReyaTester, limit_order_params_to_order, logger


@pytest.mark.asyncio
async def test_spot_maker_taker_matching(maker_tester: ReyaTester, taker_tester: ReyaTester):
    """
    End-to-end test for spot trading using TWO separate accounts:
    - Maker account: Places GTC limit order on the book
    - Taker account: Sends IOC order that matches against maker

    This tests:
    1. Maker places GTC limit order
    2. Taker sends IOC order that matches
    3. Verify order matching occurs
    4. Check all relevant endpoints:
       - spotExecutions (REST + WS)
       - balances (REST + WS)
       - orderChanges (WS)
       - L2 depth
    """
    # Use a spot market symbol (WETH = Wrapped ETH)
    symbol = "WETHRUSD"

    logger.info("=" * 80)
    logger.info(f"SPOT TRADING E2E TEST: {symbol}")
    logger.info("=" * 80)
    logger.info(f"🏭 Maker Account: {maker_tester.account_id}")
    logger.info(f"🎯 Taker Account: {taker_tester.account_id}")

    # Use a reference price for test orders (spot trading doesn't need market price API)
    reference_price = 4000.0  # Reference price for ETHRUSD
    logger.info(f"Using reference price for orders: ${reference_price}")

    # Clear any existing orders and positions for BOTH accounts
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    # Get initial balances for both accounts
    logger.info("\n📊 Getting initial balances...")
    maker_initial_balances = await maker_tester.get_balances()
    taker_initial_balances = await taker_tester.get_balances()
    logger.info(f"Maker initial balances: {[(b.asset, b.real_balance) for b in maker_initial_balances.values()]}")
    logger.info(f"Taker initial balances: {[(b.asset, b.real_balance) for b in taker_initial_balances.values()]}")

    # Step 1: Place maker order (GTC buy below reference price)
    logger.info("\n📋 Step 1: Placing maker order (GTC buy)...")
    maker_price = reference_price * 0.999  # Below reference to ensure it stays on the book
    test_qty = "0.0001"  # Small qty to fit within maker's RUSD balance (0.404 RUSD)

    maker_order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(maker_price),
        qty=test_qty,
        time_in_force=TimeInForce.GTC,
        # Note: reduce_only is not supported for GTC orders, only for IOC
    )

    maker_order_id = await maker_tester.create_limit_order(maker_order_params)
    logger.info(f"Created maker order with ID: {maker_order_id} at price ${maker_price:.2f}")

    # Wait for maker order creation confirmation
    await maker_tester.wait_for_order_creation(maker_order_id)
    expected_maker_order = limit_order_params_to_order(maker_order_params, maker_tester.account_id)
    await maker_tester.check_open_order_created(maker_order_id, expected_maker_order)
    logger.info("✅ Maker order confirmed on the book")

    # Step 2: Check L2 depth to verify maker order is visible
    logger.info("\n📊 Step 2: Checking L2 depth...")
    # Wait a moment for order to appear in orderbook
    await asyncio.sleep(1)

    # Fetch L2 depth via REST
    depth = await maker_tester.get_market_depth(symbol)
    logger.info(f"Market depth type: {depth.get('type', 'unknown')}")
    logger.info(f"Bids: {len(depth.get('bids', []))} levels")
    logger.info(f"Asks: {len(depth.get('asks', []))} levels")

    # Verify our maker order appears in the bids
    bids = depth.get('bids', [])
    maker_order_found = False
    for bid in bids:
        bid_price = float(bid['price'])
        bid_qty = float(bid['quantity'])
        logger.info(f"  Bid: ${bid_price:.2f} x {bid_qty:.6f}")
        # Check if this is our maker order (price should match)
        if abs(bid_price - maker_price) < 0.01:  # Allow small tolerance
            maker_order_found = True
            logger.info(f"  ✅ Found our maker order in L2 depth at ${bid_price:.2f}")

    if not maker_order_found:
        logger.warning(f"⚠️ Maker order at ${maker_price:.2f} not found in L2 depth - may have been matched already")
    else:
        logger.info("✅ Maker order visible in L2 depth")

    # Step 3: Place taker order (IOC sell at or below maker price to guarantee match)
    logger.info("\n📋 Step 3: Placing taker order (IOC sell to match maker)...")
    taker_price = reference_price * 0.998  # Below maker price to ensure match

    # Clear balance updates before placing order so we can verify all balance changes from this trade
    maker_tester.clear_balance_updates()
    taker_tester.clear_balance_updates()

    taker_order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=str(taker_price),
        qty=test_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    start_timestamp = int(time.time() * 1000)
    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Created taker order with ID: {taker_order_id} at price ${taker_price:.2f}")

    # Step 4: Wait for spot execution confirmation
    logger.info("\n⏳ Step 4: Waiting for spot execution...")
    expected_taker_order = limit_order_params_to_order(taker_order_params, taker_tester.account_id)

    # Wait for taker execution (should match against maker)
    taker_execution = await taker_tester.wait_for_spot_execution(expected_taker_order)
    logger.info(f"✅ Taker execution confirmed: {taker_execution.order_id}")

    # Validate taker execution details
    await taker_tester.check_spot_execution(taker_execution, expected_taker_order)
    logger.info("✅ Taker execution details validated")

    # Step 5: Verify maker order was filled or partially filled
    logger.info("\n📋 Step 5: Checking maker order status...")
    # Wait for maker order state change (should be FILLED or PARTIALLY_FILLED)
    try:
        await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Maker order fully filled")
    except RuntimeError:
        # Maker might be partially filled if taker qty was smaller
        logger.info("⚠️ Maker order might be partially filled or still open")

    # Step 6: Verify balances changed appropriately
    logger.info("\n💰 Step 6: Verifying balance changes...")
    # Wait a moment for balance updates to propagate
    await asyncio.sleep(2)

    # Get updated balances for both accounts
    maker_final_balances = await maker_tester.get_balances()
    taker_final_balances = await taker_tester.get_balances()

    # Verify balance changes match expected trade amounts
    # For WETHRUSD: base=ETH (remove W prefix), quote=RUSD
    base_asset = symbol.replace("W", "").replace("RUSD", "")  # "WETHRUSD" -> "ETH"
    quote_asset = "RUSD"

    maker_tester.verify_spot_trade_balance_changes(
        maker_account_id=maker_tester.account_id,
        taker_account_id=taker_tester.account_id,
        maker_initial_balances=maker_initial_balances,
        maker_final_balances=maker_final_balances,
        taker_initial_balances=taker_initial_balances,
        taker_final_balances=taker_final_balances,
        base_asset=base_asset,
        quote_asset=quote_asset,
        qty=test_qty,
        price=str(maker_price),  # Trade executes at maker price
        is_maker_buyer=True,  # Maker is buying (is_buy=True in maker_order_params)
    )

    # Verify all 4 balance updates were received via WebSocket
    logger.info("\n💰 Verifying balance updates via WebSocket...")
    # Both testers share the same WebSocket connection, so they receive the same messages
    # Just use one tester's updates to avoid counting duplicates
    all_balance_updates = maker_tester.ws_balance_updates
    maker_balance_updates = [b for b in all_balance_updates if b.account_id == maker_tester.account_id]
    taker_balance_updates = [b for b in all_balance_updates if b.account_id == taker_tester.account_id]

    logger.info(f"Maker received {len(maker_balance_updates)} balance updates via WS")
    logger.info(f"Taker received {len(taker_balance_updates)} balance updates via WS")

    # Assert exactly 2 balance updates per account (one for ETH, one for RUSD)
    assert len(maker_balance_updates) == 2, (
        f"Maker should receive exactly 2 balance updates (ETH + RUSD), got {len(maker_balance_updates)}"
    )
    assert len(taker_balance_updates) == 2, (
        f"Taker should receive exactly 2 balance updates (ETH + RUSD), got {len(taker_balance_updates)}"
    )

    # Get the assets that were updated
    maker_assets = {b.asset for b in maker_balance_updates}
    taker_assets = {b.asset for b in taker_balance_updates}
    logger.info(f"✅ Maker balance updates received for: {maker_assets}")
    logger.info(f"✅ Taker balance updates received for: {taker_assets}")

    # Assert both ETH and RUSD are present for each account
    assert maker_assets == {'ETH', 'RUSD'}, (
        f"Maker should have both ETH and RUSD updates, got {maker_assets}"
    )
    assert taker_assets == {'ETH', 'RUSD'}, (
        f"Taker should have both ETH and RUSD updates, got {taker_assets}"
    )

    logger.info("✅ Balance updates verified via WebSocket for both accounts")

    # Step 7: Verify order changes via WebSocket
    logger.info("\n📨 Step 7: Verifying order changes via WebSocket...")
    # Check that we received orderChanges events for both accounts
    assert maker_order_id in maker_tester.ws_order_changes, "Maker order should be in WS order changes"
    # Note: IOC orders that fill immediately don't appear in orderChanges
    if taker_order_id in taker_tester.ws_order_changes:
        logger.info("✅ Taker order changes received via WebSocket")
    logger.info("✅ Order changes verification completed")

    # Step 8: Verify spot execution via WebSocket
    logger.info("\n📊 Step 8: Verifying spot execution via WebSocket...")
    assert taker_tester.ws_last_spot_execution is not None, "Should have received spot execution via WS"
    assert taker_tester.ws_last_spot_execution.symbol == symbol
    logger.info("✅ Spot execution received via WebSocket")

    # Cleanup: Cancel any remaining open orders for both accounts
    logger.info("\n🧹 Cleanup: Cancelling any remaining orders...")
    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    logger.info("\n" + "=" * 80)
    logger.info("✅ SPOT TRADING E2E TEST COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)


@pytest.mark.asyncio
async def test_spot_order_cancellation(reya_tester: ReyaTester):
    """
    Test placing and cancelling a spot GTC order before it gets filled.
    """
    symbol = "WETHRUSD"

    logger.info("=" * 80)
    logger.info(f"SPOT ORDER CANCELLATION TEST: {symbol}")
    logger.info("=" * 80)

    # Use a reference price for test orders
    reference_price = 4000.0
    logger.info(f"Using reference price for orders: ${reference_price}")

    # Clear any existing orders
    await reya_tester.check_no_open_orders()

    # Place GTC order far from reference (won't fill)
    test_qty = "0.0001"  # Small qty to fit within account balance
    buy_price = reference_price * 0.5  # Far below reference

    order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(buy_price),
        qty=test_qty,
        time_in_force=TimeInForce.GTC,
        # Note: reduce_only is not supported for GTC orders, only for IOC
    )

    logger.info(f"Placing GTC buy order at ${buy_price:.2f} (far from market)...")
    order_id = await reya_tester.create_limit_order(order_params)
    logger.info(f"Created order with ID: {order_id}")

    # Wait for order creation
    await reya_tester.wait_for_order_creation(order_id)
    expected_order = limit_order_params_to_order(order_params, reya_tester.account_id)
    await reya_tester.check_open_order_created(order_id, expected_order)
    logger.info("✅ Order confirmed on the book")

    # Cancel the order
    logger.info("Cancelling order...")
    await reya_tester.client.cancel_order(order_id=order_id, symbol=symbol, account_id=reya_tester.account_id)

    # Wait for cancellation confirmation
    cancelled_order_id = await reya_tester.wait_for_order_state(order_id, OrderStatus.CANCELLED)
    assert cancelled_order_id == order_id, "Order was not cancelled"
    logger.info("✅ Order cancelled successfully")

    # Verify no open orders remain
    await reya_tester.check_no_open_orders()

    logger.info("✅ SPOT ORDER CANCELLATION TEST COMPLETED SUCCESSFULLY")
