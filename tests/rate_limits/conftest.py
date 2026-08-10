"""Fixtures for the Rate-Limit v1 suite.

The live modules build their OWN REST clients rather than reusing the shared
``ReyaTester`` fixtures, for two reasons:

* ``ReyaTester.orders.create_limit`` wraps creates in ``with_retry``, which
  would silently retry the very 429 these tests exist to observe;
* the whitelist-gate module needs an account that is deliberately NOT one of
  the seeded test wallets.
"""

from __future__ import annotations

from typing import Callable

import asyncio
import logging
import os
import shlex
import subprocess  # nosec B404 — the eject hook is an operator-supplied command template
from collections.abc import AsyncIterator, Awaitable

import pytest
import pytest_asyncio
from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import MAINNET_CHAIN_ID, TradingConfig
from tests.rate_limits import rl_config
from tests.rate_limits.rl_actions import RlMarket, ensure_flat, resolve_market

load_dotenv()

logger = logging.getLogger("reya.rate_limits")


def _build_client(credentials: rl_config.AccountCredentials) -> ReyaTradingClient:
    """Build a client for an explicit identity on the env's deployment.

    Deployment settings are read straight from the environment rather than via
    ``TradingConfig.from_env()``, which requires ``PERP_WALLET_ADDRESS_1`` — a
    perp credential this suite has no use for and a localnet may not define.
    """
    chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))
    default_api_url = (
        "https://api.reya.xyz/v2" if chain_id == MAINNET_CHAIN_ID else "https://api-devnet.reya-cronos.network/v2"
    )
    dex_id_env = os.environ.get("REYA_DEX_ID")
    config = TradingConfig(
        api_url=os.environ.get("REYA_API_URL", default_api_url),
        chain_id=chain_id,
        owner_wallet_address=credentials.wallet_address,
        private_key=credentials.private_key,
        account_id=credentials.account_id,
        orders_gateway_address=os.environ.get("REYA_ORDERS_GATEWAY"),
        dex_id_override=int(dex_id_env) if dex_id_env else None,
    )
    return ReyaTradingClient(config=config)


async def _started_client(credentials: rl_config.AccountCredentials) -> ReyaTradingClient:
    client = _build_client(credentials)
    await client.start()
    return client


@pytest.fixture(scope="session")
def rl_suite_config() -> rl_config.RateLimitSuiteConfig:
    """Every environment knob, resolved once per session."""
    return rl_config.load_suite_config()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def rl_standard_client_provider() -> AsyncIterator[Callable[[], Awaitable[ReyaTradingClient | None]]]:
    """Lazily build (once) the Standard-tier client, or hand back ``None``.

    A provider rather than the client itself, for two reasons:

    * ``rl_isolation`` must reach the client without ``request.getfixturevalue``
      — pytest-asyncio sets an async fixture up by driving its own runner, which
      raises ``RuntimeError: Runner.run() cannot be called from a running event
      loop`` unless the fixture happens to be cached already by an earlier test;
    * a fixture PARAMETER is resolved eagerly, so depending on the client
      directly would open a live session even for an ``-m offline`` run on a
      wired machine. Called lazily, offline runs stay offline.
    """
    built: dict[str, ReyaTradingClient] = {}

    async def provide() -> ReyaTradingClient | None:
        credentials = rl_config.standard_credentials()
        if credentials is None or not rl_config.RATE_LIMITS_ENABLED:
            return None
        if "client" not in built:
            built["client"] = await _started_client(credentials)
            logger.info(
                "rate-limit suite standard account: id=%s wallet=%s",
                credentials.account_id,
                credentials.wallet_address,
            )
        return built["client"]

    try:
        yield provide
    finally:
        client = built.get("client")
        if client is not None:
            await client.close()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def rl_client(  # pylint: disable=redefined-outer-name
    rl_standard_client_provider: Callable[[], Awaitable[ReyaTradingClient | None]],
) -> ReyaTradingClient:
    """REST client for the whitelisted, Standard-tier account under test."""
    client = await rl_standard_client_provider()
    if client is None:
        pytest.skip(f"rate-limit suite needs a Standard-tier account: {rl_config.standard_credentials_env_hint()}")
    return client


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def rl_non_whitelisted_client() -> AsyncIterator[ReyaTradingClient]:
    """REST client for an account the deployment did NOT seed into ``rl_whitelist``."""
    credentials = rl_config.non_whitelisted_credentials()
    if credentials is None:
        pytest.skip(
            "whitelist-gate coverage needs an account deliberately excluded from rl_whitelist: "
            f"{rl_config.non_whitelisted_credentials_env_hint()}"
        )

    client = await _started_client(credentials)
    logger.info("rate-limit suite non-whitelisted account: id=%s", credentials.account_id)
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def rl_trigger_client() -> AsyncIterator[ReyaTradingClient | None]:
    """REST client for the PERP identity that arms protective stops.

    Deliberately its own triple with no fallback — inheriting the spot Standard
    account is what let the retention probe arm against a mis-wired identity and
    still report a green test.

    Yields ``None`` rather than skipping so the eject flow can run its other
    four assertions unwired, while the trigger-only modules skip themselves.
    """
    credentials = rl_config.trigger_credentials()
    if credentials is None or not rl_config.RATE_LIMITS_ENABLED:
        yield None
        return

    client = await _started_client(credentials)
    logger.info("rate-limit suite trigger account: id=%s wallet=%s", credentials.account_id, credentials.wallet_address)
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def rl_unknown_owner_client(  # pylint: disable=redefined-outer-name
    rl_suite_config: rl_config.RateLimitSuiteConfig,
) -> AsyncIterator[ReyaTradingClient]:
    """A client claiming an ``accountId`` that resolves to NO owner.

    Signs with the non-whitelisted key so a misconfigured id can never be a
    whitelisted wallet's account. The gate keys on the owner resolved from the
    id and runs before signature verification, so the signer is irrelevant to
    the verdict under test.
    """
    credentials = rl_config.non_whitelisted_credentials()
    if credentials is None:
        pytest.skip(
            "unknown-owner coverage borrows the non-whitelisted signing key: "
            f"{rl_config.non_whitelisted_credentials_env_hint()}"
        )

    client = await _started_client(
        rl_config.AccountCredentials(
            account_id=rl_suite_config.unknown_account_id,
            private_key=credentials.private_key,
            wallet_address=credentials.wallet_address,
        )
    )
    logger.info("rate-limit suite unknown-owner account id: %s", rl_suite_config.unknown_account_id)
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def rl_market(  # pylint: disable=redefined-outer-name
    rl_client: ReyaTradingClient, rl_suite_config: rl_config.RateLimitSuiteConfig
) -> RlMarket:
    """The market the suite trades on (``RL_TEST_SYMBOL``)."""
    market = await resolve_market(rl_client, rl_suite_config.symbol)
    logger.info(
        "rate-limit suite market: %s minQty=%s step=%s tick=%s oracle=%s",
        market.symbol,
        market.min_qty,
        market.qty_step,
        market.tick_size,
        market.oracle_price,
    )
    return market


