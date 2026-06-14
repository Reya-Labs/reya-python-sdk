"""
Pytest fixtures for Reya Python SDK integration tests.

Uses pytest-asyncio's loop_scope feature (v0.24+) to share a single event loop
across all tests in a session, enabling session-scoped async fixtures.
"""

# pylint: disable=wrong-import-position,wrong-import-order
# Load .env BEFORE importing the SDK so module-level constants in
# `sdk.reya_rest_api.config` (e.g. `REYA_DEX_ID`) pick up devnet/staging
# overrides. Imports happen at conftest load time, so calling load_dotenv
# inside fixtures would be too late. The pylint disable above suppresses
# the `wrong-import-position` / `wrong-import-order` warnings this
# intentionally-out-of-order arrangement otherwise produces (flake8 is
# already silenced via `# noqa: E402`).
from dotenv import load_dotenv

load_dotenv()

from typing import Optional  # noqa: E402

import asyncio  # noqa: E402
import os  # noqa: E402
from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from sdk.open_api.api.wallet_data_api import WalletDataApi  # noqa: E402
from sdk.open_api.api_client import ApiClient  # noqa: E402
from sdk.open_api.configuration import Configuration  # noqa: E402
from sdk.open_api.exceptions import ApiException  # noqa: E402
from sdk.open_api.models import TimeInForce  # noqa: E402
from sdk.reya_rest_api.config import MAINNET_CHAIN_ID  # noqa: E402
from sdk.reya_rest_api.models.orders import LimitOrderParameters  # noqa: E402
from tests.helpers import ReyaTester  # noqa: E402
from tests.helpers.market_config import (  # noqa: E402
    MarketConfig,
    PerpTestConfig,
    SpotMarketConfig,
    SpotTestConfig,
    fetch_spot_market_configs,
)
from tests.helpers.reya_tester import logger  # noqa: E402
from tests.helpers.settlement import make_settlement_probe  # noqa: E402

# Time delay between tests
TEST_DELAY_SECONDS = 0.1

# Minimum RUSD balance requirements for SPOT tests (asset balance is dynamic per market)
MIN_RUSD_BALANCE = 15.0

# Default asset if not specified via CLI
DEFAULT_SPOT_ASSET = "ETH"


def pytest_addoption(parser):
    """Add custom command-line options for the live e2e suites."""
    parser.addoption(
        "--spot-asset",
        action="store",
        default=DEFAULT_SPOT_ASSET,
        help="Asset to use for spot tests (e.g., ETH, BTC). Default: ETH",
    )
    parser.addoption(
        "--orderbook-perp-asset",
        action="store",
        default="ETH",
        help="Base asset for [spot, perp]-parametrized tests' perp leg. Symbol becomes <asset>RUSDPERP.",
    )


def pytest_collection_modifyitems(items):
    """Auto-mark [spot]/[perp] param instances so `-m spot` / `-m perp` stay
    truthful for the parametrized suites without hand-applied markers."""
    for item in items:
        callspec = getattr(item, "callspec", None)
        market = callspec.params.get("market_type") if callspec else None
        if market == "spot":
            item.add_marker(pytest.mark.spot)
        elif market == "perp":
            item.add_marker(pytest.mark.perp)


@pytest.fixture(scope="session")
def spot_asset(request):
    """Get the selected spot asset from CLI option."""
    return request.config.getoption("--spot-asset").upper()


@pytest_asyncio.fixture(loop_scope="session", scope="function", autouse=True)
async def rate_limit_delay(request):
    """
    Add a small delay after each test to avoid WAF rate limiting.

    The staging environment uses AWS WAF which can block requests if too many
    come from the same IP in a short time window. This delay helps prevent
    403 Forbidden errors during test runs. Offline-marked tests make no
    network calls, so they skip the delay entirely.
    """
    yield
    if request.node.get_closest_marker("offline") is None:
        await asyncio.sleep(TEST_DELAY_SECONDS)


# ============================================================================
# Cancel-on-disconnect (cancelAllAfter) teardown safety
# ============================================================================
# An armed COD countdown that leaks past a test would mass-cancel a later
# test's resting orders mid-flight. Every tester therefore disarms in
# teardown — idempotently (disarming an unarmed account is a server-side
# no-op) and best-effort (older deployments without the endpoint must not
# tank cleanup).


