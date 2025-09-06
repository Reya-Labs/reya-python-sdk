#!/usr/bin/env python3

import time

import pytest

from sdk.tests.reya_tester import ReyaTester, logger


@pytest.mark.asyncio
async def test_market_definition(reya_tester: ReyaTester):
    """Test getting market definition for a specific symbol."""
    symbol = "ETHRUSDPERP"

    try:
        market_definition = reya_tester.get_market_definition(symbol)
        assert market_definition is not None
        assert market_definition.market_id == 1, "Wrong market id"
        assert (
            float(market_definition.min_order_qty) > 0 and float(market_definition.min_order_qty) < 10**18
        ), "Wrong min order qty"
        assert (
            float(market_definition.qty_step_size) > 0 and float(market_definition.qty_step_size) < 10**18
        ), "Wrong qty step size"
        assert float(market_definition.tick_size) > 0 and float(market_definition.tick_size) < 10**18, "Wrong tick size"
        assert (
            float(market_definition.liquidation_margin_parameter) > 0
            and float(market_definition.liquidation_margin_parameter) < 10**18
        ), "Wrong liquidation margin parameter"
        assert (
            float(market_definition.initial_margin_parameter) > 0
            and float(market_definition.initial_margin_parameter) < 10**18
        ), "Wrong initial margin parameter"
        assert market_definition.max_leverage > 0 and market_definition.max_leverage < 10**18, "Wrong max leverage"
        assert float(market_definition.oi_cap) > 0 and market_definition.max_leverage < 10**18, "Wrong oi cap"
        assert market_definition.symbol == symbol
    except Exception as e:
        logger.error(f"Error in test_market_definition: {e}")
        raise
    finally:
        if reya_tester.websocket:
            reya_tester.websocket.close()


@pytest.mark.asyncio
async def test_market_definitions(reya_tester: ReyaTester):
    """Test getting all market definitions."""

    try:
        market_definitions = reya_tester.client.reference.get_market_definitions()
        assert market_definitions is not None
        assert len(market_definitions) > 0
    except Exception as e:
        logger.error(f"Error in test_market_definitions: {e}")
        raise


@pytest.mark.asyncio
async def test_market_price(reya_tester: ReyaTester):
    """Test getting price for a specific symbol."""
    symbol = "ETHRUSDPERP"

    try:
        price = reya_tester.client.markets.get_price(symbol)
        assert price is not None
        assert price.symbol == symbol
        assert float(price.oracle_price) > 0 and float(price.oracle_price) < 10**18
        assert float(price.pool_price) > 0 and float(price.pool_price) < 10**18
        current_time = int(time.time())
        assert price.updated_at / 1000 > current_time - 60
    except Exception as e:
        logger.error(f"Error in test_market_price: {e}")
        raise


@pytest.mark.asyncio
async def test_market_summary(reya_tester: ReyaTester):
    """Test getting market summary for a specific symbol."""
    symbol = "ETHRUSDPERP"

    try:
        market_summary = reya_tester.client.markets.get_market_summary(symbol)
        assert market_summary is not None
        assert market_summary.symbol == symbol
        assert float(market_summary.oi_qty) >= 0, "OI quantity should be a valid number"
        assert float(market_summary.long_oi_qty) >= 0, "Long OI quantity should be a valid number"
        assert float(market_summary.short_oi_qty) >= 0, "Short OI quantity should be a valid number"
        assert float(market_summary.funding_rate) < 10**3 and float(market_summary.funding_rate) > -(
            10**3
        ), "Funding rate should be a valid number"

        # assert market_summary.long_funding_value, "Long funding value should be a valid number"
        # assert market_summary.short_funding_value, "Short funding value should be a valid number"
        # assert market_summary.funding_rate_velocity "Funding rate velocity should be a valid number"

        assert float(market_summary.volume24h) >= 0, "Volume 24h should be a valid number"
        assert market_summary.px_change24h.replace(".", "", 1).lstrip("-").isdigit(), "Price change 24h should be a valid number"

        assert market_summary.updated_at / 1000 > time.time() - 86400 * 2, "Updated timestamp should be valid"
        assert float(market_summary.throttled_pool_price) > 0, "Pool price should be positive"
        assert float(market_summary.throttled_oracle_price) > 0, "Oracle price should be positive"
        assert (
            market_summary.prices_updated_at / 1000 > time.time() - 86400 * 2
        ), "Prices updated timestamp should be valid"
    except Exception as e:
        logger.error(f"Error in test_market_summary: {e}")
        raise


