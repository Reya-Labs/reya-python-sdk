import os
import pytest
from dotenv import load_dotenv

from tests.reya_tester import ReyaTester, logger


@pytest.fixture
def reya_tester_base():
    """Create and return a configured ReyaTester instance"""
    # Load environment variables from .env file
    load_dotenv()

    # Initialize tester with environment variables
    tester = ReyaTester()

    # Ensure we have required configuration
    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip("Missing required wallet address or account ID for tests")

    logger.info("Test client initialized:")
    logger.info(f"  Wallet Address: {tester.owner_wallet_address}")
    logger.info(f"  Account ID: {tester.account_id}")
    logger.info(f"  Chain ID: {tester.chain_id}")

    yield tester


@pytest.fixture(scope="function")
async def reya_tester(reya_tester_base):
    """Create a ReyaTester instance with WebSocket setup and automatic cleanup"""
    # Initialize the client (load market definitions)
    await reya_tester_base.client.start()

    # Setup WebSocket connection
    await reya_tester_base.setup()
    logger.info("🚀 WebSocket setup completed for test")

    yield reya_tester_base

    # Cleanup
    logger.info("----------- Cleaning up test environment -----------")
    try:
        # Close WebSocket connection
        if reya_tester_base.websocket:
            reya_tester_base.websocket.close()
            logger.info("✅ WebSocket connection closed")

        # Close any open positions for common test symbols
        try:
            await reya_tester_base.close_exposures(fail_if_none=False)
        except Exception as e:
            logger.debug(f"No exposure to close: {e}")

        # Cancel any active orders
        await reya_tester_base.close_active_orders(fail_if_none=False)
        logger.info("✅ Active orders cancelled")

        await reya_tester_base.client.close()
        logger.info("✅ REST API client session closed")

    except Exception as e:
        logger.warning(f"Error during cleanup: {e}")

    logger.info("🧹 Test cleanup completed")


@pytest.fixture(scope="function")
async def maker_tester():
    """Create a ReyaTester instance for the maker account (7971)"""
    load_dotenv()

    # Save original env vars
    original_account_id = os.environ.get("ACCOUNT_ID")
    original_private_key = os.environ.get("PRIVATE_KEY")
    original_api_url = os.environ.get("REYA_API_URL")
    original_wallet_address = os.environ.get("OWNER_WALLET_ADDRESS")
    os.environ["ACCOUNT_ID"] = "7971"


    tester = ReyaTester()
    await tester.client.start()
    await tester.setup()

    logger.info(f"🏭 Maker account initialized: {tester.account_id}")

    yield tester

    # Cleanup
    logger.info("----------- Cleaning up maker account -----------")
    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.close_active_orders(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Maker cleanup completed")
    except Exception as e:
        logger.warning(f"Error during maker cleanup: {e}")

    # Restore original env
    if original_account_id:
        os.environ["ACCOUNT_ID"] = original_account_id
    if original_private_key:
        os.environ["PRIVATE_KEY"] = original_private_key
    if original_api_url:
        os.environ["REYA_API_URL"] = original_api_url
    if original_wallet_address:
        os.environ["OWNER_WALLET_ADDRESS"] = original_wallet_address


@pytest.fixture(scope="function")
async def taker_tester():
    """Create a ReyaTester instance for the taker account (8041)"""
    load_dotenv()

    # Save original env vars
    original_account_id = os.environ.get("ACCOUNT_ID")
    original_private_key = os.environ.get("PRIVATE_KEY")
    original_api_url = os.environ.get("REYA_API_URL")
    original_wallet_address = os.environ.get("OWNER_WALLET_ADDRESS")

    os.environ["ACCOUNT_ID"] = "8041"

    tester = ReyaTester()
    await tester.client.start()
    await tester.setup()

    logger.info(f"🎯 Taker account initialized: {tester.account_id}")

    yield tester

    # Cleanup
    logger.info("----------- Cleaning up taker account -----------")
    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.close_active_orders(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Taker cleanup completed")
    except Exception as e:
        logger.warning(f"Error during taker cleanup: {e}")

    # Restore original env
    if original_account_id:
        os.environ["ACCOUNT_ID"] = original_account_id
    if original_private_key:
        os.environ["PRIVATE_KEY"] = original_private_key
    if original_api_url:
        os.environ["REYA_API_URL"] = original_api_url
    if original_wallet_address:
        os.environ["OWNER_WALLET_ADDRESS"] = original_wallet_address
