"""Shared fixtures for the engine ws-exec suites.

The ws-exec engine suites (gtt / modify / post_only / cod) pin the WS-EXEC
transport over BOTH markets via ``ws_exec_market`` — the ws-exec twin of the
root conftest's ``market_type``/``market_config`` REST parametrization.
"""

from __future__ import annotations

import pytest_asyncio

from tests.helpers.ws_exec_market import WS_EXEC_MARKETS, build_ws_exec_market


@pytest_asyncio.fixture(loop_scope="session", scope="module", params=WS_EXEC_MARKETS)
async def ws_exec_market(request):
    """Market-parametrized ([spot, perp]) connected ws-exec harness.

    Every engine ws-exec test that takes this fixture runs once per market.
    Missing ws-exec URL/account prerequisites fail by default. Module-scoped so
    one connection set is shared across a suite."""
    async with build_ws_exec_market(request.param) as market:
        yield market