async def _disarm_cod_safely(tester: ReyaTester) -> None:
    """Disarm the tester's COD countdown; never fail teardown.

    Swallows ApiException (e.g. 404 on deployments that predate
    cancelAllAfter) and transport errors — an armed deadline must never leak
    across tests, but a failed disarm must not mask the test's own outcome.
    """
    try:
        await tester.disarm_cod()
    except (ApiException, OSError, RuntimeError, ValueError, asyncio.CancelledError) as e:
        logger.debug(f"COD disarm skipped (account {tester.account_id}): {e}")


# ============================================================================
# Execution-busts guard (session-wide settlement-regression tripwire)
# ============================================================================

_BUSTS_GUARD_WALLET_ENV_VARS = (
    "PERP_WALLET_ADDRESS_1",
    "PERP_WALLET_ADDRESS_2",
    "SPOT_WALLET_ADDRESS_1",
    "SPOT_WALLET_ADDRESS_2",
)


def _busts_guard_wallets() -> list[str]:
    """Unique wallet addresses configured via env (order-preserving)."""
    wallets: list[str] = []
    for var in _BUSTS_GUARD_WALLET_ENV_VARS:
        address = os.environ.get(var)
        if address and address not in wallets:
            wallets.append(address)
    return wallets


def _busts_guard_api_url() -> str:
    """API base URL resolved the same way TradingConfig.from_env does, but
    without requiring wallet/key env vars (the guard is read-only)."""
    chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))
    if chain_id == MAINNET_CHAIN_ID:
        default_api_url = "https://api.reya.xyz/v2"
    else:
        default_api_url = "https://api-devnet.reya-cronos.network/v2"
    return os.environ.get("REYA_API_URL", default_api_url)


async def _fetch_execution_bust_counts(wallets: list[str]) -> Optional[dict[str, int]]:
    """Fetch the executionBusts count per wallet via a throwaway read-only
    API client. Returns None when the API is unreachable (offline/unit runs)."""
    api_client = ApiClient(Configuration(host=_busts_guard_api_url()))
    try:
        wallet_api = WalletDataApi(api_client)
        counts: dict[str, int] = {}
        for address in wallets:
            bust_list = await wallet_api.get_wallet_execution_busts(address=address)
            counts[address] = len(bust_list.data or [])
        return counts
    except (ApiException, OSError, RuntimeError, ValueError) as e:
        logger.warning(f"Execution-busts guard: API unreachable, guard disabled: {e}")
        return None
    finally:
        if hasattr(api_client, "rest_client") and api_client.rest_client:
            await api_client.rest_client.close()


@pytest_asyncio.fixture(loop_scope="session", scope="session", autouse=True)
async def execution_busts_guard(request):
    """Session-wide settlement-regression tripwire.

    Records each configured wallet's `GET /v2/wallet/{addr}/executionBusts`
    count at session start and asserts it is UNCHANGED at session end — any
    new bust during the run means a fill was accepted by the matching engine
    but failed settlement, which no test should cause.

    Skips silently when no wallet env vars are configured (unit-only runs
    make zero network calls) and degrades gracefully when the API is
    unreachable (offline runs).
    """
    if all(item.get_closest_marker("offline") for item in request.session.items):
        # Offline-only session (e.g. `pytest -m offline` / tests/parity) —
        # the guard's API calls are the only network traffic; stay silent.
        yield
        return

    wallets = _busts_guard_wallets()
    if not wallets:
        yield
        return

    start_counts = await _fetch_execution_bust_counts(wallets)
    if start_counts is None:
        yield
        return

    logger.info(f"🛡️ Execution-busts guard armed: baseline {start_counts}")
    yield

    end_counts = await _fetch_execution_bust_counts(wallets)
    if end_counts is None:
        logger.warning("Execution-busts guard: API unreachable at session end; skipping final check")
        return

    changed = {
        address: (start_counts[address], end_counts.get(address))
        for address in start_counts
        if end_counts.get(address) != start_counts[address]
    }
    assert not changed, (
        "Execution busts changed during the test session (settlement regression): "
        f"{{wallet: (start, end)}} = {changed}"
    )
    logger.info("🛡️ Execution-busts guard: no new busts across the session")


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
        await _disarm_cod_safely(tester)
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

    # Disarm COD first: an armed countdown firing mid-cleanup would race the
    # explicit close_all below; an armed deadline must never leak across tests.
    await _disarm_cod_safely(reya_tester_session)
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
        await _disarm_cod_safely(tester)
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
        await _disarm_cod_safely(tester)
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

    await _disarm_cod_safely(maker_tester_session)
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

    await _disarm_cod_safely(maker_tester_session)
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

    await _disarm_cod_safely(taker_tester_session)
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
        await _disarm_cod_safely(tester)
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
        await _disarm_cod_safely(tester)
        if tester.websocket:
            tester.websocket.close()
        await tester.positions.close_all(fail_if_none=False)
        await tester.orders.close_all(fail_if_none=False)
        await tester.client.close()
        logger.info("✅ Perp taker session cleanup completed")
    except (OSError, RuntimeError, asyncio.CancelledError) as e:
        logger.warning(f"Error during perp taker cleanup: {e}")


