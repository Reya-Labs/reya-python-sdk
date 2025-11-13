#!/usr/bin/env python3
import re
import time

import pytest

from tests.reya_tester import ReyaTester, logger


@pytest.mark.asyncio
async def test_market_definition(reya_tester: ReyaTester):
    symbol = "ETHRUSDPERP"
    market_definition = await reya_tester.get_market_definition(symbol)
    assert market_definition is not None
    assert market_definition.market_id == 1, "Wrong market id"
    assert 0 < float(market_definition.min_order_qty) < 10**18, "Wrong min order qty"
    assert 0 < float(market_definition.qty_step_size) < 10**18, "Wrong qty step size"
    assert 0 < float(market_definition.tick_size) < 10**18, "Wrong tick size"
    assert 0 < float(market_definition.liquidation_margin_parameter) < 10**18, "Wrong liquidation margin parameter"
    assert 0 < float(market_definition.initial_margin_parameter) < 10**18, "Wrong initial margin parameter"
    assert 0 < market_definition.max_leverage < 10**18, "Wrong max leverage"
    assert 0 < float(market_definition.oi_cap) < 10**18, "Wrong oi cap"
    assert market_definition.symbol == symbol


@pytest.mark.asyncio
async def test_market_definitions(reya_tester: ReyaTester):
    market_definitions = await reya_tester.client.reference.get_market_definitions()
    assert market_definitions is not None
    assert len(market_definitions) > 0
    for market_definition in market_definitions:
        assert re.match("^[a-zA-Z0-9]*$", market_definition.symbol), "Symbol should only have alphanumeric characters"
        assert 0 < float(market_definition.min_order_qty) < 10**18, "Wrong min order qty"
        assert 0 < float(market_definition.qty_step_size) < 10**18, "Wrong qty step size"
        assert 0 < float(market_definition.tick_size) < 10**18, "Wrong tick size"
        assert 0 < float(market_definition.liquidation_margin_parameter) < 10**18, "Wrong liquidation margin parameter"
        assert 0 < float(market_definition.initial_margin_parameter) < 10**18, "Wrong initial margin parameter"
        assert 0 < market_definition.max_leverage < 10**18, "Wrong max leverage"
        assert 0 <= float(market_definition.oi_cap) < 10**18, "Wrong oi cap"


@pytest.mark.asyncio
async def test_market_price(reya_tester: ReyaTester):
    symbol = "ETHRUSDPERP"
    price = await reya_tester.client.markets.get_price(symbol)
    assert price is not None
    assert price.symbol == symbol
    assert 0 < float(price.oracle_price) < 10**18
    assert price.pool_price is not None
    assert 0 < float(price.pool_price) < 10**18
    current_time = int(time.time())
    assert price.updated_at / 1000 > current_time - 60


@pytest.mark.asyncio
async def test_all_prices(reya_tester: ReyaTester):
    prices = await reya_tester.client.markets.get_prices()
    assert prices is not None
    assert len(prices) > 0, "Should have at least one price"

    for sample_price in prices:
        assert sample_price.symbol is not None and len(sample_price.symbol) > 0, "Symbol should not be empty"
        assert 0 <= float(sample_price.oracle_price) < 10**18, "Oracle price should be a valid positive number"
        if "PERP" in sample_price.symbol:
            assert (
                0 <= float(sample_price.pool_price) < 10**18 if "PERP" in sample_price.symbol else True
            ), "Pool price should be a valid positive number"
        current_time = int(time.time() * 1000)
        assert sample_price.updated_at > current_time - (60 * 60 * 1000), "Updated timestamp should be within last hour"
        assert sample_price.updated_at <= current_time + (60 * 1000), "Updated timestamp should not be in future"
    symbols = {price.symbol for price in prices}
    assert "ETHRUSDPERP" in symbols, "Should include ETHRUSDPERP in all prices"


