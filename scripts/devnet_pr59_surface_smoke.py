#!/usr/bin/env python3
"""Repeatable devnet smoke checks for externally-visible PR 59 surfaces.

The full pytest suite covers order-entry behavior well, but it does not give a
small, explicit release checklist for the market-data contract changes that
integrators will notice first. This script is read-only and bounded: it checks
the generated SDK surface, REST endpoints, and read websocket channels, then
exits.

Usage:
    poetry run python scripts/devnet_pr59_surface_smoke.py --list
    poetry run python scripts/devnet_pr59_surface_smoke.py --timeout 30
"""

from __future__ import annotations

from typing import Any

import argparse
import asyncio
import json
import os
import queue
import sys
import time
import warnings
from dataclasses import dataclass
from enum import Enum

from dotenv import load_dotenv

import sdk.open_api as rest_open_api
from sdk.async_api.asset_oracle_prices_update_payload import AssetOraclePricesUpdatePayload
from sdk.async_api.error_message_payload import ErrorMessagePayload
from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.async_api.markets_summary_update_payload import MarketsSummaryUpdatePayload
from sdk.async_api.ping_message_payload import PingMessagePayload
from sdk.async_api.prices_update_payload import PricesUpdatePayload
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.open_api.api.market_data_api import MarketDataApi
from sdk.open_api.api.reference_data_api import ReferenceDataApi
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_websocket import ReyaSocket, WebSocketMessage
from sdk.reya_websocket.config import WebSocketConfig


class SmokeCheckKind(Enum):
    REST = "REST"
    WEBSOCKET = "WEBSOCKET"
    SDK_STATIC = "SDK_STATIC"


@dataclass(frozen=True)
class SmokeCheck:
    id: str
    kind: SmokeCheckKind
    target: str
    reason: str


@dataclass(frozen=True)
class CheckResult:
    check: SmokeCheck
    passed: bool
    detail: str


SMOKE_CHECKS: tuple[SmokeCheck, ...] = (
    SmokeCheck(
        id="sdk.removedAmmSurfaces",
        kind=SmokeCheckKind.SDK_STATIC,
        target="generated SDK surface",
        reason=(
            "AMM-era SDK entry points must stay removed: /v2/markets/summary, "
            "/v2/market/{symbol}/summary, /v2/marketDefinitions, and /v2/liquidityParameters."
        ),
    ),
    SmokeCheck(
        id="rest.referenceDefinitions",
        kind=SmokeCheckKind.REST,
        target="/v2/perpMarketDefinitions + /v2/spotMarketDefinitions",
        reason="Reference-data callers moved from marketDefinitions/liquidityParameters to explicit market definitions.",
    ),
    SmokeCheck(
        id="rest.assetOraclePrices",
        kind=SmokeCheckKind.REST,
        target="/v2/assetOraclePrices",
        reason="New asset oracle REST feed replaces asset/collateral consumers of the deprecated prices endpoint.",
    ),
    SmokeCheck(
        id="ws.assetOraclePrices",
        kind=SmokeCheckKind.WEBSOCKET,
        target="/v2/assetOraclePrices",
        reason="New asset oracle read WS channel is not fully covered by the normal live SDK tests.",
    ),
    SmokeCheck(
        id="rest.perpMarketsSummary",
        kind=SmokeCheckKind.REST,
        target="/v2/perpMarkets/summary + /v2/perpMarket/{symbol}/summary",
        reason="Preferred perp summary REST paths replace the removed unprefixed perp summary aliases.",
    ),
    SmokeCheck(
        id="ws.perpMarketsSummary",
        kind=SmokeCheckKind.WEBSOCKET,
        target="/v2/perpMarkets/summary + /v2/perpMarket/{symbol}/summary",
        reason="Preferred perp summary read WS channels replace the removed unprefixed summary aliases.",
    ),
    SmokeCheck(
        id="rest.deprecatedPrices",
        kind=SmokeCheckKind.REST,
        target="/v2/prices + /v2/prices/{symbol}",
        reason="Legacy prices REST endpoints remain callable during deprecation and should emit SDK deprecation warnings.",
    ),
    SmokeCheck(
        id="ws.deprecatedPrices",
        kind=SmokeCheckKind.WEBSOCKET,
        target="/v2/prices",
        reason="Legacy prices read WS channel remains callable during deprecation while consumers migrate.",
    ),
)