@pytest.fixture(scope="session")
def rl_ws_exec_url() -> str:
    """ws-exec relayer URL, shared with the existing ws-exec suite."""
    url = os.environ.get("REYA_WS_EXEC_URL")
    if not url:
        pytest.skip("ws-exec rate-limit parity needs REYA_WS_EXEC_URL")
    return url


@pytest.fixture(scope="session")
def rl_eject_hook(rl_suite_config: rl_config.RateLimitSuiteConfig):  # pylint: disable=redefined-outer-name
    """A pluggable eject/un-eject hook driven by operator-supplied commands.

    ``rl_ejected_accounts`` lives in the off-chain Postgres, which this repo has
    no access to, so the eject step is delegated to command templates the
    localnet harness provides:

        RL_TEST_EJECT_CMD="<cmd with {wallet} and/or {account_id}>"
        RL_TEST_UNEJECT_CMD="<cmd with {wallet} and/or {account_id}>"

    Both must be set or the eject test skips.
    """
    if not (rl_suite_config.eject_cmd and rl_suite_config.uneject_cmd):
        pytest.skip("eject coverage needs RL_TEST_EJECT_CMD and RL_TEST_UNEJECT_CMD (see tests/rate_limits/README.md)")

    def run(template: str, *, wallet: str, account_id: int, label: str) -> None:
        command = template.format(wallet=wallet, account_id=account_id)
        logger.info("rate-limit %s hook: %s", label, command)
        result = subprocess.run(  # nosec B603 — argv from an operator-supplied template, never shell-interpolated
            shlex.split(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=rl_suite_config.timing.eject_timeout_s,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"{label} hook failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}"
            )

    class _EjectHook:
        """Runs the configured eject / un-eject commands for one wallet."""

        def eject(self, *, wallet: str, account_id: int) -> None:
            run(str(rl_suite_config.eject_cmd), wallet=wallet, account_id=account_id, label="eject")

        def uneject(self, *, wallet: str, account_id: int) -> None:
            run(str(rl_suite_config.uneject_cmd), wallet=wallet, account_id=account_id, label="un-eject")

    return _EjectHook()


@pytest_asyncio.fixture(loop_scope="session", scope="function", autouse=True)
async def rl_isolation(  # pylint: disable=redefined-outer-name
    request,
    rl_suite_config: rl_config.RateLimitSuiteConfig,
    rl_standard_client_provider: Callable[[], Awaitable[ReyaTradingClient | None]],
) -> AsyncIterator[None]:
    """Per-test isolation for the LIVE rate-limit tests.

    Teardown flattens the account and then sleeps long enough for a drained
    GCRA place bucket to refill, so one bucket-exhausting test cannot poison
    the next one. Offline tests and unwired sessions never touch the network.
    """
    if request.node.get_closest_marker("offline") is not None:
        yield
        return

    client = await rl_standard_client_provider()
    if client is None:
        yield
        return

    yield

    # Scoped to the suite's own market so a shared test account keeps whatever
    # it is doing on other markets.
    await ensure_flat(client, rl_suite_config, rl_suite_config.symbol)
    await asyncio.sleep(rl_suite_config.timing.bucket_recovery_s)