@pytest.mark.asyncio
async def test_market_summary(reya_tester: ReyaTester):
    symbol = "ETHRUSDPERP"
    market_summary = await reya_tester.client.markets.get_market_summary(symbol)
    assert market_summary is not None
    assert market_summary.symbol == symbol
    assert float(market_summary.oi_qty) >= 0, "OI quantity should be a valid number"
    assert float(market_summary.long_oi_qty) >= 0, "Long OI quantity should be a valid number"
    assert float(market_summary.short_oi_qty) >= 0, "Short OI quantity should be a valid number"
    assert -(10**3) < float(market_summary.funding_rate) < 10**3, "Funding rate should be a valid number"

    assert (
        market_summary.long_funding_value.replace(".", "", 1).lstrip("-").isdigit()
    ), "Long funding value should be a valid number"
    assert (
        market_summary.short_funding_value.replace(".", "", 1).lstrip("-").isdigit()
    ), "Short funding value should be a valid number"
    assert (
        market_summary.funding_rate_velocity.replace(".", "", 1).lstrip("-").isdigit()
    ), "Funding rate velocity should be a valid number"

    assert float(market_summary.volume24h) >= 0, "Volume 24h should be a valid number"
    assert market_summary.px_change24h is not None
    assert (
        market_summary.px_change24h.replace(".", "", 1).lstrip("-").isdigit()
    ), "Price change 24h should be a valid number"

    assert market_summary.updated_at / 1000 > time.time() - 86400 * 2, "Updated timestamp should be valid"
    assert market_summary.throttled_pool_price is not None
    assert float(market_summary.throttled_pool_price) > 0, "Pool price should be positive"
    assert market_summary.throttled_oracle_price is not None
    assert float(market_summary.throttled_oracle_price) > 0, "Oracle price should be positive"
    assert market_summary.prices_updated_at is not None
    assert market_summary.prices_updated_at / 1000 > time.time() - 86400 * 2, "Prices updated timestamp should be valid"


@pytest.mark.asyncio
async def test_markets_summary(reya_tester: ReyaTester):
    markets_summary = await reya_tester.client.markets.get_markets_summary()
    assert markets_summary is not None
    assert len(markets_summary) > 0


@pytest.mark.asyncio
async def test_candles(reya_tester: ReyaTester):
    symbol = "ETHRUSDPERP"

    for resolution in ["1m", "5m", "15m", "1h", "4h", "1d"]:
        logger.info(f"Testing resolution: {resolution}")
        candles_count = 200
        resolution_in_seconds = (
            60
            if resolution == "1m"
            else (
                60 * 5
                if resolution == "5m"
                else (
                    60 * 15
                    if resolution == "15m"
                    else (60 * 60 if resolution == "1h" else 60 * 60 * 4 if resolution == "4h" else 60 * 60 * 24)
                )
            )
        )
        current_time = int(time.time() * 1000)
        candles = await reya_tester.client.markets.get_candles(
            symbol=symbol, resolution=resolution, end_time=current_time
        )
        assert candles is not None
        assert len(candles.t) == candles_count
        assert len(candles.c) == candles_count
        assert len(candles.o) == candles_count
        assert len(candles.h) == candles_count
        assert len(candles.l) == candles_count
        for t in range(candles_count):
            assert candles.t[t] // resolution_in_seconds == (
                current_time / 1000 - resolution_in_seconds * candles_count + resolution_in_seconds * t
            ) // (resolution_in_seconds)


@pytest.mark.asyncio
async def test_market_perp_executions(reya_tester: ReyaTester):
    symbol = "ETHRUSDPERP"
    executions = await reya_tester.client.markets.get_market_perp_executions(symbol)
    assert executions is not None
    assert len(executions.data) > 0

    execution = executions.data[0]
    assert execution.symbol == symbol
    assert 0 < float(execution.price) < 10**7, "Price should be a valid positive number"
    assert 0 < float(execution.qty) < 10**10, "Quantity should be a valid positive number"
    assert 0 <= float(execution.fee) < 10**6, "Fee should be a valid non-negative number"
    assert execution.side in [
        "B",
        "A",
    ], f"Side should be B or A, got: {execution.side}"
    assert execution.sequence_number > 0, "Sequence number should be positive"
    assert execution.account_id > 0, "Account ID should be positive"
    assert execution.exchange_id > 0, "Exchange ID should be positive"
    current_time = int(time.time() * 1000)
    assert execution.timestamp > current_time - (30 * 24 * 60 * 60 * 1000), "Timestamp should be recent"
    assert execution.timestamp <= current_time + (60 * 1000), "Timestamp should not be in future"
    assert execution.type in [
        "ORDER_MATCH",
        "LIQUIDATION",
    ], f"Unexpected execution type: {execution.type}"


