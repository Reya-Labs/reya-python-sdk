import pytest
from dotenv import load_dotenv

from sdk.tests.reya_tester import ReyaTester, logger


@pytest.fixture
def reya_tester():
    """Create and return a configured ReyaTester instance"""
    # Load environment variables from .env file
    load_dotenv()

    # Initialize tester with environment variables
    tester = ReyaTester()

    # Ensure we have required configuration
    if not tester.wallet_address or not tester.account_id:
        pytest.skip("Missing required wallet address or account ID for tests")

    logger.info("Test client initialized:")
    logger.info(f"  Wallet Address: {tester.wallet_address}")
    logger.info(f"  Account ID: {tester.account_id}")
    logger.info(f"  Chain ID: {tester.chain_id}")

    yield tester
