"""
Pytest fixtures for Reya Python SDK integration tests.

Uses pytest-asyncio's loop_scope feature (v0.24+) to share a single event loop
across all tests in a session, enabling session-scoped async fixtures.
"""

import asyncio
import logging
import os

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Delay between tests to avoid WAF rate limiting (in seconds)
TEST_DELAY_SECONDS = 0.1

from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

# ============================================================================
# Rate Limiting Protection
# ============================================================================
# Add a small delay between tests to avoid WAF rate limiting on staging


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
    except Exception as e:
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
        pytest.skip("Missing Spot Account 1 configuration (SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1) for spot tests")

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
    except Exception as e:
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
        pytest.skip("Missing Spot Account 2 configuration (SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_2, SPOT_WALLET_ADDRESS_2) for spot tests")

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
    except Exception as e:
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