_CHECKS_BY_ID = {check.id: check for check in SMOKE_CHECKS}


def _ok(check_id: str, detail: str) -> CheckResult:
    return CheckResult(_CHECKS_BY_ID[check_id], True, detail)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _assert_positive_decimal(value: Any, label: str) -> None:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f"{label} is not numeric: {value!r}") from exc
    _assert(parsed > 0, f"{label} must be positive, got {value!r}")


def _assert_recent_ms(value: int | float, label: str, max_age_ms: int) -> None:
    now_ms = int(time.time() * 1000)
    _assert(value > now_ms - max_age_ms, f"{label} is stale: {value}")
    _assert(value <= now_ms + 60_000, f"{label} is in the future: {value}")


def _describe_asset_prices(prices: list[Any], max_age_ms: int) -> str:
    _assert(prices, "asset oracle prices response was empty")
    assets: list[str] = []
    for price in prices:
        _assert(price.asset, "asset oracle price asset was empty")
        _assert_positive_decimal(price.oracle_price, f"{price.asset}.oraclePrice")
        _assert_recent_ms(price.updated_at, f"{price.asset}.updatedAt", max_age_ms=max_age_ms)
        _assert(not hasattr(price, "pool_price"), "AssetOraclePrice must not expose AMM pool_price")
        assets.append(price.asset)
    return f"{len(prices)} asset(s): {', '.join(assets[:5])}"


def _describe_perp_summaries(summaries: list[Any], symbol: str, max_age_ms: int) -> str:
    _assert(summaries, "perp markets summary response was empty")
    symbols = {summary.symbol for summary in summaries}
    _assert(symbol in symbols, f"{symbol} missing from perp markets summary")
    for summary in summaries:
        if summary.mark_price is not None:
            _assert_positive_decimal(summary.mark_price, f"{summary.symbol}.markPrice")
        if summary.throttled_mid_price is not None:
            _assert_positive_decimal(summary.throttled_mid_price, f"{summary.symbol}.throttledMidPrice")
        _assert_recent_ms(summary.updated_at, f"{summary.symbol}.updatedAt", max_age_ms=max_age_ms)
    return f"{len(summaries)} perp summary row(s), including {symbol}"


def run_static_sdk_checks() -> CheckResult:
    _assert(hasattr(rest_open_api, "AssetOraclePrice"), "REST SDK must expose AssetOraclePrice")
    _assert(not hasattr(rest_open_api, "CollateralOraclePrice"), "REST SDK must not expose CollateralOraclePrice")
    _assert(hasattr(MarketDataApi, "get_asset_oracle_prices"), "REST SDK missing get_asset_oracle_prices")
    _assert(not hasattr(MarketDataApi, "get_collateral_oracle_prices"), "old collateral oracle method still present")
    _assert(not hasattr(MarketDataApi, "get_markets_summary"), "old /v2/markets/summary method still present")
    _assert(not hasattr(MarketDataApi, "get_market_summary"), "old /v2/market/{symbol}/summary method still present")
    _assert(not hasattr(ReferenceDataApi, "get_market_definitions"), "old /v2/marketDefinitions method still present")
    _assert(
        not hasattr(ReferenceDataApi, "get_liquidity_parameters"),
        "old /v2/liquidityParameters method still present",
    )
    _assert(
        ReyaSocket.CHANNEL_PAYLOAD_MAP["/v2/assetOraclePrices"] is AssetOraclePricesUpdatePayload,
        "read WS asset oracle channel is not routed to AssetOraclePricesUpdatePayload",
    )
    _assert("/v2/markets/summary" not in ReyaSocket.CHANNEL_PAYLOAD_MAP, "old summary WS channel still routed")
    return _ok("sdk.removedAmmSurfaces", "removed aliases absent; asset oracle SDK surface present")


