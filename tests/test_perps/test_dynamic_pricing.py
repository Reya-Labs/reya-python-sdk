"""
End-to-end tests for dynamic pricing (exponential logPriceMultiplier formula).

Validates that the off-chain API correctly reflects on-chain dynamic pricing
state, and that execution prices are consistent with the pool price and spread.

Tests are organized in three levels:
  - Level 1: Read-only sanity checks (no trades)
  - Level 2: Execution price tests (min-size IOC trades)
  - Level 3: Behavioral tests (direction-only assertions)
"""

import asyncio
import math
import os

import pytest
from dotenv import load_dotenv

from sdk.open_api.models.price import Price
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import MAINNET_CHAIN_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.market_trackers import fetch_market_trackers, fetch_price
from tests.helpers.reya_tester import limit_order_params_to_order, logger

SYMBOL = "ETHRUSDPERP"
TRADE_QTY = "0.01"
# ETH market ID — used for the legacy v1 trackers endpoint
ETH_MARKET_ID = 1


def _get_api_url() -> str:
    """Resolve API URL from env — no account needed."""
    load_dotenv()
    chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))
    if chain_id == MAINNET_CHAIN_ID:
        default = "https://api.reya.xyz/v2"
    else:
        default = "https://api-cronos.reya.xyz/v2"
    return os.environ.get("REYA_API_URL", default)


# ============================================================================
# Helpers
# ============================================================================


async def _get_price_and_trackers(api_url: str):
    """Fetch both price and raw market trackers in parallel via raw HTTP."""
    price_info, trackers = await asyncio.gather(
        fetch_price(api_url, SYMBOL),
        fetch_market_trackers(api_url, ETH_MARKET_ID),
    )
    return price_info, trackers


