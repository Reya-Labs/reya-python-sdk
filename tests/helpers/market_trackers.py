"""
Raw HTTP helper for fetching market tracker data from the legacy v1 API.

@audit: This uses the legacy /api/trading/market/:marketId/trackers endpoint
which is NOT part of the v2 API surface supported by the Python SDK.
The intention is to deprecate this endpoint. It is included here because
the UI still relies on it, and testing it validates on-chain dynamic pricing
fields (logPriceMultiplier, priceSpread, depthFactor) that are critical
for execution price calculations.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import aiohttp

logger = logging.getLogger("reya.integration_tests")


@dataclass
class PriceData:
    """Parsed price data from the v2 prices endpoint."""

    oracle_price: float
    pool_price: Optional[float]


@dataclass
class MarketTrackerData:
    """Parsed market tracker data relevant to dynamic pricing tests."""

    market_id: int
    log_price_multiplier: Decimal
    price_spread: Decimal
    depth_factor: Decimal
    open_interest: Decimal


def _get_v1_base_url(v2_api_url: str) -> str:
    """Derive the v1 API base URL from the v2 API URL.

    The SDK's api_url points to e.g. https://api.reya.xyz/v2.
    The v1 trading routes are mounted at /api/trading on the same host.
    """
    # Strip /v2 suffix to get the host base
    if v2_api_url.endswith("/v2"):
        return v2_api_url[: -len("/v2")]
    if v2_api_url.endswith("/v2/"):
        return v2_api_url[: -len("/v2/")]
    return v2_api_url


async def fetch_market_trackers(
    v2_api_url: str,
    market_id: int,
    timeout: float = 10.0,
) -> MarketTrackerData:
    """Fetch market tracker data via raw HTTP from the legacy v1 endpoint.

    @audit: Legacy endpoint — /api/trading/market/:marketId/trackers.
    Not part of v2 API. Intention is to deprecate. UI still relies on it.

    Args:
        v2_api_url: The v2 API URL from the SDK config (e.g. https://api.reya.xyz/v2).
        market_id: The numeric market ID (e.g. 1 for ETHRUSDPERP).
        timeout: Request timeout in seconds.

    Returns:
        MarketTrackerData with parsed dynamic pricing fields.

    Raises:
        RuntimeError: If the request fails or response is missing expected fields.
    """
    base_url = _get_v1_base_url(v2_api_url)
    url = f"{base_url}/api/trading/market/{market_id}/trackers"

    logger.info(f"Fetching market trackers: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Market trackers request failed ({response.status}): {text}")

            data = await response.json()

    # Validate required fields exist
    required_fields = ["log_price_multiplier", "price_spread", "depth_factor", "open_interest"]
    for field in required_fields:
        if field not in data:
            raise RuntimeError(f"Market trackers response missing field: {field}")

    tracker = MarketTrackerData(
        market_id=market_id,
        log_price_multiplier=Decimal(str(data["log_price_multiplier"])),
        price_spread=Decimal(str(data["price_spread"])),
        depth_factor=Decimal(str(data["depth_factor"])),
        open_interest=Decimal(str(data["open_interest"])),
    )

    logger.info(
        f"Market trackers for market {market_id}: "
        f"logF={tracker.log_price_multiplier}, "
        f"spread={tracker.price_spread}, "
        f"depth={tracker.depth_factor}"
    )

    return tracker


async def fetch_price(
    v2_api_url: str,
    symbol: str,
    timeout: float = 10.0,
) -> PriceData:
    """Fetch price data via raw HTTP from the v2 prices endpoint.

    This avoids needing a full SDK client / reya_tester session.

    Args:
        v2_api_url: The v2 API URL (e.g. https://api-cronos.reya.xyz/v2).
        symbol: Trading symbol (e.g. ETHRUSDPERP).
        timeout: Request timeout in seconds.

    Returns:
        PriceData with oracle_price and pool_price.
    """
    # Ensure base URL ends without trailing slash
    base = v2_api_url.rstrip("/")
    url = f"{base}/prices/{symbol}"

    logger.info(f"Fetching price: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Price request failed ({response.status}): {text}")

            data = await response.json()

    # API returns camelCase keys
    oracle_price = float(data.get("oraclePrice") or data.get("oracle_price"))
    raw_pool = data.get("poolPrice") or data.get("pool_price")
    pool_price = float(raw_pool) if raw_pool is not None else None

    logger.info(f"Price for {symbol}: oracle={oracle_price}, pool={pool_price}")

    return PriceData(oracle_price=oracle_price, pool_price=pool_price)