async def run_rest_checks(client: ReyaTradingClient, max_age_ms: int) -> tuple[list[CheckResult], str]:
    perp_definitions = await client.reference.get_perp_market_definitions()
    spot_definitions = await client.reference.get_spot_market_definitions()
    _assert(perp_definitions, "perp market definitions response was empty")
    _assert(spot_definitions, "spot market definitions response was empty")

    perp_symbol = (
        "ETHRUSDPERP" if any(m.symbol == "ETHRUSDPERP" for m in perp_definitions) else perp_definitions[0].symbol
    )
    results = [
        _ok(
            "rest.referenceDefinitions",
            f"{len(perp_definitions)} perp definition(s), {len(spot_definitions)} spot definition(s)",
        )
    ]

    asset_prices = await client.markets.get_asset_oracle_prices()
    results.append(_ok("rest.assetOraclePrices", _describe_asset_prices(asset_prices, max_age_ms=max_age_ms)))

    summaries = await client.markets.get_perp_markets_summary()
    summary = await client.markets.get_perp_market_summary(perp_symbol)
    _assert(summary.symbol == perp_symbol, f"single perp summary returned {summary.symbol}, expected {perp_symbol}")
    if summary.mark_price is not None:
        _assert_positive_decimal(summary.mark_price, f"{perp_symbol}.markPrice")
    results.append(_ok("rest.perpMarketsSummary", _describe_perp_summaries(summaries, perp_symbol, max_age_ms)))

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always", DeprecationWarning)
        legacy_prices = await client.markets.get_prices()
        legacy_price = await client.markets.get_price(perp_symbol)
    _assert(legacy_prices, "deprecated /prices response was empty")
    _assert(legacy_price.symbol == perp_symbol, f"deprecated /prices/{{symbol}} returned {legacy_price.symbol}")
    deprecation_messages = [str(warning.message) for warning in captured if warning.category is DeprecationWarning]
    _assert(
        any("/prices" in message for message in deprecation_messages), "missing SDK deprecation warning for /prices"
    )
    results.append(
        _ok(
            "rest.deprecatedPrices",
            f"{len(legacy_prices)} legacy price row(s); {perp_symbol} single-price still callable with warning",
        )
    )

    return results, perp_symbol


def _extract_contents_data(contents: dict[str, Any] | None) -> Any:
    if not contents:
        return None
    if "data" in contents:
        return contents["data"]
    return contents


def _validate_ws_data(channel: str, data: Any, max_age_ms: int) -> str:
    if channel == "/v2/assetOraclePrices":
        if isinstance(data, list) and data and isinstance(data[0], dict):
            payload = AssetOraclePricesUpdatePayload.model_validate(
                {"type": "channel_data", "timestamp": time.time() * 1000, "channel": channel, "data": data}
            )
        elif isinstance(data, AssetOraclePricesUpdatePayload):
            payload = data
        else:
            raise AssertionError(f"unexpected asset oracle WS payload shape: {type(data).__name__}")
        return _describe_asset_prices(payload.data, max_age_ms=max_age_ms)

    if channel == "/v2/perpMarkets/summary":
        if isinstance(data, list) and data and isinstance(data[0], dict):
            payload = MarketsSummaryUpdatePayload.model_validate(
                {"type": "channel_data", "timestamp": time.time() * 1000, "channel": channel, "data": data}
            )
        elif isinstance(data, MarketsSummaryUpdatePayload):
            payload = data
        else:
            raise AssertionError(f"unexpected perp markets summary WS payload shape: {type(data).__name__}")
        symbol = payload.data[0].symbol
        return _describe_perp_summaries(payload.data, symbol, max_age_ms=max_age_ms)

    if channel.startswith("/v2/perpMarket/") and channel.endswith("/summary"):
        if isinstance(data, dict):
            payload = MarketSummaryUpdatePayload.model_validate(
                {"type": "channel_data", "timestamp": time.time() * 1000, "channel": channel, "data": data}
            )
        elif isinstance(data, MarketSummaryUpdatePayload):
            payload = data
        else:
            raise AssertionError(f"unexpected perp market summary WS payload shape: {type(data).__name__}")
        _assert(payload.data.symbol in channel, f"{channel} returned {payload.data.symbol}")
        if payload.data.mark_price is not None:
            _assert_positive_decimal(payload.data.mark_price, f"{payload.data.symbol}.markPrice")
        return f"{payload.data.symbol} summary parsed"

    if channel == "/v2/prices":
        if isinstance(data, list) and data and isinstance(data[0], dict):
            payload = PricesUpdatePayload.model_validate(
                {"type": "channel_data", "timestamp": time.time() * 1000, "channel": channel, "data": data}
            )
        elif isinstance(data, PricesUpdatePayload):
            payload = data
        else:
            raise AssertionError(f"unexpected legacy prices WS payload shape: {type(data).__name__}")
        _assert(payload.data, "legacy prices WS data was empty")
        return f"{len(payload.data)} legacy price row(s)"

    raise AssertionError(f"no validator for channel {channel}")


