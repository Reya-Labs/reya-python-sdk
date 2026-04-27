"""
Pytest fixtures for the shared orderbook lifecycle tests.

Tests under tests/test_orderbook/ exercise behaviours that are identical for
spot and perp markets in the v2.3.0 unified API: place, cancel, mass-cancel,
maker/taker matching, websocket order/depth events. Each test is parametrized
over the ``market_config`` fixture below, which yields a per-market config
matching the shape of ``tests/test_spot/spot_config.SpotTestConfig`` so existing
helpers (OrderBuilder, ReyaTester) keep working.

Market-specific tests (spot busts, perp triggers/positions/funding) live in
``tests/test_spot/`` and ``tests/test_perps/`` respectively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logging
import os
from dataclasses import dataclass
from decimal import Decimal

import pytest
import pytest_asyncio

from tests.test_spot.spot_config import SpotTestConfig, fetch_spot_market_configs

if TYPE_CHECKING:
    from tests.helpers.reya_tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")

# Tests in this directory are parametrized over both spot and perp; tests that
# only make sense for one market type can filter via params=["spot"] or
# params=["perp"] on a per-test basis.
_DEFAULT_MARKET_TYPES = ("spot", "perp")


def pytest_addoption(parser):
    """Add CLI options scoped to orderbook tests."""
    parser.addoption(
        "--orderbook-perp-asset",
        action="store",
        default="ETH",
        help="Base asset for perp orderbook tests (e.g. ETH). Symbol becomes <asset>RUSDPERP.",
    )


@dataclass
class PerpTestConfig:
    """Mirrors SpotTestConfig's shape so OrderBuilder + helpers can consume either.

    Fields not relevant to perp (e.g. base_asset for balance accounting) carry
    sensible defaults.
    """

    symbol: str
    market_id: int
    min_qty: str
    qty_step_size: str
    oracle_price: float
    base_asset: str
    min_balance: float

    def price(self, multiplier: float = 1.0) -> float:
        return round(self.oracle_price * multiplier, 2)

    def buy_price(self, multiplier: float = 0.99) -> float:
        return self.price(multiplier)

    def sell_price(self, multiplier: float = 1.01) -> float:
        return self.price(multiplier)


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def perp_market_config(maker_tester_session) -> PerpTestConfig:  # type: ignore[no-untyped-def]
    """Fetch a perp market config for parametrized orderbook tests.

    Uses ``--orderbook-perp-asset`` (default ETH). Skips if the testnet/perpOB
    deployment hasn't enabled this market on the matching engine
    (see ``PERP_OB_MARKET_IDS`` launch gate in
    https://github.com/Reya-Labs/reya-off-chain-monorepo/pull/2588).
    """
    asset = os.environ.get("ORDERBOOK_PERP_ASSET", "ETH").upper()
    symbol = f"{asset}RUSDPERP"

    market_def = None
    for definition in await maker_tester_session.client.reference.get_market_definitions():
        if definition.symbol == symbol:
            market_def = definition
            break

    if market_def is None:
        pytest.skip(f"Perp market {symbol} not present in /v2/marketDefinitions")

    try:
        oracle_price = float(await maker_tester_session.data.current_price(symbol))
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning(f"Failed to fetch oracle price for {symbol}: {e}")
        oracle_price = 3000.0

    return PerpTestConfig(
        symbol=symbol,
        market_id=market_def.market_id,
        min_qty=str(market_def.min_order_qty),
        qty_step_size=str(market_def.qty_step_size),
        oracle_price=oracle_price,
        base_asset=asset,
        min_balance=float(Decimal(market_def.min_order_qty) * 50),
    )


@pytest.fixture(params=_DEFAULT_MARKET_TYPES)
def market_type(request) -> str:
    """Parametrize over [spot, perp] — the param drives ``market_config``."""
    return request.param


@pytest.fixture
def market_config(market_type: str, spot_config: SpotTestConfig, perp_market_config: PerpTestConfig):
    """Yield the right per-market config for the active parametrization.

    Tests use this fixture as the single source of symbol/min_qty/oracle_price,
    regardless of whether the parametrization picked spot or perp. The two
    config types share the surface OrderBuilder needs.
    """
    return spot_config if market_type == "spot" else perp_market_config
