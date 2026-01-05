"""
Spot-specific pytest fixtures.

This conftest ensures spot fixtures are only initialized when running spot tests.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def spot_test_guard(spot_balance_guard):
    """
    Auto-use fixture that ensures spot_balance_guard runs for all spot tests.
    
    This fixture is only loaded when running tests in the test_spot directory,
    ensuring perp tests don't trigger spot account initialization.
    """
    yield spot_balance_guard
