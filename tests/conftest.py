"""
Pytest fixtures for Reya Python SDK integration tests.

Uses pytest-asyncio's loop_scope feature (v0.24+) to share a single event loop
across all tests in a session, enabling session-scoped async fixtures.
"""

# Load .env BEFORE importing the SDK so module-level constants in
# `sdk.reya_rest_api.config` (e.g. `REYA_DEX_ID`) pick up devnet/staging
# overrides. Imports happen at conftest load time, so calling load_dotenv
# inside fixtures would be too late.
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
import os  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from sdk.open_api.exceptions import ApiException  # noqa: E402
from sdk.open_api.models import TimeInForce  # noqa: E402
from sdk.reya_rest_api.models.orders import LimitOrderParameters  # noqa: E402
from tests.helpers import ReyaTester  # noqa: E402
from tests.helpers.reya_tester import logger  # noqa: E402
from tests.test_spot.spot_config import (  # noqa: E402
    SpotMarketConfig,
    SpotTestConfig,
    fetch_spot_market_configs,
)

# Time delay between tests
TEST_DELAY_SECONDS = 0.1

# Minimum RUSD balance requirements for SPOT tests (asset balance is dynamic per market)
MIN_RUSD_BALANCE = 15.0

# Default asset if not specified via CLI
DEFAULT_SPOT_ASSET = "ETH"


def pytest_addoption(parser):
    """Add custom command-line options for spot tests."""
    parser.addoption(
        "--spot-asset",
        action="store",
        default=DEFAULT_SPOT_ASSET,
        help="Asset to use for spot tests (e.g., ETH, BTC). Default: ETH",
    )


@pytest.fixture(scope="session")
def spot_asset(request):
    """Get the selected spot asset from CLI option."""
    return request.config.getoption("--spot-asset").upper()


@pytest_asyncio.fixture(loop_scope="session", scope="function", autouse=True)
async def rate_limit_delay():
    """
    Add a small delay after each test to avoid WAF rate limiting.

    The staging environment uses AWS WAF which can block requests if too many
    come from the same IP in a short time window. This delay helps prevent
    403 Forbidden errors during test runs.
    """
    yield
    await asyncio.sleep(TEST_DELAY_SECONDS)