async def _restore_to_baseline(  # pylint: disable=redefined-outer-name
    maker: ReyaTester,
    taker: ReyaTester,
    symbol: str,
    maker_baseline: Decimal,
    taker_baseline: Decimal,
    min_qty: Decimal,
    qty_step: Decimal,
) -> None:
    """Trade the test's own net delta back between the two accounts.

    All test fills are maker↔taker crosses, so the deltas mirror (taker +X ⇒
    maker −X) and one reverse cross restores both baselines exactly. The IOC
    leg is NOT reduce-only: restoring can legitimately increase an account's
    absolute position (e.g. selling back to a short baseline).
    """
    maker_delta = await maker.positions.signed_qty(symbol) - maker_baseline
    taker_delta = await taker.positions.signed_qty(symbol) - taker_baseline
    if abs(maker_delta) < min_qty and abs(taker_delta) < min_qty:
        return

    logger.info(f"  [restore] maker delta {maker_delta:+}, taker delta {taker_delta:+} on {symbol}")
    if maker_delta + taker_delta != Decimal("0"):
        logger.warning(
            f"  [restore] deltas not mirrored (sum {maker_delta + taker_delta:+}) — "
            "external fills involved; restoring the reversible overlap only"
        )
    if maker_delta == 0 or taker_delta == 0 or (maker_delta > 0) == (taker_delta > 0):
        logger.warning("  [restore] deltas not opposite-signed; cannot cross back peer-to-peer")
        return

    restore_qty = min(abs(maker_delta), abs(taker_delta)).quantize(qty_step)
    if restore_qty < min_qty:
        return

    oracle_price = Decimal(str(await maker.data.current_price(symbol)))
    cross_price = oracle_price.quantize(Decimal("0.01"))

    # Whoever gained exposure sells it back: the positive-delta side rests
    # the GTC SELL, the other side crosses with an IOC BUY.
    side_a, side_b = (maker, taker) if maker_delta > 0 else (taker, maker)
    logger.info(
        f"  [restore] account {side_a.account_id} GTC SELL {restore_qty} @ ${cross_price}, "
        f"account {side_b.account_id} IOC BUY"
    )
    try:
        ok = await _execute_perp_flatten(
            side_a=side_a,
            side_b=side_b,
            symbol=symbol,
            qty=str(restore_qty),
            price=str(cross_price),
            side_a_is_buy=False,
            ioc_reduce_only=False,
        )
        if not ok:
            logger.warning("  [restore] restore cross did not complete cleanly")
            return
    except (ApiException, OSError, RuntimeError) as e:
        logger.warning(f"  [restore] restore cross threw {type(e).__name__}: {e}")
        return

    residual_maker = await maker.positions.signed_qty(symbol) - maker_baseline
    residual_taker = await taker.positions.signed_qty(symbol) - taker_baseline
    if abs(residual_maker) >= min_qty or abs(residual_taker) >= min_qty:
        logger.warning(f"  [restore] residual delta after restore: maker {residual_maker:+}, taker {residual_taker:+}")
    else:
        logger.info("  [restore] ✅ both accounts back at baseline")


