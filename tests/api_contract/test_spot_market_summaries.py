"""Public REST contract coverage for spot market summaries."""

import time

import pytest

from tests.helpers import ReyaTester

pytestmark = [pytest.mark.rest_api, pytest.mark.spot]


async def test_spot_market_summary_resolves(reya_tester: ReyaTester):
    """The single-market endpoint returns the current spot summary shape."""
    definitions = await reya_tester.client.reference.get_spot_market_definitions()
    assert definitions, "spotMarketDefinitions returned nothing"
    symbol = definitions[0].symbol

    summary = await reya_tester.client.markets.get_spot_market_summary(symbol)

    assert summary.symbol == symbol
    assert float(summary.volume24h) >= 0
    assert summary.updated_at > int(time.time() * 1000) - 2 * 24 * 60 * 60 * 1000
    if summary.px_change24h is not None:
        assert abs(float(summary.px_change24h)) < 10**18
    if summary.oracle_price is not None:
        assert float(summary.oracle_price) > 0


async def test_spot_markets_summary_resolves(reya_tester: ReyaTester):
    """The all-markets endpoint includes every configured spot market."""
    definitions = await reya_tester.client.reference.get_spot_market_definitions()
    expected_symbols = {definition.symbol for definition in definitions}
    assert expected_symbols, "spotMarketDefinitions returned nothing"

    summaries = await reya_tester.client.markets.get_spot_markets_summary()

    assert summaries
    by_symbol = {summary.symbol: summary for summary in summaries}
    assert expected_symbols <= by_symbol.keys()
    for symbol in expected_symbols:
        assert float(by_symbol[symbol].volume24h) >= 0