async def _execute_ioc_trade(reya_tester: ReyaTester, is_buy: bool):
    """Execute a minimum-size IOC trade and return the PerpExecution."""
    market_price = await reya_tester.data.current_price(SYMBOL)
    # Set limit price with 10% buffer to ensure IOC fills
    limit_px = str(float(market_price) * (1.1 if is_buy else 0.9))

    order_params = LimitOrderParameters(
        symbol=SYMBOL,
        is_buy=is_buy,
        limit_px=limit_px,
        qty=TRADE_QTY,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.orders.create_limit(order_params)
    expected_order = limit_order_params_to_order(order_params, reya_tester.account_id)
    execution = await reya_tester.wait.for_order_execution(expected_order)
    return execution


# ============================================================================
# Level 1: Read-Only Sanity (no trades)
# ============================================================================


class TestDynamicPricingReadOnly:
    """Read-only tests that validate pricing data without executing trades."""

    @pytest.mark.asyncio
    async def test_market_tracker_fields_exist_and_valid(self):
        """T1.1 — Market tracker fields exist and are valid.

        Verifies that the legacy v1 trackers endpoint returns the dynamic pricing
        fields (logPriceMultiplier, priceSpread, depthFactor) with valid values.
        """
        api_url = _get_api_url()
        trackers = await fetch_market_trackers(api_url, ETH_MARKET_ID)

        # depth_factor must be non-negative (drives price impact scaling; 0 on unconfigured markets)
        assert trackers.depth_factor >= 0, (
            f"depth_factor should be >= 0, got {trackers.depth_factor}"
        )

        # price_spread must be non-negative (symmetric spread around pool price)
        assert trackers.price_spread >= 0, (
            f"price_spread should be >= 0, got {trackers.price_spread}"
        )

        # log_price_multiplier is a valid signed number (can be 0, positive, or negative)
        # Just verify it's finite and within a reasonable range (< 1e18 in magnitude)
        assert trackers.log_price_multiplier.is_finite(), (
            f"log_price_multiplier should be finite, got {trackers.log_price_multiplier}"
        )

        logger.info(
            f"✅ T1.1 passed: depth_factor={trackers.depth_factor}, "
            f"price_spread={trackers.price_spread}, "
            f"log_price_multiplier={trackers.log_price_multiplier}"
        )

    @pytest.mark.asyncio
    async def test_pool_price_matches_formula(self):
        """T1.2 — Pool price ≈ oraclePrice × exp(logPriceMultiplier).

        Verifies the fundamental relationship: poolPrice = oraclePrice * exp(logF / 1e18).
        Uses ~1% tolerance to account for logF changing between API calls.
        """
        api_url = _get_api_url()
        price_info, trackers = await _get_price_and_trackers(api_url)

        assert price_info.pool_price is not None, "pool_price should be available for ETHRUSDPERP"
        oracle_price = price_info.oracle_price
        pool_price = price_info.pool_price
        log_f = float(trackers.log_price_multiplier)

        # Pool price formula: oraclePrice * exp(logF / 1e18)
        expected_pool_price = oracle_price * math.exp(log_f / 1e18)

        # Allow ~1% tolerance (logF can change between the two API calls)
        tolerance = 0.01
        relative_error = abs(pool_price - expected_pool_price) / expected_pool_price

        assert relative_error < tolerance, (
            f"Pool price {pool_price} doesn't match formula "
            f"oracle({oracle_price}) * exp(logF({log_f}) / 1e18) = {expected_pool_price}. "
            f"Relative error: {relative_error:.4f} (tolerance: {tolerance})"
        )

        logger.info(
            f"✅ T1.2 passed: poolPrice={pool_price:.2f}, "
            f"expected={expected_pool_price:.2f}, "
            f"relError={relative_error:.6f}"
        )

    @pytest.mark.asyncio
    async def test_pool_price_direction_consistency(self):
        """T1.3 — Pool price direction is consistent with logPriceMultiplier sign.

        If logF > 0 → poolPrice > oraclePrice
        If logF < 0 → poolPrice < oraclePrice
        If logF ≈ 0 → poolPrice ≈ oraclePrice
        """
        api_url = _get_api_url()
        price_info, trackers = await _get_price_and_trackers(api_url)

        assert price_info.pool_price is not None, "pool_price should be available for ETHRUSDPERP"
        oracle_price = price_info.oracle_price
        pool_price = price_info.pool_price
        log_f = float(trackers.log_price_multiplier)

        # Use a small threshold to define "approximately zero"
        # logF is in 18-decimal fixed point, so 1e14 ≈ 0.0001 in normalized terms
        zero_threshold = 1e14

        if log_f > zero_threshold:
            assert pool_price > oracle_price, (
                f"logF > 0 ({log_f}) but poolPrice ({pool_price}) <= oraclePrice ({oracle_price})"
            )
            logger.info(f"✅ T1.3: logF > 0 → poolPrice > oraclePrice")
        elif log_f < -zero_threshold:
            assert pool_price < oracle_price, (
                f"logF < 0 ({log_f}) but poolPrice ({pool_price}) >= oraclePrice ({oracle_price})"
            )
            logger.info(f"✅ T1.3: logF < 0 → poolPrice < oraclePrice")
        else:
            # logF ≈ 0, pool price should be approximately equal to oracle
            relative_diff = abs(pool_price - oracle_price) / oracle_price
            assert relative_diff < 0.001, (
                f"logF ≈ 0 ({log_f}) but poolPrice ({pool_price}) differs from "
                f"oraclePrice ({oracle_price}) by {relative_diff:.6f}"
            )
            logger.info(f"✅ T1.3: logF ≈ 0 → poolPrice ≈ oraclePrice")


# ============================================================================
# Level 2: Execution Price Tests (min-size IOC trades)
# ============================================================================


class TestDynamicPricingExecution:
    """Execution price tests using minimum-size IOC trades."""

    @pytest.mark.asyncio
    async def test_long_execution_price_above_pool_price(self, reya_tester: ReyaTester):
        """T2.1 — Long trade: execution price > pool price.

        A long trade always has exec_price > poolPrice_before because:
        - logF increases during a long (avg AMM price > poolPrice_before)
        - positive spread pushes the price higher for buys

        Note: We compare to poolPrice (not oracle) because during rebalancing
        (pool net long, logF < 0), exec_price can be < oracle.
        """
        price_info: Price = await reya_tester.client.markets.get_price(SYMBOL)
        assert price_info.pool_price is not None, "pool_price should be available for ETHRUSDPERP"
        pool_price_before = float(price_info.pool_price)

        execution = await _execute_ioc_trade(reya_tester, is_buy=True)
        exec_price = float(execution.price)

        assert exec_price > pool_price_before, (
            f"Long exec_price ({exec_price}) should be > poolPrice_before ({pool_price_before})"
        )

        logger.info(
            f"✅ T2.1 passed: exec_price={exec_price:.2f} > poolPrice={pool_price_before:.2f}"
        )

    @pytest.mark.asyncio
    async def test_short_execution_price_below_pool_price(self, reya_tester: ReyaTester):
        """T2.2 — Short trade: execution price < pool price.

        A short trade always has exec_price < poolPrice_before because:
        - logF decreases during a short (avg AMM price < poolPrice_before)
        - negative spread pushes the price lower for sells
        """
        price_info: Price = await reya_tester.client.markets.get_price(SYMBOL)
        assert price_info.pool_price is not None, "pool_price should be available for ETHRUSDPERP"
        pool_price_before = float(price_info.pool_price)

        execution = await _execute_ioc_trade(reya_tester, is_buy=False)
        exec_price = float(execution.price)

        price_info_after: Price = await reya_tester.client.markets.get_price(SYMBOL)
        pool_price_after = float(price_info_after.pool_price)
        pool_price_upper = max(pool_price_before, pool_price_after)

        assert exec_price < pool_price_upper, (
            f"Short exec_price ({exec_price}) should be < poolPrice ({pool_price_upper}) "
            f"(before={pool_price_before:.6f}, after={pool_price_after:.6f})"
        )

        logger.info(
            f"✅ T2.2 passed: exec_price={exec_price:.2f} < poolPrice={pool_price_upper:.2f}"
        )

    @pytest.mark.asyncio
    async def test_execution_price_bounded_by_oracle(self, reya_tester: ReyaTester):
        """T2.3 — Execution price bounded within 5% of oracle.

        For a minimum-size trade, the execution price should not deviate more
        than 5% from the oracle price. This catches catastrophic formula bugs
        or misconfigured parameters.
        """
        market_price = await reya_tester.data.current_price(SYMBOL)
        oracle_price = float(market_price)

        execution = await _execute_ioc_trade(reya_tester, is_buy=True)
        exec_price = float(execution.price)

        lower_bound = oracle_price * 0.95
        upper_bound = oracle_price * 1.05

        assert lower_bound < exec_price < upper_bound, (
            f"Execution price {exec_price} outside 5% oracle bounds "
            f"[{lower_bound:.2f}, {upper_bound:.2f}] (oracle={oracle_price:.2f})"
        )

        logger.info(
            f"✅ T2.3 passed: exec_price={exec_price:.2f} within 5% of oracle={oracle_price:.2f}"
        )

    @pytest.mark.asyncio
    async def test_spread_floor_on_execution_price(self, reya_tester: ReyaTester):
        """T2.4 — Spread floor on execution price.

        For a small trade, the execution price should at minimum account for
        the price spread. For a long: exec_price >= poolPrice * (1 + spread/1e18).

        This is approximate for small trades where logF change is negligible.
        """
        api_url = _get_api_url()
        price_info, trackers = await _get_price_and_trackers(api_url)
        pool_price_before = float(price_info.pool_price)
        spread_normalized = float(trackers.price_spread) / 1e18

        execution = await _execute_ioc_trade(reya_tester, is_buy=True)
        exec_price = float(execution.price)

        price_info_after = await fetch_price(api_url, SYMBOL)
        pool_price_after = float(price_info_after.pool_price)
        pool_price_lower = min(pool_price_before, pool_price_after)

        # Allow 0.1% tolerance for oracle price drift between API reads and trade execution
        oracle_drift_tolerance = 0.001
        spread_floor = pool_price_lower * (1 + spread_normalized) * (1 - oracle_drift_tolerance)

        assert exec_price >= spread_floor, (
            f"Long exec_price ({exec_price}) should be >= poolPrice * (1 + spread) = {spread_floor:.2f} "
            f"(poolPrice_min={pool_price_lower:.2f}, spread={spread_normalized:.6f})"
        )

        logger.info(
            f"✅ T2.4 passed: exec_price={exec_price:.2f} >= spread_floor={spread_floor:.2f}"
        )


# ============================================================================
# Level 3: Behavioral Tests (direction-only assertions)
# ============================================================================


class TestDynamicPricingBehavior:
    """Behavioral tests with direction-only assertions for resilience to external activity."""

    @pytest.mark.asyncio
    async def test_log_price_multiplier_changes_after_trade(self, reya_tester: ReyaTester):
        """T3.1 — LogPriceMultiplier changes after a long trade.

        After executing a long IOC trade, logPriceMultiplier should increase
        (pool becomes more short → logF moves in the positive direction).

        Includes a delay to account for indexer lag.
        """
        trackers_before = await fetch_market_trackers(
            reya_tester.client.config.api_url, ETH_MARKET_ID
        )
        log_f_before = trackers_before.log_price_multiplier

        await _execute_ioc_trade(reya_tester, is_buy=True)

        # Wait for indexer to pick up the on-chain state change
        await asyncio.sleep(3.0)

        trackers_after = await fetch_market_trackers(
            reya_tester.client.config.api_url, ETH_MARKET_ID
        )
        log_f_after = trackers_after.log_price_multiplier

        assert log_f_after > log_f_before, (
            f"logPriceMultiplier should increase after long trade: "
            f"before={log_f_before}, after={log_f_after}"
        )

        logger.info(
            f"✅ T3.1 passed: logF increased from {log_f_before} to {log_f_after} "
            f"(delta={log_f_after - log_f_before})"
        )

    @pytest.mark.asyncio
    async def test_pool_price_shifts_after_trade(self, reya_tester: ReyaTester):
        """T3.2 — Pool price shifts in the expected direction after a long trade.

        After executing a long IOC trade, the pool price should increase
        (pool goes more short → logF increases → poolPrice goes up).

        Includes a delay to account for indexer lag.
        """
        price_before: Price = await reya_tester.client.markets.get_price(SYMBOL)
        assert price_before.pool_price is not None, "pool_price should be available for ETHRUSDPERP"
        ratio_before = float(price_before.pool_price) / float(price_before.oracle_price)

        await _execute_ioc_trade(reya_tester, is_buy=True)

        # Wait for indexer to pick up the on-chain state change
        await asyncio.sleep(3.0)

        price_after: Price = await reya_tester.client.markets.get_price(SYMBOL)
        assert price_after.pool_price is not None, "pool_price should be available for ETHRUSDPERP"
        ratio_after = float(price_after.pool_price) / float(price_after.oracle_price)

        assert ratio_after > ratio_before, (
            f"Pool/oracle ratio should increase after long trade (logF increases): "
            f"before={ratio_before:.8f}, after={ratio_after:.8f}"
        )

        logger.info(
            f"✅ T3.2 passed: pool/oracle ratio increased from {ratio_before:.8f} "
            f"to {ratio_after:.8f} (delta={ratio_after - ratio_before:.8f})"
        )
