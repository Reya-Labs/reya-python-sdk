"""
Pytest fixtures for Reya Python SDK integration tests.

This module provides fixtures for both legacy (ReyaTester-based) tests
and new modular tests using the refactored helper structure.
"""

import os
import asyncio
import logging
from typing import AsyncGenerator

import pytest
from dotenv import load_dotenv

# Legacy imports
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

# New modular imports
from tests.helpers.config import TestConfig, get_test_config
from tests.helpers.clients import RestClient, WebSocketClient
from tests.helpers.waiters import EventWaiter


# ============================================================================
# Configuration Fixtures
# ============================================================================

@pytest.fixture(scope="session")
def test_config() -> TestConfig:
    """
    Session-scoped test configuration.
    
    Loads configuration from environment variables once per test session.
    """
    load_dotenv()
    config = get_test_config()
    
    if not config.is_valid():
        pytest.skip("Missing required configuration (OWNER_WALLET_ADDRESS, ACCOUNT_ID)")
    
    return config


# ============================================================================
# New Modular Fixtures (for refactored tests)
# ============================================================================

@pytest.fixture(scope="function")
async def rest_client() -> AsyncGenerator[RestClient, None]:
    """
    Function-scoped REST client fixture.
    
    Provides a configured RestClient with automatic cleanup.
    """
    load_dotenv()
    client = RestClient()
    await client.start()
    
    logger.info(f"REST client initialized: account={client.account_id}")
    
    yield client
    
    await client.stop()
    logger.info("REST client closed")


@pytest.fixture(scope="function")
def ws_client() -> WebSocketClient:
    """
    Function-scoped WebSocket client fixture.
    
    Provides a configured WebSocketClient with automatic cleanup.
    Note: Call connect() and subscribe methods as needed in tests.
    """
    load_dotenv()
    client = WebSocketClient()
    
    yield client
    
    if client.is_connected:
        client.disconnect()
    logger.info("WebSocket client closed")


@pytest.fixture(scope="function")
async def event_waiter(rest_client: RestClient, ws_client: WebSocketClient) -> EventWaiter:
    """
    Function-scoped event waiter fixture.
    
    Provides an EventWaiter configured with REST and WebSocket clients.
    """
    return EventWaiter(rest_client, ws_client)


# ============================================================================
# Legacy Fixtures (for backwards compatibility)
# ============================================================================

@pytest.fixture
def reya_tester_base():
    """Create and return a configured ReyaTester instance (legacy)"""
    load_dotenv()

    tester = ReyaTester()

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip("Missing required wallet address or account ID for tests")

    logger.info("Test client initialized:")
    logger.info(f"  Wallet Address: {tester.owner_wallet_address}")
    logger.info(f"  Account ID: {tester.account_id}")
    logger.info(f"  Chain ID: {tester.chain_id}")

    yield tester


@pytest.fixture(scope="function")
async def reya_tester(reya_tester_base):
    """Create a ReyaTester instance with WebSocket setup and automatic cleanup (legacy)"""
    await reya_tester_base.client.start()
    await reya_tester_base.setup()
    logger.info("🚀 WebSocket setup completed for test")

    yield reya_tester_base

    # Cleanup
    logger.info("----------- Cleaning up test environment -----------")
    try:
        if reya_tester_base.websocket:
            reya_tester_base.websocket.close()
            logger.info("✅ WebSocket connection closed")

        try:
            await reya_tester_base.close_exposures(fail_if_none=False)
        except Exception as e:
            logger.debug(f"No exposure to close: {e}")

        await reya_tester_base.close_active_orders(fail_if_none=False)
        logger.info("✅ Active orders cancelled")

        await reya_tester_base.client.close()
        logger.info("✅ REST API client session closed")

    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")

    logger.info("🧹 Test cleanup completed")


# ============================================================================
# Multi-Account Fixtures (Maker/Taker)
# ============================================================================

def _create_account_tester_fixture(account_env_var: str, account_name: str, default_id: str):
    """
    Factory function to create account-specific tester fixtures.
    
    This reduces code duplication between maker_tester and taker_tester.
    """
    async def _fixture():
        load_dotenv()

        # Save original env vars
        original_account_id = os.environ.get("ACCOUNT_ID")
        original_private_key = os.environ.get("PRIVATE_KEY")
        original_api_url = os.environ.get("REYA_API_URL")
        original_wallet_address = os.environ.get("OWNER_WALLET_ADDRESS")
        
        # Get account ID from env, fallback to default if not set
        account_id = os.environ.get(account_env_var, default_id)
        os.environ["ACCOUNT_ID"] = account_id

        tester = ReyaTester()
        await tester.client.start()
        await tester.setup()

        logger.info(f"🔧 {account_name} account initialized: {tester.account_id}")

        yield tester

        # Cleanup
        logger.info(f"----------- Cleaning up {account_name} account -----------")
        try:
            if tester.websocket:
                tester.websocket.close()
            await tester.close_active_orders(fail_if_none=False)
            await tester.client.close()
            logger.info(f"✅ {account_name} cleanup completed")
        except Exception as e:
            logger.warning(f"Error during {account_name} cleanup: {e}")

        # Restore original env
        if original_account_id:
            os.environ["ACCOUNT_ID"] = original_account_id
        if original_private_key:
            os.environ["PRIVATE_KEY"] = original_private_key
        if original_api_url:
            os.environ["REYA_API_URL"] = original_api_url
        if original_wallet_address:
            os.environ["OWNER_WALLET_ADDRESS"] = original_wallet_address
    
    return _fixture


# Create maker fixture using factory
_maker_fixture_impl = _create_account_tester_fixture("MAKER_ACCOUNT_ID", "Maker", "8017")

@pytest.fixture(scope="function")
async def maker_tester():
    """Create a ReyaTester instance for the maker account (legacy)"""
    async for tester in _maker_fixture_impl():
        yield tester


# Create taker fixture using factory
_taker_fixture_impl = _create_account_tester_fixture("TAKER_ACCOUNT_ID", "Taker", "8044")

@pytest.fixture(scope="function")
async def taker_tester():
    """Create a ReyaTester instance for the taker account (legacy)"""
    async for tester in _taker_fixture_impl():
        yield tester