@pytest.mark.asyncio
async def test_asset_definitions(reya_tester: ReyaTester):
    assets = await reya_tester.client.reference.get_asset_definitions()
    assert assets is not None
    assert len(assets) > 0

    tokens = {}
    for asset in assets:
        tokens[asset.spot_market_symbol] = asset

        assert asset.asset is not None and len(asset.asset) > 0, "Asset symbol should not be empty"
        assert (
            asset.spot_market_symbol is not None and len(asset.spot_market_symbol) > 0
        ), "Spot market symbol should not be empty"

        assert re.match(
            "^[a-zA-Z0-9]*$", asset.spot_market_symbol
        ), "Spot market symbol should only have alphanumeric characters"
        assert re.match("^[a-zA-Z0-9]*$", asset.asset), "Asset symbol should only have alphanumeric characters"

        assert (
            0 <= float(asset.price_haircut) <= 1
        ), f"Price haircut should be between 0 and 1, got: {asset.price_haircut}"
        assert (
            0 <= float(asset.liquidation_discount) <= 1
        ), f"Liquidation discount should be between 0 and 1, got: {asset.liquidation_discount}"

        assert asset.timestamp >= 0, f"Timestamp should be non-negative, got: {asset.timestamp}"
        current_time = int(time.time() * 1000)
        if asset.timestamp > 0:
            assert asset.timestamp <= current_time + (60 * 1000), "Timestamp should not be in future"

        assert (
            "USD" in asset.spot_market_symbol
        ), f"Spot market symbol should contain USD, got: {asset.spot_market_symbol}"

    assert "ETHRUSD" in tokens, "ETHRUSD should be in asset definitions"
    assert "SRUSDRUSD" in tokens, "SRUSDRUSD should be in asset definitions"

    eth_asset = tokens.get("ETHRUSD")
    assert eth_asset is not None
    assert eth_asset.asset == "ETH", f"Expected ETH asset, got: {eth_asset.asset}"


@pytest.mark.asyncio
async def test_fee_tier_parameters(reya_tester: ReyaTester):
    fee_tiers = await reya_tester.client.reference.get_fee_tier_parameters()
    assert fee_tiers is not None
    assert len(fee_tiers) > 0
    # Check fee tier structure for common attributes
    tiers = {}
    for tier in fee_tiers:
        tiers[tier.tier_id] = tier
        assert 0 <= float(tier.maker_fee) <= 1
        assert 0 <= float(tier.taker_fee) <= 1
        assert tier.tier_type in ["REGULAR", "VIP"]

    assert len(tiers.keys()) > 0


@pytest.mark.asyncio
async def test_global_fee_parameters(reya_tester: ReyaTester):
    global_fees = await reya_tester.client.reference.get_global_fee_parameters()
    assert global_fees is not None
    assert 0 <= float(global_fees.og_discount) <= 1
    assert 0 <= float(global_fees.referee_discount) <= 1
    assert 0 <= float(global_fees.referrer_rebate) <= 1
    assert 0 <= float(global_fees.affiliate_referrer_rebate) <= 1


@pytest.mark.asyncio
async def test_liquidity_parameters(reya_tester: ReyaTester):
    """Test getting liquidity parameters."""
    liquidity_params = await reya_tester.client.reference.get_liquidity_parameters()
    assert liquidity_params is not None

    params = {}
    for param in liquidity_params:
        params[param.symbol] = param

        assert param.symbol is not None and len(param.symbol) > 0, "Symbol should not be empty"
        assert "PERP" in param.symbol, f"Symbol should be a perpetual contract (contain PERP), got: {param.symbol}"

        assert 0 < float(param.depth) <= 10000, f"Depth should be positive and reasonable, got: {param.depth}"

        assert (
            0 <= float(param.velocity_multiplier) <= 50000
        ), f"Velocity multiplier should be non-negative and reasonable, got: {param.velocity_multiplier}"

    assert len(params.keys()) > 0, "Should have at least one liquidity parameter"
    assert "ETHRUSDPERP" in params, "ETHRUSDPERP should be in liquidity parameters"

    eth_param = params.get("ETHRUSDPERP")
    assert eth_param
    assert eth_param.symbol == "ETHRUSDPERP", f"Expected ETHRUSDPERP symbol, got: {eth_param.symbol}"
    assert float(eth_param.depth) > 0, f"ETH depth should be positive, got: {eth_param.depth}"
    assert (
        float(eth_param.velocity_multiplier) > 0
    ), f"ETH velocity multiplier should be positive, got: {eth_param.velocity_multiplier}"
