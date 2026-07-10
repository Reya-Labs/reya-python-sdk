from decimal import Decimal

import pytest

from tests.helpers.price_helpers import (
    floor_price,
    format_price,
    limit_price,
    one_tick_inside_spread,
    self_match_resting_prices,
)

pytestmark = pytest.mark.offline


def test_limit_price_quantizes_oracle_price_to_market_tick():
    price = limit_price(oracle_price="1738.2773364199493", tick_size="0.01")

    assert price == Decimal("1738.28")
    assert format_price(price) == "1738.28"


def test_limit_price_snaps_to_non_power_of_ten_tick_multiple():
    price = limit_price(oracle_price="100.03", tick_size="0.05")

    assert price == Decimal("100.05")


def test_floor_price_snaps_down_to_tick_grid():
    price = floor_price(oracle_price="100.019", tick_size="0.01")

    assert price == Decimal("100.01")


def test_one_tick_inside_spread_improves_bid_without_crossing_ask():
    price = one_tick_inside_spread(best_bid="100", best_ask="100.02", tick_size="0.01")

    assert price == Decimal("100.01")


def test_one_tick_inside_spread_returns_none_when_no_tick_room():
    price = one_tick_inside_spread(best_bid="100", best_ask="100.01", tick_size="0.01")

    assert price is None


def test_one_tick_inside_spread_improves_ask_when_only_asks_exist():
    price = one_tick_inside_spread(best_bid=None, best_ask="100", tick_size="0.01")

    assert price == Decimal("99.99")


def test_self_match_resting_prices_stay_inside_external_spread():
    prices = self_match_resting_prices(best_bid="100", best_ask="100.03", tick_size="0.01")

    assert prices is not None
    assert prices.bid_px == "100.00"
    assert prices.ask_px == "100.01"


def test_self_match_resting_prices_return_none_when_spread_has_no_tick_room():
    prices = self_match_resting_prices(best_bid="100", best_ask="100.01", tick_size="0.01")

    assert prices is None