def run_websocket_checks(symbol: str, timeout_s: float, max_age_ms: int) -> list[CheckResult]:
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
    expected_channels = {
        "/v2/assetOraclePrices": "ws.assetOraclePrices",
        "/v2/perpMarkets/summary": "ws.perpMarketsSummary",
        f"/v2/perpMarket/{symbol}/summary": "ws.perpMarketsSummary",
        "/v2/prices": "ws.deprecatedPrices",
    }
    details_by_check: dict[str, list[str]] = {check_id: [] for check_id in expected_channels.values()}
    seen_channels: set[str] = set()
    errors: queue.Queue[str] = queue.Queue()
    done = queue.Queue()

    def on_open(ws: ReyaSocket) -> None:
        ws.prices.asset_oracle_prices.subscribe()
        ws.market.all_markets_summary.subscribe()
        ws.market.summary(symbol).subscribe()
        ws.prices.all_prices.subscribe()

    def on_error(_ws: ReyaSocket, error: Exception) -> None:
        errors.put(str(error))

    def on_message(ws: ReyaSocket, message: WebSocketMessage) -> None:
        try:
            if isinstance(message, PingMessagePayload):
                ws.send(json.dumps({"type": "pong"}))
                return
            if isinstance(message, ErrorMessagePayload):
                errors.put(message.message)
                return
            if isinstance(message, SubscribedMessagePayload):
                data = _extract_contents_data(message.contents)
                if data is not None and message.channel in expected_channels:
                    detail = _validate_ws_data(message.channel, data, max_age_ms=max_age_ms)
                    seen_channels.add(message.channel)
                    details_by_check[expected_channels[message.channel]].append(detail)
                    if expected_channels.keys() <= seen_channels:
                        done.put(True)
                return
            channel = getattr(message, "channel", None)
            if channel in expected_channels:
                detail = _validate_ws_data(channel, message, max_age_ms=max_age_ms)
                seen_channels.add(channel)
                details_by_check[expected_channels[channel]].append(detail)
                if expected_channels.keys() <= seen_channels:
                    done.put(True)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.put(f"{type(exc).__name__}: {exc}")

    config = WebSocketConfig(
        url=ws_url,
        ping_interval=20,
        ping_timeout=15,
        connection_timeout=max(5, int(timeout_s)),
    )
    ws = ReyaSocket(config=config, on_open=on_open, on_message=on_message, on_error=on_error)
    ws.connect()

    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            if not errors.empty():
                raise AssertionError(errors.get())
            if not done.empty():
                break
            time.sleep(0.1)
    finally:
        ws.close()
        if ws._thread is not None:  # pylint: disable=protected-access
            ws._thread.join(timeout=2)

    missing = set(expected_channels) - seen_channels
    _assert(not missing, f"timed out waiting for WS data from: {sorted(missing)}")

    return [
        _ok("ws.assetOraclePrices", "; ".join(details_by_check["ws.assetOraclePrices"])),
        _ok("ws.perpMarketsSummary", "; ".join(details_by_check["ws.perpMarketsSummary"])),
        _ok("ws.deprecatedPrices", "; ".join(details_by_check["ws.deprecatedPrices"])),
    ]


async def run_smoke(timeout_s: float, max_age_ms: int) -> list[CheckResult]:
    load_dotenv()
    results = [run_static_sdk_checks()]
    async with ReyaTradingClient() as client:
        rest_results, symbol = await run_rest_checks(client, max_age_ms=max_age_ms)
        results.extend(rest_results)
    results.extend(run_websocket_checks(symbol=symbol, timeout_s=timeout_s, max_age_ms=max_age_ms))
    return results


def print_smoke_plan() -> None:
    for check in SMOKE_CHECKS:
        print(f"{check.id} [{check.kind.value}] {check.target}")
        print(f"  {check.reason}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="Print the smoke-plan checklist and exit.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Websocket wait timeout in seconds.")
    parser.add_argument(
        "--max-age-ms",
        type=int,
        default=60 * 60 * 1000,
        help="Maximum acceptable age for price/summary timestamps.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list:
        print_smoke_plan()
        return 0

    try:
        results = asyncio.run(run_smoke(timeout_s=args.timeout, max_age_ms=args.max_age_ms))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PR 59 devnet surface smoke checks:")
    for result in results:
        print(f"PASS {result.check.id}: {result.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