@pytest.mark.asyncio
async def test_markets_summary(reya_tester: ReyaTester):
    """Test getting summary for all markets."""

    try:
        markets_summary = reya_tester.client.markets.get_markets_summary()
        assert markets_summary is not None
        assert len(markets_summary) > 0
    except Exception as e:
        logger.error(f"Error in test_markets_summary: {e}")
        raise


@pytest.mark.asyncio
async def test_candles(reya_tester: ReyaTester):
    """Test getting candle data for a specific symbol."""
    symbol = "ETHRUSDPERP"

    for resolution in ["1m", "5m", "15m", "1h", "4h", "1d"]:
        logger.info(f"Testing resolution: {resolution}")
        candles_count = 200
        resolution_in_seconds = 60 if resolution == "1m" else 60*5 if resolution == "5m" else 60*15 if resolution == "15m" \
            else 60*60 if resolution == "1h" else 60*60*4 if resolution == "4h" else 60*60*24
        try:
            current_time = int(time.time() * 1000)
            candles = reya_tester.client.markets.get_candles(symbol=symbol, resolution=resolution, end_time=current_time)
            assert candles is not None
            assert len(candles.t) == candles_count
            assert len(candles.c) == candles_count
            assert len(candles.o) == candles_count
            assert len(candles.h) == candles_count
            assert len(candles.l) == candles_count
            for t in range(candles_count):
                assert candles.t[t]//(resolution_in_seconds) == (current_time/1000 - resolution_in_seconds*candles_count + resolution_in_seconds*t)//(resolution_in_seconds)
        except Exception as e:
            logger.error(f"Error in test_candles: {e}")
            raise


# TODO: enhance
@pytest.mark.asyncio
async def test_market_perp_executions(reya_tester: ReyaTester):
    """Test getting perp executions for a specific market."""
    symbol = "ETHRUSDPERP"

    try:
        executions = reya_tester.client.markets.get_market_perp_executions(symbol)
        assert executions is not None
        assert len(executions.data) > 0
        assert executions.data[0].symbol == symbol
    except Exception as e:
        logger.error(f"Error in test_market_perp_executions: {e}")
        raise


# TODO: enhance
@pytest.mark.asyncio
async def test_asset_definitions(reya_tester: ReyaTester):
    """Test getting asset definitions."""

    try:
        assets = reya_tester.client.reference.get_asset_definitions()
        assert assets is not None
        assert len(assets) > 0
        # Check for common assets in the symbols/tokens
        tokens = {}
        for asset in assets:
            tokens[asset.spot_market_symbol] = asset

        assert "ETHRUSD" in tokens.keys()
        assert "SRUSDRUSD" in tokens.keys()
    except Exception as e:
        logger.error(f"Error in test_asset_definitions: {e}")
        raise


@pytest.mark.asyncio
async def test_fee_tier_parameters(reya_tester: ReyaTester):
    """Test getting fee tier parameters."""

    try:
        fee_tiers = reya_tester.client.reference.get_fee_tier_parameters()
        assert fee_tiers is not None
        assert len(fee_tiers) > 0
        # Check fee tier structure for common attributes
        tiers = {}
        for tier in fee_tiers:
            tiers[tier.tier_id] = tier
            assert float(tier.maker_fee) >= 0 and float(tier.maker_fee) <= 1
            assert float(tier.taker_fee) >= 0 and float(tier.taker_fee) <= 1

        assert len(tiers.keys()) > 0

    except Exception as e:
        logger.error(f"Error in test_fee_tier_parameters: {e}")
        raise


@pytest.mark.asyncio
async def test_global_fee_parameters(reya_tester: ReyaTester):
    """Test getting global fee parameters."""

    try:
        global_fees = reya_tester.client.reference.get_global_fee_parameters()
        assert global_fees is not None
        assert float(global_fees.og_discount) >= 0 and float(global_fees.og_discount) <= 1
        assert float(global_fees.referee_discount) >= 0 and float(global_fees.referee_discount) <= 1
        assert float(global_fees.referrer_rebate) >= 0 and float(global_fees.referrer_rebate) <= 1
        assert float(global_fees.affiliate_referrer_rebate) >= 0 and float(global_fees.affiliate_referrer_rebate) <= 1
    except Exception as e:
        logger.error(f"Error in test_global_fee_parameters: {e}")
        raise


# TODO: enhance
@pytest.mark.asyncio
async def test_liquidity_parameters(reya_tester: ReyaTester):
    """Test getting liquidity parameters."""

    try:
        liquidity_params = reya_tester.client.reference.get_liquidity_parameters()
        assert liquidity_params is not None

        params = {}
        for param in liquidity_params:
            params[param.symbol] = param

        assert len(params.keys()) > 0
        assert "ETHRUSDPERP" in params.keys()
    except Exception as e:
        logger.error(f"Error in test_liquidity_parameters: {e}")
        raise