# ============================================================================
# Session-Scoped Fixtures (Single connection for entire test suite)
# ============================================================================
# Using loop_scope="session" ensures all fixtures share the same event loop


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def reya_tester_session():
    """
    Session-scoped ReyaTester - ONE connection for the entire test suite.

    This dramatically reduces test time by maintaining a single WebSocket and
    API connection throughout all tests.
    """
    load_dotenv()

    tester = ReyaTester()

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip("Missing required wallet address or account ID for tests")

    logger.info("=" * 60)
    logger.info("🚀 SESSION START: Initializing single WebSocket connection")
    logger.info(f"   Wallet: {tester.owner_wallet_address}")
    logger.info(f"   Account: {tester.account_id}")
    logger.info("=" * 60)

    # setup() calls client.start() internally, no need to call it separately
    await tester.setup()

    yield tester

    # Cleanup at end of entire test session
    logger.info("=" * 60)
    logger.info("🧹 SESSION END: Closing connections")
    logger.info("=" * 60)
    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.positions.close_all(fail_if_none=False)
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during session cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def reya_tester(reya_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper that cleans state between tests.

    Reuses the session-scoped connection but ensures clean state for each test.
    """
    # Clean up any leftover positions and orders from previous test
    await reya_tester_session.positions.close_all(fail_if_none=False)
    await reya_tester_session.orders.close_all(fail_if_none=False)

    # Clear ALL WebSocket tracking state for fresh test
    reya_tester_session.ws.clear()

    yield reya_tester_session

    # Clean up positions and orders after test (connection stays open)
    await reya_tester_session.positions.close_all(fail_if_none=False)
    await reya_tester_session.orders.close_all(fail_if_none=False)


# ============================================================================
# Multi-Account Session Fixtures (Maker/Taker)
# ============================================================================


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def maker_tester_session():
    """
    Session-scoped maker account - ONE connection for entire test suite.

    Uses SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1 as the maker.
    """
    load_dotenv()

    # Maker uses Spot Account 1
    tester = ReyaTester(spot_account_number=1)

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip(
            "Missing Spot Account 1 configuration (SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1) for spot tests"
        )

    logger.info(f"🔧 SESSION: Maker account initialized: {tester.account_id}")

    # setup() calls client.start() internally
    await tester.setup()

    yield tester

    # Cleanup
    try:
        if tester.websocket:
            tester.websocket.close()
        preserve_orders = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"
        if not preserve_orders:
            await tester.orders.close_all(fail_if_none=False)
        else:
            logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping session cleanup for maker account")
        await tester.client.close()
        logger.info("✅ Maker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during maker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def taker_tester_session():
    """
    Session-scoped taker account - ONE connection for entire test suite.

    Uses SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_2, SPOT_WALLET_ADDRESS_2 as the taker.
    """
    load_dotenv()

    # Taker uses Spot Account 2
    tester = ReyaTester(spot_account_number=2)

    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip(
            "Missing Spot Account 2 configuration (SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_2, SPOT_WALLET_ADDRESS_2) for spot tests"
        )

    logger.info(f"🔧 SESSION: Taker account initialized: {tester.account_id}")

    # setup() calls client.start() internally
    await tester.setup()

    yield tester

    # Cleanup
    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Taker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during taker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def maker_tester(maker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper for maker that cleans state between tests.

    Set SPOT_PRESERVE_ACCOUNT1_ORDERS=true to skip order cleanup for SPOT_ACCOUNT_ID_1.
    This is useful when testing with external liquidity from a depth script.
    """
    preserve_orders = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)
    else:
        logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping order cleanup for maker account")
    maker_tester_session.ws.clear()

    yield maker_tester_session

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def spot_tester(maker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper for single-account spot tests.
    Uses SPOT account 1 (same as maker_tester).

    Set SPOT_PRESERVE_ACCOUNT1_ORDERS=true to skip order cleanup for SPOT_ACCOUNT_ID_1.
    This is useful when testing with external liquidity from a depth script.
    """
    preserve_orders = os.getenv("SPOT_PRESERVE_ACCOUNT1_ORDERS", "").lower() == "true"

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)
    else:
        logger.info("⚠️ SPOT_PRESERVE_ACCOUNT1_ORDERS=true: Skipping order cleanup for spot_tester")
    maker_tester_session.ws.clear()

    yield maker_tester_session

    if not preserve_orders:
        await maker_tester_session.orders.close_all(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def taker_tester(taker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Function-scoped wrapper for taker that cleans state between tests.
    """
    await taker_tester_session.orders.close_all(fail_if_none=False)
    taker_tester_session.ws.clear()

    yield taker_tester_session

    await taker_tester_session.orders.close_all(fail_if_none=False)


# ============================================================================
# Perp orderbook maker/taker fixtures
# ============================================================================
# Under perpOB every fill needs a maker and a taker. PERP_ACCOUNT_ID_1 acts as
# the maker (resting GTC orders); PERP_ACCOUNT_ID_2 acts as the taker (IOC fills).
# Tests that don't need a counterparty can keep using the single-account
# ``reya_tester`` fixture defined above.


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def perp_maker_tester_session():
    """Session-scoped perp maker — uses PERP_ACCOUNT_ID_1 (default ReyaTester credentials)."""
    load_dotenv()

    tester = ReyaTester(perp_account_number=1)
    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip(
            "Missing perp account 1 configuration "
            "(PERP_ACCOUNT_ID_1, PERP_PRIVATE_KEY_1, PERP_WALLET_ADDRESS_1) for perp tests"
        )

    logger.info(f"🔧 SESSION: Perp maker initialized: account_id={tester.account_id}")
    await tester.setup()
    yield tester

    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.positions.close_all(fail_if_none=False)
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Perp maker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during perp maker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def perp_taker_tester_session():
    """Session-scoped perp taker — uses PERP_ACCOUNT_ID_2."""
    load_dotenv()

    tester = ReyaTester(perp_account_number=2)
    if not tester.owner_wallet_address or not tester.account_id:
        pytest.skip(
            "Missing perp account 2 configuration "
            "(PERP_ACCOUNT_ID_2, PERP_PRIVATE_KEY_2, PERP_WALLET_ADDRESS_2) for perp orderbook tests"
        )

    logger.info(f"🔧 SESSION: Perp taker initialized: account_id={tester.account_id}")
    await tester.setup()
    yield tester

    try:
        if tester.websocket:
            tester.websocket.close()
        await tester.positions.close_all(fail_if_none=False)
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Perp taker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during perp taker cleanup: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def perp_flatten_between_tests(  # pylint: disable=redefined-outer-name,unused-argument
    perp_maker_tester_session, perp_taker_tester_session, perp_position_guard
):
    """Function-scoped orchestrated flatten run before & after every perp test.

    Mirrors what `perp_position_guard` does at session boundaries, but at
    per-test scope. Required because under perpOB a one-sided reduce-only IOC
    has no AMM counterparty, so the per-test `close_all` legitimately skips and
    leaves mirrored debris from a previous test that the next test may need
    cleared (most position-management tests assert on an empty starting
    position via `check.position_not_open`).

    Activated transitively through `perp_maker_tester` / `perp_taker_tester`.
    `_flatten_to_zero` short-circuits when nothing needs flattening, so the
    overhead on tests that don't accumulate debris is just two position
    queries (~100ms).
    """
    market_def = await perp_maker_tester_session.get_market_definition(PERP_GUARD_SYMBOL)
    min_qty = Decimal(str(market_def.min_order_qty))
    qty_step = Decimal(str(market_def.qty_step_size))

    await _flatten_to_zero(
        maker=perp_maker_tester_session,
        taker=perp_taker_tester_session,
        symbol=PERP_GUARD_SYMBOL,
        min_qty=min_qty,
        qty_step=qty_step,
        label="pre-test",
    )

    yield

    await _flatten_to_zero(
        maker=perp_maker_tester_session,
        taker=perp_taker_tester_session,
        symbol=PERP_GUARD_SYMBOL,
        min_qty=min_qty,
        qty_step=qty_step,
        label="post-test",
    )


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def perp_maker_tester(  # pylint: disable=redefined-outer-name,unused-argument
    perp_maker_tester_session, perp_flatten_between_tests
):
    """Function-scoped perp maker — clears orders/positions/WS state between tests.

    Requests `perp_flatten_between_tests` (transitively activates
    `perp_position_guard`) so per-test debris is orchestrated-flattened
    rather than left to a best-effort one-sided IOC.
    """
    await perp_maker_tester_session.orders.close_all(fail_if_none=False)
    await perp_maker_tester_session.positions.close_all(fail_if_none=False)
    perp_maker_tester_session.ws.clear()
    yield perp_maker_tester_session
    await perp_maker_tester_session.orders.close_all(fail_if_none=False)
    await perp_maker_tester_session.positions.close_all(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def perp_taker_tester(  # pylint: disable=redefined-outer-name,unused-argument
    perp_taker_tester_session, perp_flatten_between_tests
):
    """Function-scoped perp taker — clears orders/positions/WS state between tests.

    Requests `perp_flatten_between_tests` (transitively activates
    `perp_position_guard`) so per-test debris is orchestrated-flattened
    rather than left to a best-effort one-sided IOC.
    """
    await perp_taker_tester_session.orders.close_all(fail_if_none=False)
    await perp_taker_tester_session.positions.close_all(fail_if_none=False)
    perp_taker_tester_session.ws.clear()
    yield perp_taker_tester_session
    await perp_taker_tester_session.orders.close_all(fail_if_none=False)
    await perp_taker_tester_session.positions.close_all(fail_if_none=False)


# ============================================================================
# Perp Position Guard (session-level orchestrated flatten)
# ============================================================================
# Mirrors the `_execute_spot_transfer` + `spot_balance_guard` pattern (lines
# 445+ below) but for perp positions. Under perpOB there's no AMM counterparty,
# so a single-account reduce-only IOC has nothing to match against. Tests need
# orchestrated maker/taker close: one tester rests a GTC, the other crosses it
# with an IOC, and both positions move consistently.


async def _get_perp_position_qty(tester: ReyaTester, symbol: str) -> Decimal:
    """Return the signed position size (+ long, − short) on `symbol`, or 0."""
    pos = await tester.data.position(symbol)
    if pos is None or pos.qty is None:
        return Decimal("0")
    qty = Decimal(str(pos.qty))
    # Sides on the API: 'B' = Bid/long (positive), 'A' = Ask/short (negative).
    return qty if str(pos.side) in ("Side.B", "B") else -qty


async def _execute_perp_flatten(
    side_a: ReyaTester,
    side_b: ReyaTester,
    symbol: str,
    qty: str,
    price: str,
    side_a_is_buy: bool,
) -> bool:
    """Cross a GTC on one side with a matching IOC on the other to move
    both testers' positions by ±qty in opposite directions.

    Mirrors `_execute_spot_transfer`:
    - `side_a` (with `side_a_is_buy=True/False`) places a GTC.
    - `side_b` places the opposite-direction IOC at the same price.
    - On match, side_a moves +qty (if buy) / -qty (if sell), side_b mirrors.

    Used by `perp_position_guard` to converge accumulated positions back
    toward zero at session end. For mirrored positions (5: -X, 6: +X — the
    common case after a maker/taker test) this restores both to zero.
    For unmirrored residue (e.g. accumulated drift), it reduces by qty.
    """
    # API rejects `reduceOnly` on GTC (only valid for IOC and TP/SL). The
    # GTC is left as a regular limit; it's reduce-by-construction because the
    # caller passed `qty <= |side_a position|`. The IOC sets reduce_only=True
    # because the API requires it on perp IOCs.
    gtc_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=side_a_is_buy,
        limit_px=price,
        qty=qty,
        time_in_force=TimeInForce.GTC,
    )
    gtc_response = await side_a.client.create_limit_order(gtc_params)
    gtc_order_id = gtc_response.order_id
    if not gtc_order_id:
        logger.error("perp_flatten: GTC creation returned no order_id")
        return False

    await asyncio.sleep(0.3)

    ioc_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=not side_a_is_buy,
        limit_px=price,
        qty=qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=True,
    )
    await side_b.client.create_limit_order(ioc_params)

    await asyncio.sleep(1.0)

    # Cancel any unmatched residual on the GTC side (shouldn't happen if the
    # IOC fully crossed, but defensive).
    try:
        open_orders = await side_a.client.get_open_orders()
        for order in open_orders:
            if hasattr(order, "order_id") and order.order_id == gtc_order_id:
                await side_a.client.cancel_order(
                    order_id=gtc_order_id,
                    symbol=symbol,
                    account_id=side_a.account_id,
                )
                break
    except (OSError, RuntimeError) as e:  # nosec B110
        logger.debug(f"perp_flatten: GTC cancel failed (may have been fully filled): {e}")

    return True