# ============================================================================
# [spot, perp] market parametrization (lifted from tests/engine/conftest.py)
# ============================================================================
# Any test (in any directory) can take `market_config` + `maker`/`taker` and
# run once per market type. ALL resolution is lazy (request.getfixturevalue)
# so an env configured for only one market never spins up the other market's
# sessions — the missing-credential pytest.skip then applies only to that
# market's param instances instead of tanking the suite.

_DEFAULT_MARKET_TYPES = ("spot", "perp")


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def perp_market_config(
    request, perp_maker_tester_session  # pylint: disable=redefined-outer-name
) -> PerpTestConfig:
    """Fetch the perp market config for parametrized tests.

    Resolves the asset from (in order): the ``--orderbook-perp-asset`` CLI
    flag, the ``ORDERBOOK_PERP_ASSET`` env var, then the default ``ETH``.
    Metadata comes from the PERP session (historically this pulled the SPOT
    maker session, which forced spot-account init on perp-only runs). Skips
    if the deployment hasn't enabled this market on the matching engine.
    """
    cli_asset = request.config.getoption("--orderbook-perp-asset", default=None)
    asset = (cli_asset or os.environ.get("ORDERBOOK_PERP_ASSET", "ETH")).upper()
    symbol = f"{asset}RUSDPERP"

    market_def = None
    for definition in await perp_maker_tester_session.client.reference.get_perp_market_definitions():
        if definition.symbol == symbol:
            market_def = definition
            break

    if market_def is None:
        pytest.skip(f"Perp market {symbol} not present in /v2/marketDefinitions")
    assert market_def is not None  # narrows the Optional after the skip above

    # Fail loud rather than swallow the error with a fake price — a wrong oracle
    # price silently invalidates every downstream test (limits, liquidity checks,
    # circuit-breaker bands).
    oracle_price = float(await perp_maker_tester_session.data.current_price(symbol))

    return PerpTestConfig(
        symbol=symbol,
        market_id=market_def.market_id,
        min_qty=str(market_def.min_order_qty),
        qty_step_size=str(market_def.qty_step_size),
        oracle_price=oracle_price,
        base_asset=asset,
        min_balance=float(Decimal(market_def.min_order_qty) * 50),
        tick_size=str(market_def.tick_size),
    )


@pytest.fixture(params=_DEFAULT_MARKET_TYPES)
def market_type(request) -> str:
    """Parametrize over [spot, perp] — the param drives ``market_config``."""
    param: str = request.param
    return param


@pytest.fixture
def market_config(market_type: str, request) -> MarketConfig:  # pylint: disable=redefined-outer-name
    """Yield the right per-market config for the active parametrization.

    Resolved LAZILY by market type: requesting the [spot] instance never
    touches the perp session and vice versa. (The pre-lift version took both
    configs as arguments, which eagerly initialized BOTH market sessions for
    every param instance.)
    """
    name = "spot_config" if market_type == "spot" else "perp_market_config"
    config: MarketConfig = request.getfixturevalue(name)
    return config


@pytest.fixture
def maker(market_type: str, request):  # pylint: disable=redefined-outer-name
    """Yield the maker tester for the active parametrization (lazy; see
    ``market_config``)."""
    return request.getfixturevalue("perp_maker_tester" if market_type == "perp" else "maker_tester")


@pytest.fixture
def taker(market_type: str, request):  # pylint: disable=redefined-outer-name
    """Yield the taker tester for the active parametrization (lazy; see
    ``market_config``)."""
    return request.getfixturevalue("perp_taker_tester" if market_type == "perp" else "taker_tester")


@pytest.fixture
def settlement_probe(market_type, market_config, maker, taker):  # pylint: disable=redefined-outer-name
    """A market-matched settlement probe for [spot, perp]-parametrized FILL
    tests: spot asserts exact zero-fee balance deltas, perp asserts ±qty signed
    position deltas. ``maker`` is the aggressor (buyer), ``taker`` the resting
    counterparty (seller)."""
    return make_settlement_probe(market_type, market_config, buyer=maker, seller=taker)


