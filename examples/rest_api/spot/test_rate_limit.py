"""
Spot rate limit system verification tests.

Each test function verifies a specific aspect of the rate limiting system.
Comment in/out the function calls in main() to run the tests you want.

Usage:
    python examples/rest_api/spot/test_rate_limit.py
"""

import asyncio
import logging
import time

from dotenv import load_dotenv

from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient, get_spot_config
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.open_api.exceptions import ApiException

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

SYMBOL = "WETHRUSD"
ORDER_QTY = "0.001"
SAFE_NO_MATCH_BUY_PRICE = "1"  # $1 — won't match any asks
SAFE_NO_MATCH_SELL_PRICE = "100000"  # $100k — won't match any bids


async def create_client(account_number: int = 1) -> ReyaTradingClient:
    config = get_spot_config(account_number=account_number)
    client = ReyaTradingClient(config)
    await client.start()
    logger.info(f"Client ready — wallet: {config.owner_wallet_address}, account: {config.account_id}")
    return client


# =============================================================================
# TEST A: Sliding window rate limit (IOC orders)
# =============================================================================


async def test_a_sliding_window_rate_limit():
    """
    Submits IOC orders quickly to hit the rate limit (5/min fallback).
    Uses IOC to avoid interfering with GTC open order caps.
    """
    logger.info("=" * 60)
    logger.info("TEST A: Sliding window rate limit")
    logger.info("=" * 60)

    client = await create_client(account_number=1)
    num_orders = 8

    logger.info(f"Sending {num_orders} IOC orders (limit should be ~5/min)...")

    accepted = 0
    rejected = 0
    rejection_messages = []

    for i in range(1, num_orders + 1):
        params = LimitOrderParameters(
            symbol=SYMBOL,
            is_buy=True,
            qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE,
            time_in_force=TimeInForce.IOC,
        )
        try:
            response = await client.create_limit_order(params)
            accepted += 1
            order_id = response.order_id if response else "N/A"
            logger.info(f"  Order {i}: ACCEPTED (order_id={order_id})")
        except (ApiException, Exception) as e:
            rejected += 1
            msg = str(e)
            rejection_messages.append(msg)
            logger.info(f"  Order {i}: REJECTED — {msg}")

    logger.info("")
    logger.info(f"Results: {accepted} accepted, {rejected} rejected out of {num_orders}")

    if rejected > 0 and any("rate limit" in m.lower() for m in rejection_messages):
        logger.info("SUCCESS: Rate limiting is working")
    elif rejected == 0:
        logger.info("WARNING: No rejections — rate limit may not be active or limit is higher than expected")

    # Wait for window reset and verify
    logger.info("")
    logger.info("Waiting 65s for rate limit window to reset...")
    await asyncio.sleep(65)

    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.IOC,
    )
    try:
        await client.create_limit_order(params)
        logger.info("SUCCESS: Order accepted after window reset")
    except ApiException as e:
        logger.info(f"FAIL: Still rejected after reset — {e}")

    await client.close()


# =============================================================================
# TEST B: Open order count cap (GTC orders)
# =============================================================================


