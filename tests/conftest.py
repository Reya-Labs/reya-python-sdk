"""
Pytest fixtures for Reya Python SDK integration tests.

Uses pytest-asyncio's loop_scope feature (v0.24+) to share a single event loop
across all tests in a session, enabling session-scoped async fixtures.
"""

import asyncio

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Time delay between tests
TEST_DELAY_SECONDS = 0.1

# Minimum balance requirements for SPOT tests
MIN_ETH_BALANCE = 0.05
MIN_RUSD_BALANCE = 15.0

from decimal import Decimal  # noqa: E402

from sdk.open_api.models import TimeInForce  # noqa: E402
from sdk.reya_rest_api.models.orders import LimitOrderParameters  # noqa: E402
from tests.helpers import ReyaTester  # noqa: E402
from tests.helpers.reya_tester import logger  # noqa: E402
from tests.test_spot.spot_config import SpotTestConfig  # noqa: E402


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
        await tester.close_exposures(fail_if_none=False)
        await tester.close_active_orders(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during session cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def reya_tester(reya_tester_session):
    """
    Function-scoped wrapper that cleans state between tests.

    Reuses the session-scoped connection but ensures clean state for each test.
    """
    # Clean up any leftover positions and orders from previous test
    await reya_tester_session.close_exposures(fail_if_none=False)
    await reya_tester_session.close_active_orders(fail_if_none=False)

    # Clear ALL WebSocket tracking state for fresh test
    reya_tester_session.ws.clear()  # Clear all WebSocket state including positions
    reya_tester_session.ws_order_changes.clear()
    reya_tester_session.ws_balance_updates.clear()
    reya_tester_session.ws_last_trade = None

    yield reya_tester_session

    # Clean up positions and orders after test (connection stays open)
    await reya_tester_session.close_exposures(fail_if_none=False)
    await reya_tester_session.close_active_orders(fail_if_none=False)


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
        await tester.close_active_orders(fail_if_none=False)
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
        await tester.close_active_orders(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Taker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during taker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def maker_tester(maker_tester_session):
    """
    Function-scoped wrapper for maker that cleans state between tests.
    """
    await maker_tester_session.close_active_orders(fail_if_none=False)
    maker_tester_session.ws_order_changes.clear()
    maker_tester_session.ws_balance_updates.clear()
    maker_tester_session.ws.clear_spot_executions()  # Clear all spot executions
    maker_tester_session.ws_last_trade = None

    yield maker_tester_session

    await maker_tester_session.close_active_orders(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def spot_tester(maker_tester_session):
    """
    Function-scoped wrapper for single-account spot tests.
    Uses SPOT account 1 (same as maker_tester).
    """
    await maker_tester_session.close_active_orders(fail_if_none=False)
    maker_tester_session.ws_order_changes.clear()
    maker_tester_session.ws_balance_updates.clear()
    maker_tester_session.ws.clear_spot_executions()
    maker_tester_session.ws_last_trade = None

    yield maker_tester_session

    await maker_tester_session.close_active_orders(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def taker_tester(taker_tester_session):
    """
    Function-scoped wrapper for taker that cleans state between tests.
    """
    await taker_tester_session.close_active_orders(fail_if_none=False)
    taker_tester_session.ws_order_changes.clear()
    taker_tester_session.ws_balance_updates.clear()
    taker_tester_session.ws.clear_spot_executions()  # Clear all spot executions
    taker_tester_session.ws_last_trade = None

    yield taker_tester_session

    await taker_tester_session.close_active_orders(fail_if_none=False)


# ============================================================================
# SPOT Test Configuration Fixture
# ============================================================================


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def spot_config(maker_tester_session):
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


@pytest_asyncio.fixture(loop_scope="session", scope="session", autouse=True)
async def spot_balance_guard(maker_tester_session, taker_tester_session, spot_config):
    """
    Session-scoped fixture that checks balances before SPOT tests and restores them after.

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
    logger.info("=" * 60)
    logger.info("💰 SPOT BALANCE GUARD: Restoring account balances")
    logger.info("=" * 60)

    # Get final balances
    final_maker_eth = await _get_account_balance(maker_tester_session, "ETH")
    final_maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    final_taker_eth = await _get_account_balance(taker_tester_session, "ETH")
    final_taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"📊 Final Account 1 (Maker): {final_maker_eth} ETH, {final_maker_rusd} RUSD")
    logger.info(f"📊 Final Account 2 (Taker): {final_taker_eth} ETH, {final_taker_rusd} RUSD")

    # Calculate differences (positive = maker gained, negative = maker lost)
    eth_diff = final_maker_eth - initial_balances["maker_eth"]
    rusd_diff = final_maker_rusd - initial_balances["maker_rusd"]

    logger.info(f"📈 Changes for Maker: ETH {eth_diff:+}, RUSD {rusd_diff:+}")

    symbol = "WETHRUSD"
    oracle_price = Decimal(str(spot_config.oracle_price))
    min_price = oracle_price * Decimal("0.95")
    max_price = oracle_price * Decimal("1.05")

    # Restore balances if significant ETH change
    if abs(eth_diff) >= Decimal("0.001"):
        # Calculate the effective price that would restore RUSD exactly
        # effective_price = |RUSD_diff| / |ETH_diff|
        # This is the average price at which trades occurred during tests
        if abs(rusd_diff) > Decimal("0.01"):
            effective_price = abs(rusd_diff) / abs(eth_diff)
        else:
            effective_price = oracle_price

        logger.info(f"� Effective trade price during tests: ${effective_price:.2f}")

        # Clamp to allowed price range (±5% of oracle)
        restoration_price = max(min_price, min(max_price, effective_price))
        restoration_price = restoration_price.quantize(Decimal("0.01"))

        logger.info(f"� Restoration price (clamped to range): ${restoration_price}")

        if eth_diff > 0:
            # Maker gained ETH during tests, transfer back to Taker
            qty = str(eth_diff.quantize(Decimal("0.001")))
            logger.info(f"🔄 Restoring: Transferring {qty} ETH from Maker → Taker @ ${restoration_price}")
            await _execute_spot_transfer(
                sender=maker_tester_session,
                receiver=taker_tester_session,
                symbol=symbol,
                qty=qty,
                price=str(restoration_price),
            )
        else:
            # Maker lost ETH during tests, transfer from Taker back to Maker
            qty = str((-eth_diff).quantize(Decimal("0.001")))
            logger.info(f"🔄 Restoring: Transferring {qty} ETH from Taker → Maker @ ${restoration_price}")
            await _execute_spot_transfer(
                sender=taker_tester_session,
                receiver=maker_tester_session,
                symbol=symbol,
                qty=qty,
                price=str(restoration_price),
            )

        await asyncio.sleep(1.0)

    # Log final restored balances
    restored_maker_eth = await _get_account_balance(maker_tester_session, "ETH")
    restored_maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    restored_taker_eth = await _get_account_balance(taker_tester_session, "ETH")
    restored_taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"✅ Final Account 1 (Maker): {restored_maker_eth} ETH, {restored_maker_rusd} RUSD")
    logger.info(f"✅ Final Account 2 (Taker): {restored_taker_eth} ETH, {restored_taker_rusd} RUSD")

    # Log how close we got to initial balances
    final_eth_diff = restored_maker_eth - initial_balances["maker_eth"]
    final_rusd_diff = restored_maker_rusd - initial_balances["maker_rusd"]
    logger.info(f"📊 Remaining difference from initial: ETH {final_eth_diff:+}, RUSD {final_rusd_diff:+}")

    logger.info("=" * 60)