@pytest.fixture
def settlement_cleanup_guard(market_type, request):  # pylint: disable=redefined-outer-name
    """Post-test settlement cleanup for a [spot, perp]-parametrized FILL test:
    spot wires the session ``spot_balance_guard`` (balances restored at session
    end); perp is a no-op here because the perp baseline-restore activates
    transitively via the maker/taker → perp fixtures. Request it (autouse) only
    from modules that actually produce fills, so rejection-only and read-only
    tests never spin up the spot sessions."""
    if market_type == "spot":
        request.getfixturevalue("spot_balance_guard")
    yield


# ============================================================================
# Perp-skip canary
# ============================================================================
# The lazy resolution above has one dangerous regression mode: a bug that
# makes EVERY [perp] param instance skip at setup while perp credentials are
# present would leave the suite green with zero perp coverage. Track
# setup-phase outcomes of perp-param instances and fail the run if all of
# them skipped. (In-test skips — e.g. external-liquidity guards — happen in
# the 'call' phase and don't trip this.) Kill switch: PERP_SKIP_CANARY=off.

_perp_param_setup_stats = {"total": 0, "skipped": 0}


def pytest_runtest_logreport(report):
    if report.when != "setup" or "[perp" not in report.nodeid:
        return
    _perp_param_setup_stats["total"] += 1
    if report.skipped:
        _perp_param_setup_stats["skipped"] += 1


def pytest_sessionfinish(session, exitstatus):  # pylint: disable=unused-argument
    if os.environ.get("PERP_SKIP_CANARY", "").lower() == "off":
        return
    stats = _perp_param_setup_stats
    if stats["total"] >= 3 and stats["skipped"] == stats["total"] and os.environ.get("PERP_ACCOUNT_ID_1"):
        print(
            "\n[perp-skip canary] every [perp] param instance "
            f"({stats['total']}) skipped at setup while PERP credentials are present — "
            "perp coverage silently vanished (lazy fixture regression or perp market "
            "missing from /v2/marketDefinitions). Set PERP_SKIP_CANARY=off to override."
        )
        session.exitstatus = 1


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def perp_baseline_restore(  # pylint: disable=redefined-outer-name,unused-argument
    perp_maker_tester_session, perp_taker_tester_session, perp_position_guard
):
    """Capture both perp accounts' signed positions before each test and trade
    the test's own delta back afterwards.

    Perp tests are baseline-relative: whatever position an account holds when
    a test starts is the baseline it asserts signed deltas against (via
    `check.position_delta`), and this teardown crosses the net delta back
    between the two accounts so each test leaves them exactly as it got them.
    Pre-existing (possibly dirty) state therefore never fails a test — tests
    that genuinely need a flat account `pytest.skip` themselves instead.

    Activated transitively through `perp_maker_tester` / `perp_taker_tester`.
    Teardown ordering matters: the wrappers' `orders.close_all` finalizers run
    first (clearing any leftover resting GTC), then this restore cross runs on
    a clean book.
    """
    market_def = await perp_maker_tester_session.get_market_definition(PERP_GUARD_SYMBOL)
    min_qty = Decimal(str(market_def.min_order_qty))
    qty_step = Decimal(str(market_def.qty_step_size))

    maker_baseline = await perp_maker_tester_session.positions.signed_qty(PERP_GUARD_SYMBOL)
    taker_baseline = await perp_taker_tester_session.positions.signed_qty(PERP_GUARD_SYMBOL)

    yield

    await _restore_to_baseline(
        maker=perp_maker_tester_session,
        taker=perp_taker_tester_session,
        symbol=PERP_GUARD_SYMBOL,
        maker_baseline=maker_baseline,
        taker_baseline=taker_baseline,
        min_qty=min_qty,
        qty_step=qty_step,
    )


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def perp_maker_tester(  # pylint: disable=redefined-outer-name,unused-argument
    perp_maker_tester_session, perp_baseline_restore
):
    """Function-scoped perp maker — clears orders/WS state between tests.

    Positions are deliberately NOT closed here: perp tests are baseline-
    relative (see `perp_baseline_restore`), and a one-sided `close_all`
    would eat the very baseline the restore fixture preserves.
    """
    await perp_maker_tester_session.orders.close_all(fail_if_none=False)
    perp_maker_tester_session.ws.clear()
    yield perp_maker_tester_session
    await _disarm_cod_safely(perp_maker_tester_session)
    await perp_maker_tester_session.orders.close_all(fail_if_none=False)


