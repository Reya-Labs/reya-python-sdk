"""
Pytest fixtures for Reya Python SDK integration tests.

Uses pytest-asyncio's loop_scope feature (v0.24+) to share a single event loop
across all tests in a session, enabling session-scoped async fixtures.
"""

import asyncio
import os
from decimal import Decimal

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger
from tests.test_spot.spot_config import SpotTestConfig

# Time delay between tests
TEST_DELAY_SECONDS = 0.1

# Minimum balance requirements for SPOT tests
MIN_ETH_BALANCE = 0.05
MIN_RUSD_BALANCE = 15.0


@pytest_asyncio.fixture(loop_scope="session", scope="function", autouse=True)
async def rate_limit_delay():
    """
    Add a small delay after each test to avoid WAF rate limiting.

    The staging environment uses AWS WAF which can block requests if too many
    come from the same IP in a short time window. This delay helps prevent
    403 Forbidden errors during test runs.
    """
    yield
    await asyncio.sleep(TEST_DELAY_SECONDS)


# ============================================================================
# Session-Scoped Fixtures (Single connection for entire test suite)
# ============================================================================
# Using loop_scope="session" ensures all fixtures share the same event loop


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def reya_tester_session():
    """
    Session-scoped ReyaTester - ONE connection for the entire test suite.

    This dramatically reduces test time by maintaining a single WebSocket and
    API connection throughout all tests.
    """
    load_dotenv()

    tester = ReyaTester()

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip("Missing required wallet address or account ID for tests")

    logger.info("=" * 60)
    logger.info("🚀 SESSION START: Initializing single WebSocket connection")
    logger.info(f"   Wallet: {tester.owner_wallet_address}")
    logger.info(f"   Account: {tester.account_id}")
    logger.info("=" * 60)

    # setup() calls client.start() internally, no need to call it separately
    await tester.setup()

    yield tester

    # Cleanup at end of entire test session
    logger.info("=" * 60)
    logger.info("🧹 SESSION END: Closing connections")
    logger.info("=" * 60)
    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.positions.close_all(fail_if_none=False)
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during session cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def reya_tester(reya_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper that cleans state between tests.

    Reuses the session-scoped connection but ensures clean state for each test.
    """
    # Clean up any leftover positions and orders from previous test
    await reya_tester_session.positions.close_all(fail_if_none=False)
    await reya_tester_session.orders.close_all(fail_if_none=False)

    # Clear ALL WebSocket tracking state for fresh test
    reya_tester_session.ws.clear()

    yield reya_tester_session

    # Clean up positions and orders after test (connection stays open)
    await reya_tester_session.positions.close_all(fail_if_none=False)
    await reya_tester_session.orders.close_all(fail_if_none=False)


# ============================================================================
# Multi-Account Session Fixtures (Maker/Taker)
# ============================================================================


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def maker_tester_session():
    """
    Session-scoped maker account - ONE connection for entire test suite.

    Uses SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1 as the maker.
    """
    load_dotenv()

    # Maker uses Spot Account 1
    tester = ReyaTester(spot_account_number=1)

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip(
            "Missing Spot Account 1 configuration (SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1) for spot tests"
        )

    logger.info(f"🔧 SESSION: Maker account initialized: {tester.account_id}")

    # setup() calls client.start() internally
    await tester.setup()

    yield tester

    # Cleanup
    try:
        if tester.websocket:
            tester.websocket.close()
        preserve_orders = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"
        if not preserve_orders:
            await tester.orders.close_all(fail_if_none=False)
        else:
            logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping session cleanup for maker account")
        await tester.client.close()
        logger.info("✅ Maker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during maker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def taker_tester_session():
    """
    Session-scoped taker account - ONE connection for entire test suite.

    Uses SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_2, SPOT_WALLET_ADDRESS_2 as the taker.
    """
    load_dotenv()

    # Taker uses Spot Account 2
    tester = ReyaTester(spot_account_number=2)

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip(
            "Missing Spot Account 2 configuration (SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_2, SPOT_WALLET_ADDRESS_2) for spot tests"
        )

    logger.info(f"🔧 SESSION: Taker account initialized: {tester.account_id}")

    # setup() calls client.start() internally
    await tester.setup()

    yield tester

    # Cleanup
    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Taker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during taker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def maker_tester(maker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper for maker that cleans state between tests.

    Set SPOT_PRESERVE_ACCOUNT1_ORDERS=true to skip order cleanup for SPOT_ACCOUNT_ID_1.
    This is useful when testing with external liquidity from a depth script.
    """
    preserve_orders = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)
    else:
        logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping order cleanup for maker account")
    maker_tester_session.ws.clear()

    yield maker_tester_session

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def spot_tester(maker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper for single-account spot tests.
    Uses SPOT account 1 (same as maker_tester).

    Set SPOT_PRESERVE_ACCOUNT1_ORDERS=true to skip order cleanup for SPOT_ACCOUNT_ID_1.
    This is useful when testing with external liquidity from a depth script.
    """
    preserve_orders = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)
    else:
        logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping order cleanup for spot_tester")
    maker_tester_session.ws.clear()

    yield maker_tester_session

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def taker_tester(taker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper for taker that cleans state between tests.
    """
    await taker_tester_session.orders.close_all(fail_if_none=False)
    taker_tester_session.ws.clear()

    yield taker_tester_session

    await taker_tester_session.orders.close_all(fail_if_none=False)


# ============================================================================
# SPOT Test Configuration Fixture
# ============================================================================


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def spot_config(maker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Session-scoped fixture that provides centralized SPOT test configuration.

    Fetches the current ETH oracle price dynamically and provides a
    SpotTestConfig object with all test parameters.

    Usage in tests:
        async def test_something(spot_config, maker_tester):
            maker_price = spot_config.price(0.99)  # 99% of oracle
            qty = spot_config.min_qty
            symbol = spot_config.symbol
    """
    # Use ETHRUSD spot market for oracle price
    oracle_symbol = "ETHRUSD"

    try:
        price_str = await maker_tester_session.data.current_price(oracle_symbol)
        oracle_price = float(price_str)
        logger.info(f"📊 Fetched ETH oracle price: ${oracle_price:.2f}")
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning(f"Failed to fetch oracle price for {oracle_symbol}: {e}")
        oracle_price = 3000.0
        logger.warning(f"Using fallback oracle price: ${oracle_price:.2f}")

    return SpotTestConfig(symbol="WETHRUSD", min_qty="0.001", oracle_price=oracle_price)


# ============================================================================
# SPOT Balance Guard Fixture
# ============================================================================


async def _get_account_balance(tester: ReyaTester, asset: str) -> Decimal:
    """Get balance for a specific asset from a tester's account."""
    balance = await tester.data.balance(asset)
    if balance and balance.real_balance:
        return Decimal(balance.real_balance)
    return Decimal("0")


async def _execute_spot_transfer(
    sender: ReyaTester,
    receiver: ReyaTester,
    symbol: str,
    qty: str,
    price: str,
) -> bool:
    """
    Transfer spot assets between accounts via order matching.

    Sender places GTC sell, receiver places IOC buy at same price.
    Returns True if transfer succeeded.
    """
    # Step 1: Sender places GTC sell order
    sell_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=price,
        qty=qty,
        time_in_force=TimeInForce.GTC,
    )

    sell_response = await sender.client.create_limit_order(sell_params)
    sell_order_id = sell_response.order_id

    if not sell_order_id:
        logger.error("Failed to create sell order for transfer")
        return False

    await asyncio.sleep(0.3)

    # Step 2: Receiver places IOC buy order to match
    buy_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=price,
        qty=qty,
        time_in_force=TimeInForce.IOC,
    )

    await receiver.client.create_limit_order(buy_params)

    # Step 3: Wait for settlement and cancel any remaining sell order
    await asyncio.sleep(1.0)

    try:
        open_orders = await sender.client.get_open_orders()
        for order in open_orders:
            if hasattr(order, "order_id") and order.order_id == sell_order_id:
                await sender.client.cancel_order(
                    order_id=sell_order_id,
                    symbol=symbol,
                    account_id=sender.account_id,
                )
                break
    except (OSError, RuntimeError) as e:  # nosec B110
        logger.debug(f"Order cancel failed (may have been fully filled): {e}")

    return True


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def spot_balance_guard(
    maker_tester_session, taker_tester_session, spot_config
):  # pylint: disable=redefined-outer-name
    """
    Session-scoped fixture that checks balances before SPOT tests and restores them after.

    NOTE: This fixture is NOT autouse - it must be explicitly requested by spot tests.

    At session start:
    - Checks both accounts have minimum required balances (0.05 ETH, 50 RUSD each)
    - Stores initial balances for restoration
    - Skips all SPOT tests if balances are insufficient

    At session end:
    - Calculates balance differences from initial state
    - Executes transfers to restore initial balances
    """
    logger.info("=" * 60)
    logger.info("💰 SPOT BALANCE GUARD: Checking account balances")
    logger.info("=" * 60)

    # Get initial balances for both accounts
    maker_eth = await _get_account_balance(maker_tester_session, "ETH")
    maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    taker_eth = await _get_account_balance(taker_tester_session, "ETH")
    taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"📊 Account 1 (Maker): {maker_eth} ETH, {maker_rusd} RUSD")
    logger.info(f"📊 Account 2 (Taker): {taker_eth} ETH, {taker_rusd} RUSD")

    # Store initial balances for restoration
    initial_balances = {
        "maker_eth": maker_eth,
        "maker_rusd": maker_rusd,
        "taker_eth": taker_eth,
        "taker_rusd": taker_rusd,
    }

    # Check minimum requirements
    min_eth = Decimal(str(MIN_ETH_BALANCE))
    min_rusd = Decimal(str(MIN_RUSD_BALANCE))

    insufficient = []
    if maker_eth < min_eth:
        insufficient.append(f"Account 1 ETH: {maker_eth} < {min_eth}")
    if maker_rusd < min_rusd:
        insufficient.append(f"Account 1 RUSD: {maker_rusd} < {min_rusd}")
    if taker_eth < min_eth:
        insufficient.append(f"Account 2 ETH: {taker_eth} < {min_eth}")
    if taker_rusd < min_rusd:
        insufficient.append(f"Account 2 RUSD: {taker_rusd} < {min_rusd}")

    if insufficient:
        logger.error("❌ INSUFFICIENT BALANCES FOR SPOT TESTS:")
        for msg in insufficient:
            logger.error(f"   - {msg}")
        logger.error(f"   Required minimums: {MIN_ETH_BALANCE} ETH, {MIN_RUSD_BALANCE} RUSD per account")
        pytest.skip(f"Insufficient balances for SPOT tests: {', '.join(insufficient)}")

    logger.info("✅ Balance check passed - proceeding with SPOT tests")
    logger.info("=" * 60)

    # Run all SPOT tests
    yield initial_balances

    # ========================================================================
    # BALANCE RESTORATION (runs after all SPOT tests complete)
    # ========================================================================
    # This restoration logic handles both scenarios:
    # 1. Tests traded only between maker and taker accounts
    # 2. Tests traded with external liquidity (non-empty order book)
    #
    # Strategy: Track each account independently and restore via maker↔taker
    # transfers. External liquidity trades will show as imbalances that we
    # correct by transferring between our accounts.
    # ========================================================================
    logger.info("=" * 60)
    logger.info("💰 SPOT BALANCE GUARD: Restoring account balances")
    logger.info("=" * 60)

    # Get final balances for both accounts
    final_maker_eth = await _get_account_balance(maker_tester_session, "ETH")
    final_maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    final_taker_eth = await _get_account_balance(taker_tester_session, "ETH")
    final_taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"📊 Final Account 1 (Maker): {final_maker_eth} ETH, {final_maker_rusd} RUSD")
    logger.info(f"📊 Final Account 2 (Taker): {final_taker_eth} ETH, {final_taker_rusd} RUSD")

    # Calculate changes for EACH account independently
    maker_eth_change = final_maker_eth - initial_balances["maker_eth"]
    maker_rusd_change = final_maker_rusd - initial_balances["maker_rusd"]
    taker_eth_change = final_taker_eth - initial_balances["taker_eth"]
    taker_rusd_change = final_taker_rusd - initial_balances["taker_rusd"]

    logger.info(f"📈 Maker changes: ETH {maker_eth_change:+}, RUSD {maker_rusd_change:+}")
    logger.info(f"📈 Taker changes: ETH {taker_eth_change:+}, RUSD {taker_rusd_change:+}")

    symbol = "WETHRUSD"
    oracle_price = Decimal(str(spot_config.oracle_price))
    min_price = oracle_price * Decimal("0.95")
    max_price = oracle_price * Decimal("1.05")

    # Determine restoration needs
    # Net ETH change across both accounts (non-zero means external trades occurred)
    net_eth_change = maker_eth_change + taker_eth_change
    if abs(net_eth_change) >= Decimal("0.001"):
        logger.info(f"📊 Net ETH change (external trades): {net_eth_change:+}")

    min_qty = Decimal("0.001")

    # Calculate what each account needs to reach initial balance
    maker_eth_needed = initial_balances["maker_eth"] - final_maker_eth  # positive = needs more ETH
    taker_eth_needed = initial_balances["taker_eth"] - final_taker_eth  # positive = needs more ETH

    logger.info(f"📊 Maker needs: {maker_eth_needed:+} ETH, Taker needs: {taker_eth_needed:+} ETH")

    # Get current order book to determine restoration strategy
    depth = await taker_tester_session.data.market_depth(symbol)
    has_external_bids = depth.bids and len(depth.bids) > 0
    has_external_asks = depth.asks and len(depth.asks) > 0
    has_external_liquidity = has_external_bids or has_external_asks

    logger.info(f"📊 Order book: bids={has_external_bids}, asks={has_external_asks}")

    # ========================================================================
    # RESTORATION STRATEGY:
    # - If NO external liquidity: Use internal transfers (maker ↔ taker)
    # - If external liquidity exists: Each account trades with external liquidity
    #   (internal transfers won't work because IOC orders match external first)
    # ========================================================================

    if not has_external_liquidity:
        # EMPTY ORDER BOOK: Use internal transfers between maker and taker
        logger.info("📋 Empty order book - using internal transfers")

        # Calculate restoration price based on RUSD changes
        total_eth_moved = abs(maker_eth_change) + abs(taker_eth_change)
        total_rusd_moved = abs(maker_rusd_change) + abs(taker_rusd_change)

        if total_eth_moved > Decimal("0") and total_rusd_moved > Decimal("0.01"):
            effective_price = total_rusd_moved / total_eth_moved
        else:
            effective_price = oracle_price

        restoration_price = max(min_price, min(max_price, effective_price))
        restoration_price = restoration_price.quantize(Decimal("0.01"))
        logger.info(f"💱 Restoration price: ${restoration_price}")

        if abs(maker_eth_needed) >= min_qty:
            if maker_eth_needed > 0:
                qty = str(maker_eth_needed.quantize(min_qty))
                logger.info(f"🔄 Internal transfer: {qty} ETH from Taker → Maker @ ${restoration_price}")
                await _execute_spot_transfer(
                    sender=taker_tester_session,
                    receiver=maker_tester_session,
                    symbol=symbol,
                    qty=qty,
                    price=str(restoration_price),
                )
            else:
                qty = str((-maker_eth_needed).quantize(min_qty))
                logger.info(f"🔄 Internal transfer: {qty} ETH from Maker → Taker @ ${restoration_price}")
                await _execute_spot_transfer(
                    sender=maker_tester_session,
                    receiver=taker_tester_session,
                    symbol=symbol,
                    qty=qty,
                    price=str(restoration_price),
                )
            await asyncio.sleep(1.0)

    else:
        # NON-EMPTY ORDER BOOK: Each account trades with external liquidity
        logger.info("📋 External liquidity present - each account trades with external")

        # Helper function to restore an account's ETH balance via external liquidity
        async def restore_account_eth(tester: ReyaTester, eth_needed: Decimal, account_name: str):
            if abs(eth_needed) < min_qty:
                return

            if eth_needed > 0:
                # Account needs more ETH - buy from external asks
                if not has_external_asks:
                    logger.warning(f"⚠️ {account_name} needs {eth_needed} ETH but no external asks available")
                    return

                qty = str(eth_needed.quantize(min_qty))
                best_ask = Decimal(str(depth.asks[0].px))
                buy_price = min(max_price, best_ask * Decimal("1.001")).quantize(Decimal("0.01"))
                logger.info(f"🔄 {account_name}: Buying {qty} ETH @ ${buy_price} from external asks")

                try:
                    buy_params = LimitOrderParameters(
                        symbol=symbol,
                        is_buy=True,
                        limit_px=str(buy_price),
                        qty=qty,
                        time_in_force=TimeInForce.IOC,
                    )
                    await tester.client.create_limit_order(buy_params)
                    await asyncio.sleep(0.5)
                except (ApiException, OSError, RuntimeError) as e:
                    logger.warning(f"⚠️ {account_name}: Failed to buy ETH: {e}")

            else:
                # Account has excess ETH - sell to external bids
                if not has_external_bids:
                    logger.warning(f"⚠️ {account_name} has excess {-eth_needed} ETH but no external bids available")
                    return

                qty = str((-eth_needed).quantize(min_qty))
                best_bid = Decimal(str(depth.bids[0].px))
                sell_price = max(min_price, best_bid * Decimal("0.999")).quantize(Decimal("0.01"))
                logger.info(f"🔄 {account_name}: Selling {qty} ETH @ ${sell_price} to external bids")

                try:
                    sell_params = LimitOrderParameters(
                        symbol=symbol,
                        is_buy=False,
                        limit_px=str(sell_price),
                        qty=qty,
                        time_in_force=TimeInForce.IOC,
                    )
                    await tester.client.create_limit_order(sell_params)
                    await asyncio.sleep(0.5)
                except (ApiException, OSError, RuntimeError) as e:
                    logger.warning(f"⚠️ {account_name}: Failed to sell ETH: {e}")

        # Restore both accounts
        await restore_account_eth(maker_tester_session, maker_eth_needed, "Maker")
        await restore_account_eth(taker_tester_session, taker_eth_needed, "Taker")
        await asyncio.sleep(1.0)

    # Log final restored balances
    restored_maker_eth = await _get_account_balance(maker_tester_session, "ETH")
    restored_maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    restored_taker_eth = await _get_account_balance(taker_tester_session, "ETH")
    restored_taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"✅ Final Account 1 (Maker): {restored_maker_eth} ETH, {restored_maker_rusd} RUSD")
    logger.info(f"✅ Final Account 2 (Taker): {restored_taker_eth} ETH, {restored_taker_rusd} RUSD")

    # Log how close we got to initial balances for both accounts
    maker_eth_diff = restored_maker_eth - initial_balances["maker_eth"]
    maker_rusd_diff = restored_maker_rusd - initial_balances["maker_rusd"]
    taker_eth_diff = restored_taker_eth - initial_balances["taker_eth"]
    taker_rusd_diff = restored_taker_rusd - initial_balances["taker_rusd"]

    logger.info(f"📊 Maker remaining diff from initial: ETH {maker_eth_diff:+}, RUSD {maker_rusd_diff:+}")
    logger.info(f"📊 Taker remaining diff from initial: ETH {taker_eth_diff:+}, RUSD {taker_rusd_diff:+}")

    logger.info("=" * 60)
