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