@pytest_asyncio.fixture(loop_scope="session", scope="function")
async def perp_taker_tester(  # pylint: disable=redefined-outer-name,unused-argument
    perp_taker_tester_session, perp_baseline_restore
):
    """Function-scoped perp taker — clears orders/WS state between tests.

    Positions are deliberately NOT closed here: perp tests are baseline-
    relative (see `perp_baseline_restore`), and a one-sided `close_all`
    would eat the very baseline the restore fixture preserves.
    """
    await perp_taker_tester_session.orders.close_all(fail_if_none=False)
    perp_taker_tester_session.ws.clear()
    yield perp_taker_tester_session
    await _disarm_cod_safely(perp_taker_tester_session)
    await perp_taker_tester_session.orders.close_all(fail_if_none=False)


# ============================================================================
# Perp Position Guard (session-level orchestrated flatten)
# ============================================================================
# Mirrors the `_execute_spot_transfer` + `spot_balance_guard` pattern (lines
# 445+ below) but for perp positions. Under perpOB there's no AMM counterparty,
# so a single-account reduce-only IOC has nothing to match against. Tests need
# orchestrated maker/taker close: one tester rests a GTC, the other crosses it
# with an IOC, and both positions move consistently.


async def _execute_perp_flatten(
    side_a: ReyaTester,
    side_b: ReyaTester,
    symbol: str,
    qty: str,
    price: str,
    side_a_is_buy: bool,
    ioc_reduce_only: bool = True,
) -> bool:
    """Cross a GTC on one side with a matching IOC on the other to move
    both testers' positions by ±qty in opposite directions.

    Mirrors `_execute_spot_transfer`:
    - `side_a` (with `side_a_is_buy=True/False`) places a GTC.
    - `side_b` places the opposite-direction IOC at the same price.
    - On match, side_a moves +qty (if buy) / -qty (if sell), side_b mirrors.

    `ioc_reduce_only` controls the IOC leg: flatten-to-zero callers keep the
    default (the IOC always reduces toward zero there); baseline-restore
    callers MUST pass False, because trading a test's delta back can
    legitimately increase an account's absolute position (e.g. selling back
    to a short baseline).
    """
    # API rejects `reduceOnly` on GTC (only valid for IOC and TP/SL), so the
    # GTC leg is always a plain limit.
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
        reduce_only=ioc_reduce_only,
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


async def _flatten_to_zero(  # pylint: disable=redefined-outer-name
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
    maker_qty = await maker.positions.signed_qty(symbol)
    taker_qty = await taker.positions.signed_qty(symbol)
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
    # perp_market_config fixture (which lives in tests/engine/conftest).
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
        pytest tests/spot/ -v --spot-asset=ETH
        pytest tests/spot/ -v --spot-asset=BTC
    """
    # Validate the selected asset is available
    if spot_asset not in spot_market_configs:
        available = sorted(spot_market_configs.keys())
        pytest.skip(f"Asset '{spot_asset}' not available. Available assets: {', '.join(available)}")

    selected_market: SpotMarketConfig = spot_market_configs[spot_asset]

    logger.info("=" * 60)
    logger.info(f"🎯 SPOT TESTS CONFIGURED FOR: {spot_asset}")
    logger.info(f"   Symbol: {selected_market.symbol}")
    logger.info(f"   Min Order Qty: {selected_market.min_order_qty}")
    logger.info(f"   Min Balance: {selected_market.min_balance}")
    logger.info("=" * 60)

    # Fetch oracle price using PERP symbol (e.g., ETHRUSDPERP, BTCRUSDPERP)
    oracle_symbol = selected_market.oracle_symbol

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
        symbol=selected_market.symbol,
        market_id=selected_market.market_id,
        min_qty=selected_market.min_order_qty,
        qty_step_size=selected_market.qty_step_size,
        oracle_price=oracle_price,
        base_asset=selected_market.base_asset,
        min_balance=float(selected_market.min_balance),
        tick_size=selected_market.tick_size,
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
