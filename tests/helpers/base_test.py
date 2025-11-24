#!/usr/bin/env python3

from typing import Optional

import logging

import pytest

from tests.helpers.reya_tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class BaseReyaTest:
    """Base test class that handles common setup and teardown for Reya tests"""

    def __init__(self):
        self.reya_tester: Optional[ReyaTester] = None
        self.test_symbol: str = "ETHRUSDPERP"

    async def setup_test(self, reya_tester: ReyaTester, symbol: str = "ETHRUSDPERP"):
        """Setup WebSocket connection and initialize test environment"""
        self.reya_tester = reya_tester
        self.test_symbol = symbol

        # Setup WebSocket connection for monitoring
        await self.reya_tester.setup()

        logger.info(f"🚀 Test setup completed for symbol: {symbol}")

    async def teardown_test(self, fail_if_none: bool = False):
        """Clean up WebSocket connections, positions, and orders"""
        if not self.reya_tester:
            logger.warning("No reya_tester instance found for cleanup")
            return

        logger.info("----------- Cleaning up test environment -----------")

        try:
            # Close WebSocket connection
            if self.reya_tester.websocket:
                self.reya_tester.websocket.close()
                logger.info("✅ WebSocket connection closed")

            # Close any open positions
            await self.reya_tester.close_exposures(fail_if_none=fail_if_none)
            logger.info("✅ Positions closed")

            # Cancel any active orders
            await self.reya_tester.close_active_orders(fail_if_none=fail_if_none)
            logger.info("✅ Active orders cancelled")

        except Exception as e:
            logger.warning(f"Error during cleanup: {e}")

        logger.info("🧹 Test cleanup completed")


@pytest.mark.asyncio
class AsyncBaseReyaTest(BaseReyaTest):
    """Async version of base test class with pytest async support"""

    async def run_test_with_cleanup(self, test_func, reya_tester: ReyaTester, symbol: str = "ETHRUSDPERP", **kwargs):
        """
        Run a test function with automatic setup and cleanup

        Args:
            test_func: The actual test function to run
            reya_tester: The ReyaTester fixture
            symbol: Symbol to use for testing
            **kwargs: Additional arguments to pass to the test function
        """
        await self.setup_test(reya_tester, symbol)

        try:
            # Run the actual test
            await test_func(self.reya_tester, **kwargs)

        except Exception as e:
            logger.error(f"Error in test: {e}")
            raise
        finally:
            # Always cleanup
            await self.teardown_test(fail_if_none=False)
