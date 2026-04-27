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

from typing import TYPE_CHECKING, Union

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal

import pytest
import pytest_asyncio

from tests.helpers.liquidity_detector import (
    SAFE_NO_MATCH_BUY_PRICE,
    SAFE_NO_MATCH_SELL_PRICE,
    LiquidityDetector,
    OrderBookState,
    log_order_book_state,
)
from tests.test_spot.spot_config import SpotTestConfig

if TYPE_CHECKING:
    from tests.helpers.reya_tester import ReyaTester
    from tests.helpers.reya_tester.data import DataOperations

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

    Implements the same liquidity-detection surface (``refresh_order_book``,
    ``has_*_liquidity``, ``best_*_price``, etc.) as SpotTestConfig so the shared
    test_orderbook tests can treat the two configs interchangeably.
    """

    symbol: str
    market_id: int
    min_qty: str
    qty_step_size: str
    oracle_price: float
    base_asset: str
    min_balance: float
    _order_book: OrderBookState | None = field(default=None, repr=False)

    def price(self, multiplier: float = 1.0) -> float:
        return round(self.oracle_price * multiplier, 2)

    def buy_price(self, multiplier: float = 0.99) -> float:
        return self.price(multiplier)

    def sell_price(self, multiplier: float = 1.01) -> float:
        return self.price(multiplier)

    async def refresh_order_book(self, data_ops: DataOperations) -> OrderBookState:
        detector = LiquidityDetector(self.oracle_price)
        self._order_book = await detector.get_order_book_state(data_ops, self.symbol)
        log_order_book_state(self._order_book)
        return self._order_book

    @property
    def order_book(self) -> OrderBookState | None:
        return self._order_book

    @property
    def has_any_external_liquidity(self) -> bool:
        return False if self._order_book is None else self._order_book.has_any_liquidity

    @property
    def has_usable_bid_liquidity(self) -> bool:
        return False if self._order_book is None else self._order_book.has_usable_bid_liquidity

    @property
    def has_usable_ask_liquidity(self) -> bool:
        return False if self._order_book is None else self._order_book.has_usable_ask_liquidity

    @property
    def best_bid_price(self) -> Decimal | None:
        if self._order_book is None or not self._order_book.bids.has_liquidity:
            return None
        return self._order_book.bids.best_price

    @property
    def best_ask_price(self) -> Decimal | None:
        if self._order_book is None or not self._order_book.asks.has_liquidity:
            return None
        return self._order_book.asks.best_price

    @property
    def circuit_breaker_floor(self) -> Decimal:
        return (Decimal(str(self.oracle_price)) * Decimal("0.95")).quantize(Decimal("0.01"))

    @property
    def circuit_breaker_ceiling(self) -> Decimal:
        return (Decimal(str(self.oracle_price)) * Decimal("1.05")).quantize(Decimal("0.01"))

    def get_usable_bid_price_for_qty(self, qty: str) -> Decimal | None:
        if self._order_book is None:
            return None
        return LiquidityDetector(self.oracle_price).get_usable_bid_price(self._order_book, qty)

    def get_usable_ask_price_for_qty(self, qty: str) -> Decimal | None:
        if self._order_book is None:
            return None
        return LiquidityDetector(self.oracle_price).get_usable_ask_price(self._order_book, qty)

    def get_safe_no_match_buy_price(self) -> Decimal:
        return SAFE_NO_MATCH_BUY_PRICE

    def get_safe_no_match_sell_price(self) -> Decimal:
        return SAFE_NO_MATCH_SELL_PRICE


# Type alias for tests that accept either config — duck-typed via the shared surface above.
MarketConfig = Union[SpotTestConfig, PerpTestConfig]


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
def market_config(
    market_type: str, spot_config: SpotTestConfig, perp_market_config: PerpTestConfig
) -> MarketConfig:
    """Yield the right per-market config for the active parametrization.

    Tests use this fixture as the single source of symbol/min_qty/oracle_price,
    regardless of whether the parametrization picked spot or perp. The two
    config types share the surface OrderBuilder + liquidity helpers need.
    """
    return spot_config if market_type == "spot" else perp_market_config


@pytest.fixture
def maker(
    market_type: str,
    maker_tester,  # spot maker (PERP_ACCOUNT_ID_1 / SPOT_ACCOUNT_ID_1)
    perp_maker_tester,
):
    """Yield the maker tester for the active parametrization."""
    return maker_tester if market_type == "spot" else perp_maker_tester


@pytest.fixture
def taker(
    market_type: str,
    taker_tester,
    perp_taker_tester,
):
    """Yield the taker tester for the active parametrization."""
    return taker_tester if market_type == "spot" else perp_taker_tester
