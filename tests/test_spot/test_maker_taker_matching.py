"""
End-to-end test for spot maker-taker matching.

This test uses TWO separate accounts to verify the complete spot trading flow:
- Maker account: Places GTC limit order on the book
- Taker account: Sends IOC order that matches against maker
"""

import asyncio

import pytest

from sdk.async_api.depth import Depth
from sdk.open_api.models.order_status import OrderStatus
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import limit_order_params_to_order, logger
from tests.test_spot.spot_config import SpotTestConfig


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.e2e
@pytest.mark.asyncio
async def test_spot_maker_taker_matching(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    End-to-end test for spot trading using TWO separate accounts.

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
    logger.info("=" * 80)
    logger.info(f"SPOT TRADING E2E TEST: {spot_config.symbol}")
    logger.info("=" * 80)
    logger.info(f"🏭 Maker Account: {maker_tester.account_id}")
    logger.info(f"🎯 Taker Account: {taker_tester.account_id}")
    logger.info(f"Using oracle price for orders: ${spot_config.oracle_price:.2f}")

    # Clear any existing orders for BOTH accounts
    await maker_tester.check_no_open_orders()
    await taker_tester.check_no_open_orders()

    # Get initial balances for both accounts
    logger.info("\n📊 Getting initial balances...")
    maker_initial_balances = await maker_tester.get_balances()
    taker_initial_balances = await taker_tester.get_balances()
    logger.info(f"Maker initial balances: {[(b.asset, b.real_balance) for b in maker_initial_balances.values()]}")
    logger.info(f"Taker initial balances: {[(b.asset, b.real_balance) for b in taker_initial_balances.values()]}")

    # Step 1: Place maker order (GTC buy within oracle deviation)
    logger.info("\n📋 Step 1: Placing maker order (GTC buy)...")
    maker_price = spot_config.price(0.99)

    maker_order_params = OrderBuilder.from_config(spot_config).buy().at_price(0.99).gtc().build()

    maker_order_id = await maker_tester.create_limit_order(maker_order_params)
    logger.info(f"Created maker order with ID: {maker_order_id} at price ${maker_price:.2f}")

    # Wait for maker order creation confirmation
    await maker_tester.wait_for_order_creation(maker_order_id)
    expected_maker_order = limit_order_params_to_order(maker_order_params, maker_tester.account_id)
    await maker_tester.check_open_order_created(maker_order_id, expected_maker_order)
    logger.info("✅ Maker order confirmed on the book")

    # Step 2: Check L2 depth to verify maker order is visible
    logger.info("\n📊 Step 2: Checking L2 depth...")
    await asyncio.sleep(0.05)

    depth = await maker_tester.get_market_depth(spot_config.symbol)
    assert isinstance(depth, Depth), f"Expected Depth type, got {type(depth)}"
    logger.info(f"Market depth type: {depth.type}")
    logger.info(f"Bids: {len(depth.bids)} levels")
    logger.info(f"Asks: {len(depth.asks)} levels")

    # Verify our maker order appears in the bids (using typed Level.px/qty attributes)
    bids = depth.bids
    maker_order_found = False
    for bid in bids:
        bid_price = float(bid.px)
        bid_qty = float(bid.qty)
        logger.info(f"  Bid: ${bid_price:.2f} x {bid_qty:.6f}")
        if abs(bid_price - maker_price) < 0.01:
            maker_order_found = True
            logger.info(f"  ✅ Found our maker order in L2 depth at ${bid_price:.2f}")

    if not maker_order_found:
        logger.warning(f"⚠️ Maker order at ${maker_price:.2f} not found in L2 depth - may have been matched already")
    else:
        logger.info("✅ Maker order visible in L2 depth")

    # Step 3: Place taker order (IOC sell at maker price to guarantee match)
    logger.info("\n📋 Step 3: Placing taker order (IOC sell to match maker)...")
    taker_price = maker_price  # Same price ensures within oracle deviation

    # Clear balance updates before placing order
    maker_tester.clear_balance_updates()
    taker_tester.clear_balance_updates()

    taker_order_params = OrderBuilder.from_config(spot_config).sell().at_price(0.99).ioc().build()

    taker_order_id = await taker_tester.create_limit_order(taker_order_params)
    logger.info(f"Created taker order with ID: {taker_order_id} at price ${taker_price:.2f}")

    # Step 4: Wait for spot execution confirmation
    logger.info("\n⏳ Step 4: Waiting for spot execution...")
    expected_taker_order = limit_order_params_to_order(taker_order_params, taker_tester.account_id)

    # Strict matching on order_id and all fields
    taker_execution = await taker_tester.wait_for_spot_execution(taker_order_id, expected_taker_order)
    logger.info(f"✅ Taker execution confirmed: {taker_execution.order_id}")

    await taker_tester.check_spot_execution(taker_execution, expected_taker_order)
    logger.info("✅ Taker execution details validated")

    # Step 5: Verify maker order was filled
    logger.info("\n📋 Step 5: Checking maker order status...")
    try:
        await maker_tester.wait_for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info("✅ Maker order fully filled")
    except RuntimeError:
        logger.info("⚠️ Maker order might be partially filled or still open")

    # Step 6: Verify balances changed appropriately
    logger.info("\n💰 Step 6: Verifying balance changes...")
    await asyncio.sleep(0.1)

    maker_final_balances = await maker_tester.get_balances()
    taker_final_balances = await taker_tester.get_balances()

    # For WETHRUSD: base=ETH, quote=RUSD
    base_asset = spot_config.symbol.replace("W", "").replace("RUSD", "")  # "WETHRUSD" -> "ETH"
    quote_asset = "RUSD"

    maker_tester.verify_spot_trade_balance_changes(
        maker_initial_balances=maker_initial_balances,
        maker_final_balances=maker_final_balances,
        taker_initial_balances=taker_initial_balances,
        taker_final_balances=taker_final_balances,
        base_asset=base_asset,
        quote_asset=quote_asset,
        qty=spot_config.min_qty,
        price=str(maker_price),
        is_maker_buyer=True,
    )

    # Verify balance updates via WebSocket
    # Each tester has its own WebSocket connection subscribed to its own wallet
    logger.info("\n💰 Verifying balance updates via WebSocket...")
    maker_balance_updates = [b for b in maker_tester.ws_balance_updates if b.account_id == maker_tester.account_id]
    taker_balance_updates = [b for b in taker_tester.ws_balance_updates if b.account_id == taker_tester.account_id]

    logger.info(f"Maker received {len(maker_balance_updates)} balance updates via WS")
    logger.info(f"Taker received {len(taker_balance_updates)} balance updates via WS")

    assert (
        len(maker_balance_updates) == 2
    ), f"Maker should receive exactly 2 balance updates (ETH + RUSD), got {len(maker_balance_updates)}"
    assert (
        len(taker_balance_updates) == 2
    ), f"Taker should receive exactly 2 balance updates (ETH + RUSD), got {len(taker_balance_updates)}"

    maker_assets = {b.asset for b in maker_balance_updates}
    taker_assets = {b.asset for b in taker_balance_updates}
    logger.info(f"✅ Maker balance updates received for: {maker_assets}")
    logger.info(f"✅ Taker balance updates received for: {taker_assets}")

    assert maker_assets == {"ETH", "RUSD"}, f"Maker should have both ETH and RUSD updates, got {maker_assets}"
    assert taker_assets == {"ETH", "RUSD"}, f"Taker should have both ETH and RUSD updates, got {taker_assets}"

    logger.info("✅ Balance updates verified via WebSocket for both accounts")

    # Step 7: Verify order changes via WebSocket
    logger.info("\n📨 Step 7: Verifying order changes via WebSocket...")
    assert maker_order_id in maker_tester.ws_order_changes, "Maker order should be in WS order changes"
    if taker_order_id in taker_tester.ws_order_changes:
        logger.info("✅ Taker order changes received via WebSocket")
    logger.info("✅ Order changes verification completed")

    # Step 8: Verify spot execution via WebSocket
    logger.info("\n📊 Step 8: Verifying spot execution via WebSocket...")
    assert taker_tester.ws_last_spot_execution is not None, "Should have received spot execution via WS"
    assert taker_tester.ws_last_spot_execution.symbol == spot_config.symbol
    logger.info("✅ Spot execution received via WebSocket")

    # Cleanup
    logger.info("\n🧹 Cleanup: Cancelling any remaining orders...")
    await maker_tester.close_active_orders(fail_if_none=False)
    await taker_tester.close_active_orders(fail_if_none=False)

    logger.info("\n%s", "=" * 80)
    logger.info("✅ SPOT TRADING E2E TEST COMPLETED SUCCESSFULLY")
    logger.info("=" * 80)