PERP_GUARD_SYMBOL = "ETHRUSDPERP"


async def _flatten_to_zero(
    maker: ReyaTester,
    taker: ReyaTester,
    symbol: str,
    min_qty: Decimal,
    qty_step: Decimal,
    label: str,
) -> None:
    """Drive both accounts' positions on `symbol` toward zero via an
    orchestrated maker↔taker cross.

    Crosses at oracle, sized to `min(|maker_qty|, |taker_qty|)` — the
    overlap that can be flattened with a single peer-to-peer match.
    Unmirrored excess (one side has more than the other) is logged but
    not auto-corrected, since closing it would require a third
    counterparty that we don't orchestrate.

    `label` is just for log readability ("session start" / "session end").
    """
    maker_qty = await _get_perp_position_qty(maker, symbol)
    taker_qty = await _get_perp_position_qty(taker, symbol)
    logger.info(f"  [{label}] maker (account {maker.account_id}): {maker_qty} {symbol}")
    logger.info(f"  [{label}] taker (account {taker.account_id}): {taker_qty} {symbol}")

    # Peer-to-peer flatten only works when one side is long and the other
    # is short — only then can the cross reduce both positions simultaneously.
    # Same-sign or zero-on-one-side cases need an external counterparty we
    # don't orchestrate; skip them.
    if maker_qty == 0 or taker_qty == 0:
        logger.info(f"  [{label}] one side already flat; nothing to cross")
        return
    if (maker_qty > 0) == (taker_qty > 0):
        logger.warning(
            f"  [{label}] both accounts on the same side "
            f"(maker {maker_qty:+}, taker {taker_qty:+}); cannot orchestrate flatten"
        )
        return

    flatten_qty_raw = min(abs(maker_qty), abs(taker_qty))
    if flatten_qty_raw < min_qty:
        logger.info(f"  [{label}] overlap below min_qty; nothing to cross")
        return
    flatten_qty = flatten_qty_raw.quantize(qty_step)

    if maker_qty + taker_qty != Decimal("0"):
        logger.warning(
            f"  [{label}] positions not perfectly mirrored "
            f"(sum {maker_qty + taker_qty:+}); flattening overlap of {flatten_qty}"
        )

    # Long side places GTC SELL; short side crosses with IOC BUY (reduce_only).
    if maker_qty > 0:
        side_a, side_b = maker, taker
    else:
        side_a, side_b = taker, maker

    oracle_price = Decimal(str(await maker.data.current_price(symbol)))
    cross_price = oracle_price.quantize(Decimal("0.01"))

    logger.info(
        f"  [{label}] flatten: account {side_a.account_id} GTC SELL {flatten_qty} @ ${cross_price}, "
        f"account {side_b.account_id} IOC BUY"
    )

    try:
        ok = await _execute_perp_flatten(
            side_a=side_a,
            side_b=side_b,
            symbol=symbol,
            qty=str(flatten_qty),
            price=str(cross_price),
            side_a_is_buy=False,
        )
        if ok:
            logger.info(f"  [{label}] ✅ flatten completed")
        else:
            logger.warning(f"  [{label}] flatten did not complete cleanly")
    except (ApiException, OSError, RuntimeError) as e:
        logger.warning(f"  [{label}] flatten threw {type(e).__name__}: {e}")


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def perp_position_guard(  # pylint: disable=redefined-outer-name
    perp_maker_tester_session, perp_taker_tester_session
):
    """Session-scoped guard that drives both perp accounts' positions to
    zero at session start AND session end via orchestrated maker↔taker
    crosses.

    Inspired by `spot_balance_guard` but with a perp-specific baseline:
    spot wants to *preserve* asset balances (delta-restoration), perps
    want positions=0 (since open positions accrue funding and bear
    market risk between runs). So this guard flattens to zero rather
    than restoring deltas.

    Opt-in via `perp_maker_tester` / `perp_taker_tester` (the function-
    scoped wrappers), which transitively request this guard. Spot-only
    sessions don't trigger it, so missing PERP_ACCOUNT_ID_* env vars
    don't tank the suite.

    Cleanup uses orchestrated GTC/IOC pairs because perpOB has no AMM
    counterparty for one-sided closes. Mirrored debris (maker -X, taker
    +X — the common case after maker/taker fills) flattens cleanly.
    Unmirrored excess is logged but not corrected.

    Running at BOTH start and end is intentional: end cleanup may not
    run if the session crashes, so the start sweep guarantees that
    every run begins from a known-clean state regardless of how the
    previous run terminated.
    """
    symbol = PERP_GUARD_SYMBOL

    # Pull market metadata from the maker tester so we don't depend on the
    # perp_market_config fixture (which lives in tests/test_orderbook/conftest).
    market_def = await perp_maker_tester_session.get_market_definition(symbol)
    min_qty = Decimal(str(market_def.min_order_qty))
    qty_step = Decimal(str(market_def.qty_step_size))

    logger.info("=" * 60)
    logger.info(f"📍 PERP POSITION GUARD: pre-session flatten of {symbol}")
    logger.info("=" * 60)

    await _flatten_to_zero(
        maker=perp_maker_tester_session,
        taker=perp_taker_tester_session,
        symbol=symbol,
        min_qty=min_qty,
        qty_step=qty_step,
        label="session start",
    )

    yield

    logger.info("=" * 60)
    logger.info(f"📍 PERP POSITION GUARD: post-session flatten of {symbol}")
    logger.info("=" * 60)

    try:
        await _flatten_to_zero(
            maker=perp_maker_tester_session,
            taker=perp_taker_tester_session,
            symbol=symbol,
            min_qty=min_qty,
            qty_step=qty_step,
            label="session end",
        )
    except (ApiException, OSError, RuntimeError) as e:
        logger.warning(f"perp_position_guard: flatten threw {type(e).__name__}: {e}")