async def test_b_open_order_count_cap():
    """
    Submits GTC orders to hit the open order count cap (3 for regular wallet).
    Uses far-from-market prices so orders rest in the book.
    """
    logger.info("=" * 60)
    logger.info("TEST B: Open order count cap (regular wallet, cap=3)")
    logger.info("=" * 60)

    config = get_spot_config(account_number=1)
    client = ReyaTradingClient(config)
    await client.start()

    # First, clean up any existing orders
    logger.info("Cleaning up existing orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} existing orders")
    except Exception:
        logger.info("  No existing orders to cancel")

    logger.info("Waiting 65s for rate limit window to reset after cleanup...")
    await asyncio.sleep(65)

    expires_after = int(time.time()) + 300  # 5 min expiry

    # Place 3 GTC orders — all should succeed
    logger.info("Placing 3 GTC orders (should all succeed)...")
    order_ids = []
    for i in range(1, 4):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            order_ids.append(response.order_id)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: REJECTED — {e}")

    # Place 4th GTC order — should be rejected by count cap
    logger.info("")
    logger.info("Placing 4th GTC order (should be rejected by count cap)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        await client.create_limit_order(params)
        logger.info("  FAIL: 4th order was accepted (expected rejection)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "order count cap" in msg.lower():
            logger.info(f"  SUCCESS: Rejected with count cap message — {msg}")
        else:
            logger.info(f"  REJECTED but unexpected message — {msg}")

    # Cancel one order, wait for rate limit window to reset, then place again
    if order_ids:
        logger.info("")
        logger.info(f"Cancelling order {order_ids[0]} to free up a slot...")
        try:
            await client.cancel_order(
                order_id=order_ids[0], symbol=SYMBOL, account_id=config.account_id,
            )
            logger.info("  Order cancelled")
        except (ApiException, Exception) as e:
            logger.info(f"  Cancel failed — {e}")

        # We used 5/5 rate limit budget (3 creates + 1 rejected create + 1 cancel)
        # Need to wait for the window to reset before we can submit again
        logger.info("Waiting 65s for rate limit window to reset...")
        await asyncio.sleep(65)

        logger.info("Placing new GTC order after cancel (should succeed — 2 resting, cap is 3)...")
        expires_after = int(time.time()) + 300
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            logger.info(f"  SUCCESS: Order accepted (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  FAIL: Still rejected — {e}")

    # Cleanup
    logger.info("")
    logger.info("Cleaning up remaining orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} orders")
    except Exception:
        pass

    await client.close()


# =============================================================================
# TEST C: Rate limit shared across operation types (creates + cancels)
# =============================================================================


async def test_c_rate_limit_shared_across_operations():
    """
    Proves that creates and cancels share the same sliding window budget (5/min).
    Steps:
      1. Place 2 GTC orders           (2/5 rate limit, 2/3 cap)
      2. Cancel 1 order               (3/5 rate limit, 1/3 cap)
      3. Place 2 more GTC orders      (5/5 rate limit, 3/3 cap)
      4. Try another create  → rejected by rate limit (5/5)
      5. Try a cancel        → rejected by rate limit (5/5)
    """
    logger.info("=" * 60)
    logger.info("TEST C: Rate limit shared across creates + cancels")
    logger.info("=" * 60)

    config = get_spot_config(account_number=1)
    client = ReyaTradingClient(config)
    await client.start()

    # Clean up
    logger.info("Cleaning up existing orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} existing orders")
    except Exception:
        logger.info("  No existing orders to cancel")

    logger.info("Waiting 65s for rate limit window to reset...")
    await asyncio.sleep(65)

    expires_after = int(time.time()) + 300
    order_ids = []

    # Step 1: Place 2 GTC orders (2/5 rate limit, 2/3 cap)
    logger.info("")
    logger.info("Step 1: Placing 2 GTC orders (2/5 rate limit, 2/3 cap)...")
    for i in range(1, 3):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            order_ids.append(response.order_id)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: UNEXPECTED REJECTION — {e}")

    # Step 2: Cancel 1 order (3/5 rate limit, 1/3 cap)
    logger.info("")
    logger.info(f"Step 2: Cancelling order {order_ids[0]} (3/5 rate limit, 1/3 cap)...")
    try:
        await client.cancel_order(
            order_id=order_ids[0], symbol=SYMBOL, account_id=config.account_id,
        )
        logger.info("  Cancel: ACCEPTED")
    except (ApiException, Exception) as e:
        logger.info(f"  Cancel: UNEXPECTED REJECTION — {e}")

    # Step 3: Place 2 more GTC orders (5/5 rate limit, 3/3 cap)
    logger.info("")
    logger.info("Step 3: Placing 2 more GTC orders (5/5 rate limit, 3/3 cap)...")
    for i in range(3, 5):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            order_ids.append(response.order_id)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: UNEXPECTED REJECTION — {e}")

    # Step 4: Try another create → should be rejected by rate limit (5/5)
    logger.info("")
    logger.info("Step 4: Placing another order (should be rejected by rate limit 5/5)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        await client.create_limit_order(params)
        logger.info("  FAIL: Order was accepted (expected rate limit rejection)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  SUCCESS: Rejected by rate limit")
        else:
            logger.info(f"  REJECTED but unexpected reason — {msg}")

    # Step 5: Try a cancel → should also be rejected by rate limit (5/5)
    logger.info("")
    logger.info("Step 5: Trying a cancel (should also be rejected by rate limit 5/5)...")
    try:
        await client.cancel_order(
            order_id=order_ids[1], symbol=SYMBOL, account_id=config.account_id,
        )
        logger.info("  FAIL: Cancel was accepted (expected rate limit rejection)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  SUCCESS: Cancel also rejected by rate limit")
        else:
            logger.info(f"  REJECTED but unexpected reason — {msg}")

    # Cleanup — wait for window reset first since we're at 5/5
    logger.info("")
    logger.info("Waiting 65s for rate limit window to reset before cleanup...")
    await asyncio.sleep(65)
    logger.info("Cleaning up remaining orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} orders")
    except Exception:
        pass

    await client.close()


# =============================================================================
# TEST D: Mass cancel separate bucket
# =============================================================================


async def test_d_mass_cancel_separate_bucket():
    """
    Proves mass cancels have their own independent rate limit bucket (2/min).
    Uses whitelisted wallet (account 2) for higher order headroom.

    Steps:
      1. Place 2 GTC orders                             (2/5 order, 0/2 mass cancel)
      2. Mass cancel #1 → succeeds                      (2/5 order, 1/2 mass cancel)
      3. Place 2 GTC orders                              (4/5 order, 1/2 mass cancel)
      4. Mass cancel #2 → succeeds                      (4/5 order, 2/2 mass cancel)
      5. Mass cancel #3 → rejected (mass cancel budget exhausted)
      6. Place an IOC order → succeeds (order budget at 5/5, still separate from mass cancel)
      7. Place another IOC → rejected (order budget now at 5/5)
    """
    logger.info("=" * 60)
    logger.info("TEST D: Mass cancel separate bucket (limit=2/min)")
    logger.info("=" * 60)

    # Use whitelisted wallet (account 2) — higher order cap (5) gives more headroom
    config = get_spot_config(account_number=2)
    client = ReyaTradingClient(config)
    await client.start()

    # Clean up
    logger.info("Cleaning up existing orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} existing orders")
    except Exception:
        logger.info("  No existing orders to cancel")

    logger.info("Waiting 65s for both rate limit windows to reset...")
    await asyncio.sleep(65)

    expires_after = int(time.time()) + 300

    # Step 1: Place 2 GTC orders
    logger.info("")
    logger.info("Step 1: Placing 2 GTC orders...")
    for i in range(1, 3):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: REJECTED — {e}")

    # Step 2: Mass cancel #1 (1/2 mass cancel budget)
    logger.info("")
    logger.info("Step 2: Mass cancel #1 (1/2 mass cancel budget)...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  SUCCESS: Cancelled {result.cancelled_count} orders")
    except (ApiException, Exception) as e:
        logger.info(f"  UNEXPECTED REJECTION — {e}")

    await asyncio.sleep(1)

    # Step 3: Place 2 GTC orders
    logger.info("")
    logger.info("Step 3: Placing 2 GTC orders...")
    expires_after = int(time.time()) + 300
    for i in range(1, 3):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: REJECTED — {e}")

    # Step 4: Mass cancel #2 (2/2 mass cancel budget)
    logger.info("")
    logger.info("Step 4: Mass cancel #2 (2/2 mass cancel budget)...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  SUCCESS: Cancelled {result.cancelled_count} orders")
    except (ApiException, Exception) as e:
        logger.info(f"  UNEXPECTED REJECTION — {e}")

    # Step 5: Mass cancel #3 → should be rejected (2/2 used)
    logger.info("")
    logger.info("Step 5: Mass cancel #3 (should be rejected — 2/2 mass cancel budget used)...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  FAIL: Mass cancel accepted (expected rejection) — cancelled {result.cancelled_count}")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  SUCCESS: Mass cancel rejected by rate limit")
        else:
            logger.info(f"  REJECTED but unexpected reason — {msg}")

    # Step 6: Verify single order create still works (order budget is separate)
    logger.info("")
    logger.info("Step 6: Placing an IOC order (should succeed — order budget at 5/5 after this)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.IOC,
    )
    try:
        response = await client.create_limit_order(params)
        logger.info(f"  SUCCESS: IOC accepted (proves order bucket is independent of mass cancel)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  FAIL: IOC rejected by rate limit (order budget unexpectedly exhausted)")
        else:
            logger.info(f"  REJECTED — {msg}")

    # Step 7: One more IOC → should be rejected (order budget now at 5/5)
    logger.info("")
    logger.info("Step 7: Placing another IOC (should be rejected — order budget at 5/5)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.IOC,
    )
    try:
        await client.create_limit_order(params)
        logger.info(f"  FAIL: IOC accepted (expected order rate limit rejection)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  SUCCESS: IOC rejected by order rate limit (5/5 used)")
        else:
            logger.info(f"  REJECTED but unexpected reason — {msg}")

    await client.close()


# =============================================================================
# TEST E: Whitelisted tier has higher cap
# =============================================================================


async def test_e_whitelisted_higher_cap():
    """
    Proves the tier system works by testing both wallets side by side:
      - Wallet 1 (regular, cap=3): gets rejected at 4th order
      - Wallet 2 (whitelisted, cap=5): can place 5 orders, rejected at 6th

    Steps:
      1. Clean up both wallets
      2. Wallet 1: place 3 GTC → all succeed, 4th rejected at (3/3)
      3. Wallet 2: place 5 GTC → all succeed, 6th rejected at (5/5)
    """
    logger.info("=" * 60)
    logger.info("TEST E: Tiered caps — regular (3) vs whitelisted (5)")
    logger.info("=" * 60)

    config1 = get_spot_config(account_number=1)
    client1 = ReyaTradingClient(config1)
    await client1.start()

    config2 = get_spot_config(account_number=2)
    client2 = ReyaTradingClient(config2)
    await client2.start()

    # Clean up both wallets
    logger.info("Cleaning up existing orders for both wallets...")
    for name, client, config in [("Wallet 1 (regular)", client1, config1), ("Wallet 2 (whitelisted)", client2, config2)]:
        try:
            result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
            logger.info(f"  {name}: cancelled {result.cancelled_count} orders")
        except Exception:
            logger.info(f"  {name}: no existing orders")

    logger.info("Waiting 65s for rate limit windows to reset...")
    await asyncio.sleep(65)

    # --- Wallet 1 (regular, cap=3) ---
    logger.info("")
    logger.info("-" * 60)
    logger.info("Wallet 1 (regular) — cap should be 3")
    logger.info("-" * 60)

    expires_after = int(time.time()) + 300

    logger.info("Placing 3 GTC orders (should all succeed)...")
    for i in range(1, 4):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client1.create_limit_order(params)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: REJECTED — {e}")

    logger.info("Placing 4th GTC order (should be rejected at 3/3)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        await client1.create_limit_order(params)
        logger.info("  FAIL: 4th order accepted (expected rejection at 3/3)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "order count cap" in msg.lower() and "3/3" in msg:
            logger.info(f"  SUCCESS: Rejected at (3/3) — regular tier cap working")
        else:
            logger.info(f"  REJECTED — {msg}")

    # --- Wallet 2 (whitelisted, cap=5) ---
    logger.info("")
    logger.info("-" * 60)
    logger.info("Wallet 2 (whitelisted) — cap should be 5")
    logger.info("-" * 60)

    expires_after = int(time.time()) + 300

    logger.info("Placing 5 GTC orders (should all succeed — whitelisted cap is 5)...")
    for i in range(1, 6):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client2.create_limit_order(params)
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: REJECTED — {e}")

    logger.info("Placing 6th GTC order (should be rejected at 5/5)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        await client2.create_limit_order(params)
        logger.info("  FAIL: 6th order accepted (expected rejection at 5/5)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "order count cap" in msg.lower() and "5/5" in msg:
            logger.info(f"  SUCCESS: Rejected at (5/5) — whitelisted tier cap working")
        else:
            logger.info(f"  REJECTED — {msg}")

    # Cleanup both
    logger.info("")
    logger.info("Waiting 65s for rate limit windows to reset before cleanup...")
    await asyncio.sleep(65)
    logger.info("Cleaning up remaining orders...")
    for name, client, config in [("Wallet 1", client1, config1), ("Wallet 2", client2, config2)]:
        try:
            result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
            logger.info(f"  {name}: cancelled {result.cancelled_count} orders")
        except Exception:
            pass

    await client1.close()
    await client2.close()


# =============================================================================
# TEST F: Open notional cap
# =============================================================================


async def test_f_open_notional_cap():
    """
    Proves the open notional cap works. Regular wallet cap is $3k.
    Notional = limitPx × remainingQty (both in E9 format internally).

    Steps:
      1. Place GTC buy at $1 for qty 2000 → notional = $2000 → succeeds
      2. Place GTC buy at $1 for qty 1500 → total would be $3500 > $3000 → rejected
      3. Place GTC buy at $1 for qty 900 → total would be $2900 < $3000 → succeeds
    """
    logger.info("=" * 60)
    logger.info("TEST F: Open notional cap (regular wallet, $3k cap)")
    logger.info("=" * 60)

    config = get_spot_config(account_number=1)
    client = ReyaTradingClient(config)
    await client.start()

    # Clean up
    logger.info("Cleaning up existing orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} existing orders")
    except Exception:
        logger.info("  No existing orders to cancel")

    logger.info("Waiting 65s for rate limit window to reset...")
    await asyncio.sleep(65)

    expires_after = int(time.time()) + 300

    # Step 1: Place GTC buy at $1 for qty 2000 → notional = $2000 (under $3k cap)
    logger.info("")
    logger.info("Step 1: Placing GTC buy at $1 x 2000 qty (notional=$2000, under $3k cap)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty="2000",
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        response = await client.create_limit_order(params)
        logger.info(f"  ACCEPTED (order_id={response.order_id}) — $2000 notional resting")
    except (ApiException, Exception) as e:
        logger.info(f"  UNEXPECTED REJECTION — {e}")

    # Wait for OrdersProvider to process the stream event from step 1
    logger.info("  Waiting 6s for order book state to propagate (XREAD BLOCK timeout is 5s)...")
    await asyncio.sleep(6)

    # Step 2: Place GTC buy at $1 for qty 1500 → total = $3500 > $3000 → rejected
    logger.info("")
    logger.info("Step 2: Placing GTC buy at $1 x 1500 qty (total notional=$3500 > $3k cap)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty="1500",
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        await client.create_limit_order(params)
        logger.info("  FAIL: Order accepted (expected notional cap rejection)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "notional cap" in msg.lower():
            logger.info(f"  SUCCESS: Rejected by notional cap")
        else:
            logger.info(f"  REJECTED — {msg}")

    # Step 3: Place GTC buy at $1 for qty 900 → total = $2900 < $3000 → succeeds
    await asyncio.sleep(6)
    logger.info("")
    logger.info("Step 3: Placing GTC buy at $1 x 900 qty (total notional=$2900 < $3k cap)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty="900",
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        response = await client.create_limit_order(params)
        logger.info(f"  SUCCESS: Accepted (order_id={response.order_id}) — $2900 notional resting")
    except (ApiException, Exception) as e:
        logger.info(f"  FAIL: Rejected — {e}")

    # Cleanup
    logger.info("")
    logger.info("Waiting 65s for rate limit window to reset before cleanup...")
    await asyncio.sleep(65)
    logger.info("Cleaning up remaining orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} orders")
    except Exception:
        pass

    await client.close()


# =============================================================================
# TEST G: Premium wallet bypasses caps and uses premium rate limit
# =============================================================================


async def test_g_premium_wallet():
    """
    Proves that SPOT_ACCOUNT_ID_1 (configured as a premium wallet) behaves as expected:
      - All open order caps (count + notional) are bypassed
      - The premium sliding-window rate limit (7/min) is applied instead of
        the regular 3/min

    Cronos config assumed:
      - RATE_LIMIT_TRADES_MAX_PER_WALLET        = 3  (regular)
      - RATE_LIMIT_TRADES_PREMIUM_LIMIT_PER_WALLET = 7  (premium)
      - RATE_LIMIT_GTC_MAX_OPEN_ORDERS          = 3  (regular count cap)
      - RATE_LIMIT_GTC_MAX_OPEN_NOTIONAL        = $3k (regular notional cap)
      - EXECUTOR_PREMIUM_WALLETS                = SPOT_WALLET_ADDRESS_1

    Steps:
      1. Clean up existing orders
      2. Place 6 GTC orders at qty=1000, $1 price (notional=$6k each →
         total far above regular $3k cap; count far above regular 3 cap).
         All must succeed → proves both caps are bypassed for premium.
      3. Place a 7th GTC order → should still succeed (premium limit = 7, so
         after 6 creates we have 6/7).
      4. Place an 8th GTC order → should be rejected by rate limit
         (premium limit 7/7 reached). Confirms premium's *special* limit is
         actually applied (not the default 100, not the regular 3).
      5. Wait for window reset and cleanup.
    """
    logger.info("=" * 60)
    logger.info("TEST G: Premium wallet — cap bypass + premium rate limit")
    logger.info("=" * 60)

    config = get_spot_config(account_number=1)
    client = ReyaTradingClient(config)
    await client.start()
    logger.info(f"Wallet: {config.owner_wallet_address} (expected to be premium)")

    # Clean up
    logger.info("Cleaning up existing orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} existing orders")
    except Exception:
        logger.info("  No existing orders to cancel")

    logger.info("Waiting 65s for rate limit windows to reset...")
    await asyncio.sleep(65)

    expires_after = int(time.time()) + 300

    # Step 1+2: place 6 GTC orders, each notional = $1 × 1000 = $1000
    # Totals: 6 orders (above regular count cap 3) and $6k notional
    # (above regular notional cap $3k). A regular wallet would be rejected
    # at the 4th order or once notional crosses $3k. Premium must bypass both.
    logger.info("")
    logger.info("Step 1: Placing 6 GTC orders (qty=1000 each → $6k total notional)")
    logger.info("        Regular caps: count=3, notional=$3k — premium must bypass both")
    accepted = 0
    for i in range(1, 7):
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty="1000",
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            accepted += 1
            logger.info(f"  Order {i}: ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Order {i}: REJECTED — {e}")

    if accepted == 6:
        logger.info("  SUCCESS: All 6 orders accepted — count cap + notional cap are bypassed")
    else:
        logger.info(f"  FAIL: Only {accepted}/6 accepted — premium bypass is NOT working")

    # Step 3: 7th order — still within premium rate limit (7/7)
    logger.info("")
    logger.info("Step 2: Placing 7th GTC order (premium rate limit 6/7 → 7/7)...")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty="1000",
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        response = await client.create_limit_order(params)
        logger.info(f"  SUCCESS: 7th order accepted (order_id={response.order_id})")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  FAIL: 7th order rejected by rate limit — premium limit appears lower than 7: {msg}")
        else:
            logger.info(f"  FAIL: 7th order rejected with unexpected reason — {msg}")

    # Step 4: 8th order — must be rejected by rate limit (premium 7/7 exhausted)
    logger.info("")
    logger.info("Step 3: Placing 8th GTC order (premium rate limit 7/7 → should be rejected)")
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty="1000",
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        await client.create_limit_order(params)
        logger.info("  FAIL: 8th order accepted (expected rate limit rejection — premium limit higher than 7?)")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  SUCCESS: 8th order rejected by rate limit — premium limit of 7 is enforced")
        else:
            logger.info(f"  REJECTED but unexpected reason (not rate limit) — {msg}")

    # Cleanup
    logger.info("")
    logger.info("Waiting 65s for rate limit window to reset before cleanup...")
    await asyncio.sleep(65)
    logger.info("Cleaning up remaining orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} orders")
    except Exception:
        pass

    await client.close()


# =============================================================================
# TEST H: Premium wallet mass cancel limit
# =============================================================================


async def test_h_premium_mass_cancel():
    """
    Proves that SPOT_ACCOUNT_ID_1 (premium) uses the premium mass-cancel
    rate limit (4/min), not the regular 2/min, and that the order bucket
    and mass cancel bucket remain independent for premium wallets.

    Cronos config assumed:
      - RATE_LIMIT_MASS_CANCEL_MAX_PER_WALLET             = 2   (regular)
      - RATE_LIMIT_MASS_CANCEL_PREMIUM_LIMIT_PER_WALLET   = 4   (premium)
      - RATE_LIMIT_TRADES_PREMIUM_LIMIT_PER_WALLET        = 7   (premium order bucket)
      - EXECUTOR_PREMIUM_WALLETS                          = SPOT_WALLET_ADDRESS_1

    Steps:
      1. Clean up + wait 65s for both rate limit windows to reset.
      2. Loop 4 times: place 1 GTC order, then mass cancel it.
         After the loop: orders bucket = 4/7, mass cancel bucket = 4/4.
         All 4 mass cancels must succeed → proves premium mass cancel limit is
         higher than regular 2.
      3. Attempt a 5th mass cancel → must be rejected by rate limit →
         proves premium's limit of 4 is actually enforced (not default, not regular 2).
      4. Place one more GTC order → must succeed (order bucket at 5/7), proving
         the order and mass cancel buckets remain independent for premium wallets.
      5. Cleanup after window reset.
    """
    logger.info("=" * 60)
    logger.info("TEST H: Premium mass cancel rate limit (premium=4, regular=2)")
    logger.info("=" * 60)

    config = get_spot_config(account_number=1)
    client = ReyaTradingClient(config)
    await client.start()
    logger.info(f"Wallet: {config.owner_wallet_address} (expected to be premium)")

    # Cleanup
    logger.info("Cleaning up existing orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} existing orders")
    except Exception:
        logger.info("  No existing orders to cancel")

    logger.info("Waiting 65s for both rate limit windows to reset...")
    await asyncio.sleep(65)

    # Step 1: 4 iterations of (create + mass cancel)
    logger.info("")
    logger.info("Step 1: 4 iterations of place GTC + mass cancel")
    logger.info("        (regular mass cancel limit is 2 — a regular wallet would be rejected at iteration 3)")

    mass_cancels_accepted = 0
    for i in range(1, 5):
        expires_after = int(time.time()) + 300
        params = LimitOrderParameters(
            symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
            limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
            expires_after=expires_after,
        )
        try:
            response = await client.create_limit_order(params)
            logger.info(f"  Iter {i}: Order ACCEPTED (order_id={response.order_id})")
        except (ApiException, Exception) as e:
            logger.info(f"  Iter {i}: Order UNEXPECTED REJECTION — {e}")
            continue

        # Give OrdersProvider a moment to see the new order before cancelling
        await asyncio.sleep(1)

        try:
            result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
            mass_cancels_accepted += 1
            logger.info(f"  Iter {i}: Mass cancel ACCEPTED (cancelled {result.cancelled_count})")
        except (ApiException, Exception) as e:
            msg = str(e)
            logger.info(f"  Iter {i}: Mass cancel REJECTED — {msg}")

    if mass_cancels_accepted == 4:
        logger.info("  SUCCESS: All 4 mass cancels accepted — premium mass cancel limit > regular 2")
    else:
        logger.info(f"  FAIL: Only {mass_cancels_accepted}/4 mass cancels accepted — premium limit not applied as expected")

    # Step 2: 5th mass cancel → should be rejected
    logger.info("")
    logger.info("Step 2: 5th mass cancel (mass cancel bucket at 4/4 — should be rejected)")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  FAIL: 5th mass cancel accepted — premium limit appears higher than 4 (cancelled {result.cancelled_count})")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  SUCCESS: 5th mass cancel rejected by rate limit — premium limit of 4 is enforced")
        else:
            logger.info(f"  REJECTED but unexpected reason — {msg}")

    # Step 3: independent order bucket — place another GTC order
    logger.info("")
    logger.info("Step 3: Placing one more GTC order (order bucket should still have headroom — 4/7)")
    expires_after = int(time.time()) + 300
    params = LimitOrderParameters(
        symbol=SYMBOL, is_buy=True, qty=ORDER_QTY,
        limit_px=SAFE_NO_MATCH_BUY_PRICE, time_in_force=TimeInForce.GTC,
        expires_after=expires_after,
    )
    try:
        response = await client.create_limit_order(params)
        logger.info(f"  SUCCESS: Order accepted (order_id={response.order_id}) — order and mass cancel buckets are independent")
    except (ApiException, Exception) as e:
        msg = str(e)
        if "rate limit" in msg.lower():
            logger.info(f"  FAIL: Order rejected — order bucket unexpectedly exhausted or not independent: {msg}")
        else:
            logger.info(f"  FAIL: Order rejected — {msg}")

    # Cleanup
    logger.info("")
    logger.info("Waiting 65s for rate limit windows to reset before cleanup...")
    await asyncio.sleep(65)
    logger.info("Cleaning up remaining orders...")
    try:
        result = await client.mass_cancel(symbol=SYMBOL, account_id=config.account_id)
        logger.info(f"  Cancelled {result.cancelled_count} orders")
    except Exception:
        pass

    await client.close()


# =============================================================================
# MAIN
# =============================================================================


async def main():
    load_dotenv()

    # Comment in/out to choose which tests to run:
    # await test_a_sliding_window_rate_limit()
    # await test_b_open_order_count_cap()
    # await test_c_rate_limit_shared_across_operations()
    # await test_d_mass_cancel_separate_bucket()
    # await test_e_whitelisted_higher_cap()
    # await test_f_open_notional_cap()
    # await test_g_premium_wallet()
    await test_h_premium_mass_cancel()


if __name__ == "__main__":
    asyncio.run(main())