# ============================================================================
# SPOT Test Configuration Fixture
# ============================================================================


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def spot_market_configs(maker_tester_session):  # pylint: disable=redefined-outer-name
    """
    Session-scoped fixture that fetches all spot market configurations from API.

    Returns a dictionary mapping base asset (e.g., "ETH", "BTC") to SpotMarketConfig.
    """
    logger.info("📊 Fetching spot market configurations from API...")
    try:
        configs = await fetch_spot_market_configs(maker_tester_session.client)
        available_assets = sorted(configs.keys())
        logger.info(f"✅ Loaded spot market configs for: {', '.join(available_assets)}")
        return configs
    except (OSError, RuntimeError, ValueError) as e:
        logger.error(f"❌ Failed to fetch spot market configs: {e}")
        raise


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def spot_config(maker_tester_session, spot_market_configs, spot_asset):  # pylint: disable=redefined-outer-name
    """
    Session-scoped fixture that provides centralized SPOT test configuration.

    Fetches market config dynamically from API and the current oracle price.
    The asset is selected via the --spot-asset CLI option (default: ETH).

    Usage in tests:
        async def test_something(spot_config, maker_tester):
            maker_price = spot_config.price(0.99)  # 99% of oracle
            qty = spot_config.min_qty
            symbol = spot_config.symbol

    Run tests for different assets:
        pytest tests/test_spot/ -v --spot-asset=ETH
        pytest tests/test_spot/ -v --spot-asset=BTC
    """
    # Validate the selected asset is available
    if spot_asset not in spot_market_configs:
        available = sorted(spot_market_configs.keys())
        pytest.skip(f"Asset '{spot_asset}' not available. Available assets: {', '.join(available)}")

    market_config: SpotMarketConfig = spot_market_configs[spot_asset]

    logger.info("=" * 60)
    logger.info(f"🎯 SPOT TESTS CONFIGURED FOR: {spot_asset}")
    logger.info(f"   Symbol: {market_config.symbol}")
    logger.info(f"   Min Order Qty: {market_config.min_order_qty}")
    logger.info(f"   Min Balance: {market_config.min_balance}")
    logger.info("=" * 60)

    # Fetch oracle price using PERP symbol (e.g., ETHRUSDPERP, BTCRUSDPERP)
    oracle_symbol = market_config.oracle_symbol

    try:
        price_str = await maker_tester_session.data.current_price(oracle_symbol)
        oracle_price = float(price_str)
        logger.info(f"📊 Fetched {spot_asset} oracle price: ${oracle_price:.2f}")
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning(f"Failed to fetch oracle price for {oracle_symbol}: {e}")
        # Fallback prices per asset
        fallback_prices = {"ETH": 3000.0, "BTC": 80000.0}
        oracle_price = fallback_prices.get(spot_asset, 1000.0)
        logger.warning(f"Using fallback oracle price: ${oracle_price:.2f}")

    return SpotTestConfig(
        symbol=market_config.symbol,
        market_id=market_config.market_id,
        min_qty=market_config.min_order_qty,
        qty_step_size=market_config.qty_step_size,
        oracle_price=oracle_price,
        base_asset=market_config.base_asset,
        min_balance=float(market_config.min_balance),
    )


# ============================================================================
# SPOT Balance Guard Fixture
# ============================================================================


async def _get_account_balance(tester: ReyaTester, asset: str) -> Decimal:
    """Get balance for a specific asset from a tester's account."""
    balance = await tester.data.balance(asset)
    if balance and balance.real_balance:
        return Decimal(balance.real_balance)
    return Decimal("0")


async def _execute_spot_transfer(
    sender: ReyaTester,
    receiver: ReyaTester,
    symbol: str,
    qty: str,
    price: str,
) -> bool:
    """
    Transfer spot assets between accounts via order matching.

    Sender places GTC sell, receiver places IOC buy at same price.
    Returns True if transfer succeeded.
    """
    # Step 1: Sender places GTC sell order
    sell_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=price,
        qty=qty,
        time_in_force=TimeInForce.GTC,
    )

    sell_response = await sender.client.create_limit_order(sell_params)
    sell_order_id = sell_response.order_id

    if not sell_order_id:
        logger.error("Failed to create sell order for transfer")
        return False

    await asyncio.sleep(0.3)

    # Step 2: Receiver places IOC buy order to match
    buy_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=price,
        qty=qty,
        time_in_force=TimeInForce.IOC,
    )

    await receiver.client.create_limit_order(buy_params)

    # Step 3: Wait for settlement and cancel any remaining sell order
    await asyncio.sleep(1.0)

    try:
        open_orders = await sender.client.get_open_orders()
        for order in open_orders:
            if hasattr(order, "order_id") and order.order_id == sell_order_id:
                await sender.client.cancel_order(
                    order_id=sell_order_id,
                    symbol=symbol,
                    account_id=sender.account_id,
                )
                break
    except (OSError, RuntimeError) as e:  # nosec B110
        logger.debug(f"Order cancel failed (may have been fully filled): {e}")

    return True


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def spot_balance_guard(
    maker_tester_session, taker_tester_session, spot_config
):  # pylint: disable=redefined-outer-name
    """
    Session-scoped fixture that checks balances before SPOT tests and restores them after.

    NOTE: This fixture is NOT autouse - it must be explicitly requested by spot tests.

    The base asset is determined dynamically from spot_config (e.g., ETH, BTC).

    At session start:
    - Checks both accounts have minimum required balances for the selected asset
    - Stores initial balances for restoration
    - Skips all SPOT tests if balances are insufficient

    At session end:
    - Calculates balance differences from initial state
    - Executes transfers to restore initial balances
    """
    # Get asset info from spot_config
    base_asset = spot_config.base_asset
    min_asset_balance = Decimal(str(spot_config.min_balance))
    symbol = spot_config.symbol

    logger.info("=" * 60)
    logger.info(f"💰 SPOT BALANCE GUARD: Checking {base_asset} account balances")
    logger.info("=" * 60)

    # Get initial balances for both accounts
    maker_asset = await _get_account_balance(maker_tester_session, base_asset)
    maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    taker_asset = await _get_account_balance(taker_tester_session, base_asset)
    taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"📊 Account 1 (Maker): {maker_asset} {base_asset}, {maker_rusd} RUSD")
    logger.info(f"📊 Account 2 (Taker): {taker_asset} {base_asset}, {taker_rusd} RUSD")

    # Store initial balances for restoration (using generic keys)
    initial_balances = {
        "maker_asset": maker_asset,
        "maker_rusd": maker_rusd,
        "taker_asset": taker_asset,
        "taker_rusd": taker_rusd,
        "base_asset": base_asset,
        "symbol": symbol,
        "min_qty": Decimal(spot_config.min_qty),
    }

    # Check minimum requirements
    min_rusd = Decimal(str(MIN_RUSD_BALANCE))

    insufficient = []
    if maker_asset < min_asset_balance:
        insufficient.append(f"Account 1 {base_asset}: {maker_asset} < {min_asset_balance}")
    if maker_rusd < min_rusd:
        insufficient.append(f"Account 1 RUSD: {maker_rusd} < {min_rusd}")
    if taker_asset < min_asset_balance:
        insufficient.append(f"Account 2 {base_asset}: {taker_asset} < {min_asset_balance}")
    if taker_rusd < min_rusd:
        insufficient.append(f"Account 2 RUSD: {taker_rusd} < {min_rusd}")

    if insufficient:
        logger.error("❌ INSUFFICIENT BALANCES FOR SPOT TESTS:")
        for msg in insufficient:
            logger.error(f"   - {msg}")
        logger.error(f"   Required minimums: {min_asset_balance} {base_asset}, {MIN_RUSD_BALANCE} RUSD per account")
        pytest.skip(f"Insufficient balances for SPOT tests: {', '.join(insufficient)}")

    logger.info("✅ Balance check passed - proceeding with SPOT tests")
    logger.info("=" * 60)

    # Run all SPOT tests
    yield initial_balances

    # ========================================================================
    # BALANCE RESTORATION (runs after all SPOT tests complete)
    # ========================================================================
    # This restoration logic handles both scenarios:
    # 1. Tests traded only between maker and taker accounts
    # 2. Tests traded with external liquidity (non-empty order book)
    #
    # Strategy: Track each account independently and restore via maker↔taker
    # transfers. External liquidity trades will show as imbalances that we
    # correct by transferring between our accounts.
    # ========================================================================
    # Extract stored values from initial_balances
    base_asset = initial_balances["base_asset"]
    symbol = initial_balances["symbol"]
    min_qty = initial_balances["min_qty"]

    logger.info("=" * 60)
    logger.info(f"💰 SPOT BALANCE GUARD: Restoring {base_asset} account balances")
    logger.info("=" * 60)

    # Get final balances for both accounts
    final_maker_asset = await _get_account_balance(maker_tester_session, base_asset)
    final_maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    final_taker_asset = await _get_account_balance(taker_tester_session, base_asset)
    final_taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"📊 Final Account 1 (Maker): {final_maker_asset} {base_asset}, {final_maker_rusd} RUSD")
    logger.info(f"📊 Final Account 2 (Taker): {final_taker_asset} {base_asset}, {final_taker_rusd} RUSD")

    # Calculate changes for EACH account independently
    maker_asset_change = final_maker_asset - initial_balances["maker_asset"]
    maker_rusd_change = final_maker_rusd - initial_balances["maker_rusd"]
    taker_asset_change = final_taker_asset - initial_balances["taker_asset"]
    taker_rusd_change = final_taker_rusd - initial_balances["taker_rusd"]

    logger.info(f"📈 Maker changes: {base_asset} {maker_asset_change:+}, RUSD {maker_rusd_change:+}")
    logger.info(f"📈 Taker changes: {base_asset} {taker_asset_change:+}, RUSD {taker_rusd_change:+}")

    oracle_price = Decimal(str(spot_config.oracle_price))
    min_price = oracle_price * Decimal("0.95")
    max_price = oracle_price * Decimal("1.05")

    # Determine restoration needs
    # Net asset change across both accounts (non-zero means external trades occurred)
    net_asset_change = maker_asset_change + taker_asset_change
    if abs(net_asset_change) >= min_qty:
        logger.info(f"📊 Net {base_asset} change (external trades): {net_asset_change:+}")

    # Calculate what each account needs to reach initial balance
    maker_asset_needed = initial_balances["maker_asset"] - final_maker_asset  # positive = needs more
    taker_asset_needed = initial_balances["taker_asset"] - final_taker_asset  # positive = needs more

    logger.info(
        f"📊 Maker needs: {maker_asset_needed:+} {base_asset}, Taker needs: {taker_asset_needed:+} {base_asset}"
    )

    # Get current order book to determine restoration strategy
    depth = await taker_tester_session.data.market_depth(symbol)
    has_external_bids = depth.bids and len(depth.bids) > 0
    has_external_asks = depth.asks and len(depth.asks) > 0
    has_external_liquidity = has_external_bids or has_external_asks

    logger.info(f"📊 Order book: bids={has_external_bids}, asks={has_external_asks}")

    # ========================================================================
    # RESTORATION STRATEGY:
    # - If NO external liquidity: Use internal transfers (maker ↔ taker)
    # - If external liquidity exists: Each account trades with external liquidity
    #   (internal transfers won't work because IOC orders match external first)
    # ========================================================================

    if not has_external_liquidity:
        # EMPTY ORDER BOOK: Use internal transfers between maker and taker
        logger.info("📋 Empty order book - using internal transfers")

        # Calculate restoration price based on RUSD changes
        total_asset_moved = abs(maker_asset_change) + abs(taker_asset_change)
        total_rusd_moved = abs(maker_rusd_change) + abs(taker_rusd_change)

        if total_asset_moved > Decimal("0") and total_rusd_moved > Decimal("0.01"):
            effective_price = total_rusd_moved / total_asset_moved
        else:
            effective_price = oracle_price

        restoration_price = max(min_price, min(max_price, effective_price))
        restoration_price = restoration_price.quantize(Decimal("0.01"))
        logger.info(f"💱 Restoration price: ${restoration_price}")

        if abs(maker_asset_needed) >= min_qty:
            if maker_asset_needed > 0:
                qty = str(maker_asset_needed.quantize(min_qty))
                logger.info(f"🔄 Internal transfer: {qty} {base_asset} from Taker → Maker @ ${restoration_price}")
                await _execute_spot_transfer(
                    sender=taker_tester_session,
                    receiver=maker_tester_session,
                    symbol=symbol,
                    qty=qty,
                    price=str(restoration_price),
                )
            else:
                qty = str((-maker_asset_needed).quantize(min_qty))
                logger.info(f"🔄 Internal transfer: {qty} {base_asset} from Maker → Taker @ ${restoration_price}")
                await _execute_spot_transfer(
                    sender=maker_tester_session,
                    receiver=taker_tester_session,
                    symbol=symbol,
                    qty=qty,
                    price=str(restoration_price),
                )
            await asyncio.sleep(1.0)

    else:
        # NON-EMPTY ORDER BOOK: Each account trades with external liquidity
        logger.info("📋 External liquidity present - each account trades with external")

        # Helper function to restore an account's asset balance via external liquidity
        async def restore_account_asset(tester: ReyaTester, asset_needed: Decimal, account_name: str):
            if abs(asset_needed) < min_qty:
                return

            if asset_needed > 0:
                # Account needs more asset - buy from external asks
                if not has_external_asks:
                    logger.warning(f"⚠️ {account_name} needs {asset_needed} {base_asset} but no external asks available")
                    return

                qty = str(asset_needed.quantize(min_qty))
                best_ask = Decimal(str(depth.asks[0].px))
                buy_price = min(max_price, best_ask * Decimal("1.001")).quantize(Decimal("0.01"))
                logger.info(f"🔄 {account_name}: Buying {qty} {base_asset} @ ${buy_price} from external asks")

                try:
                    buy_params = LimitOrderParameters(
                        symbol=symbol,
                        is_buy=True,
                        limit_px=str(buy_price),
                        qty=qty,
                        time_in_force=TimeInForce.IOC,
                    )
                    await tester.client.create_limit_order(buy_params)
                    await asyncio.sleep(0.5)
                except (ApiException, OSError, RuntimeError) as e:
                    logger.warning(f"⚠️ {account_name}: Failed to buy {base_asset}: {e}")

            else:
                # Account has excess asset - sell to external bids
                if not has_external_bids:
                    logger.warning(
                        f"⚠️ {account_name} has excess {-asset_needed} {base_asset} but no external bids available"
                    )
                    return

                qty = str((-asset_needed).quantize(min_qty))
                best_bid = Decimal(str(depth.bids[0].px))
                sell_price = max(min_price, best_bid * Decimal("0.999")).quantize(Decimal("0.01"))
                logger.info(f"🔄 {account_name}: Selling {qty} {base_asset} @ ${sell_price} to external bids")

                try:
                    sell_params = LimitOrderParameters(
                        symbol=symbol,
                        is_buy=False,
                        limit_px=str(sell_price),
                        qty=qty,
                        time_in_force=TimeInForce.IOC,
                    )
                    await tester.client.create_limit_order(sell_params)
                    await asyncio.sleep(0.5)
                except (ApiException, OSError, RuntimeError) as e:
                    logger.warning(f"⚠️ {account_name}: Failed to sell {base_asset}: {e}")

        # Restore both accounts
        await restore_account_asset(maker_tester_session, maker_asset_needed, "Maker")
        await restore_account_asset(taker_tester_session, taker_asset_needed, "Taker")
        await asyncio.sleep(1.0)

    # Log final restored balances
    restored_maker_asset = await _get_account_balance(maker_tester_session, base_asset)
    restored_maker_rusd = await _get_account_balance(maker_tester_session, "RUSD")
    restored_taker_asset = await _get_account_balance(taker_tester_session, base_asset)
    restored_taker_rusd = await _get_account_balance(taker_tester_session, "RUSD")

    logger.info(f"✅ Final Account 1 (Maker): {restored_maker_asset} {base_asset}, {restored_maker_rusd} RUSD")
    logger.info(f"✅ Final Account 2 (Taker): {restored_taker_asset} {base_asset}, {restored_taker_rusd} RUSD")

    # Log how close we got to initial balances for both accounts
    maker_asset_diff = restored_maker_asset - initial_balances["maker_asset"]
    maker_rusd_diff = restored_maker_rusd - initial_balances["maker_rusd"]
    taker_asset_diff = restored_taker_asset - initial_balances["taker_asset"]
    taker_rusd_diff = restored_taker_rusd - initial_balances["taker_rusd"]

    logger.info(f"📊 Maker remaining diff from initial: {base_asset} {maker_asset_diff:+}, RUSD {maker_rusd_diff:+}")
    logger.info(f"📊 Taker remaining diff from initial: {base_asset} {taker_asset_diff:+}, RUSD {taker_rusd_diff:+}")

    logger.info("=" * 60)
