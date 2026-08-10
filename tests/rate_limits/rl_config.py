"""Environment-driven configuration for the Rate-Limit v1 suite.

Every knob below is read from the environment at import time with a default
that matches the Standard-tier shape of the Rate-Limit v1 design. The suite is
deliberately configuration-driven rather than load-generating: the localnet
deployment is expected to expose SMALL Standard-tier limits so a handful of
requests trips them, and to seed the standard test wallets into ``rl_whitelist``
(heavy wallets into ``rl_market_makers``).

See ``tests/rate_limits/README.md`` for the full knob contract — that file and
this module must stay in sync.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal

import pytest

# --------------------------------------------------------------------------
# Public error codes (the FINAL Rate-Limit v1 wire contract).
# Kept as plain strings, deliberately NOT the generated ``RequestErrorCode``
# enum: the specs are not tagged yet, so the generated enum does not know most
# of these codes and typed parsing would raise before a test could assert.
# --------------------------------------------------------------------------

RATE_LIMITED_ERROR = "RATE_LIMITED_ERROR"
OPEN_ORDER_COUNT_EXCEEDED_ERROR = "OPEN_ORDER_COUNT_EXCEEDED_ERROR"
OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR = "OPEN_ORDER_NOTIONAL_EXCEEDED_ERROR"
CAPACITY_LIMITED_ERROR = "CAPACITY_LIMITED_ERROR"
ACCOUNT_SUSPENDED_ERROR = "ACCOUNT_SUSPENDED_ERROR"
NOT_WHITELISTED_ERROR = "NOT_WHITELISTED_ERROR"

#: Statuses the edge uses for the per-account / cap rejects and the 403-class
#: gate rejects. ``Retry-After`` is mandatory on 429 and optional on 503.
HTTP_RATE_LIMITED = 429
HTTP_CAPACITY_LIMITED = 503
HTTP_FORBIDDEN = 403

#: An ejected account may be rejected by the edge (it was removed from
#: ``rl_whitelist`` in the same transaction) or by the matching engine (the
#: ``rl_ejected_accounts`` admission check). Both are 403-class; tests accept
#: either and record which one the deployment actually produced.
EJECT_REJECT_CODES = (NOT_WHITELISTED_ERROR, ACCOUNT_SUSPENDED_ERROR)


# --------------------------------------------------------------------------
# Env plumbing
# --------------------------------------------------------------------------


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return Decimal(default)
    return Decimal(raw.strip())


def _env_str(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


# --------------------------------------------------------------------------
# Suite gate
# --------------------------------------------------------------------------

RL_ENABLED_ENV = "RL_TEST_ENABLED"

#: Whole-suite opt-in. Every live module carries ``requires_rate_limits`` in
#: its ``pytestmark`` so the suite collects (and therefore proves it imports)
#: on every environment but only RUNS where rate limits are deployed.
RATE_LIMITS_ENABLED = _env_flag(RL_ENABLED_ENV)

requires_rate_limits = pytest.mark.skipif(
    not RATE_LIMITS_ENABLED,
    reason=(
        f"rate-limit coverage is opt-in: set {RL_ENABLED_ENV}=1 on a deployment that has "
        "Rate-Limit v1 enabled with small Standard-tier limits (see tests/rate_limits/README.md)"
    ),
)


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountCredentials:
    """One signing identity: the account id, its key, and its owner wallet."""

    account_id: int
    private_key: str
    wallet_address: str


_STANDARD_ENV = ("RL_TEST_ACCOUNT_ID", "RL_TEST_PRIVATE_KEY", "RL_TEST_WALLET_ADDRESS")
_STANDARD_FALLBACK_ENV = ("SPOT_ACCOUNT_ID_1", "SPOT_PRIVATE_KEY_1", "SPOT_WALLET_ADDRESS_1")
_NON_WHITELISTED_ENV = (
    "RL_TEST_NON_WHITELISTED_ACCOUNT_ID",
    "RL_TEST_NON_WHITELISTED_PRIVATE_KEY",
    "RL_TEST_NON_WHITELISTED_WALLET_ADDRESS",
)
_TRIGGER_ENV = (
    "RL_TEST_TRIGGER_ACCOUNT_ID",
    "RL_TEST_TRIGGER_PRIVATE_KEY",
    "RL_TEST_TRIGGER_WALLET_ADDRESS",
)


def _resolve_credentials(names: tuple[str, str, str]) -> AccountCredentials | None:
    account_id, private_key, wallet_address = (os.environ.get(name, "").strip() for name in names)
    if not (account_id and private_key and wallet_address):
        return None
    return AccountCredentials(
        account_id=int(account_id),
        private_key=private_key,
        wallet_address=wallet_address,
    )


def standard_credentials() -> AccountCredentials | None:
    """The whitelisted, Standard-tier account the bucket/cap tests hammer.

    Falls back to ``SPOT_*_1`` so an already-configured localnet .env works
    without new variables.
    """
    return _resolve_credentials(_STANDARD_ENV) or _resolve_credentials(_STANDARD_FALLBACK_ENV)


def standard_credentials_env_hint() -> str:
    return f"{' / '.join(_STANDARD_ENV)} (or {' / '.join(_STANDARD_FALLBACK_ENV)})"


def non_whitelisted_credentials() -> AccountCredentials | None:
    """An account the localnet deployment deliberately did NOT seed into
    ``rl_whitelist``. No fallback: guessing here would silently turn the
    whitelist-gate test into a false pass on a whitelisted wallet."""
    return _resolve_credentials(_NON_WHITELISTED_ENV)


def non_whitelisted_credentials_env_hint() -> str:
    return " / ".join(_NON_WHITELISTED_ENV)


def trigger_credentials() -> AccountCredentials | None:
    """The PERP identity that arms the retention probe's protective stop.

    No fallback to the Standard account: SL/TP is perp-only while the rest of
    the suite is spot-only, and inheriting the spot identity is what let the
    probe arm against a mis-wired account and report "not probed" on a green
    test. The eject hook ejects by OWNER WALLET, so the wiring must point this
    triple at an account owned by the same wallet as the Standard account —
    the eject test asserts that rather than assuming it.
    """
    return _resolve_credentials(_TRIGGER_ENV)


def trigger_credentials_env_hint() -> str:
    return " / ".join(_TRIGGER_ENV)


# --------------------------------------------------------------------------
# Tier parameters + timings
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StandardTierLimits:
    """The Standard-tier limits the deployment under test is configured with.

    Tests never rely on the exact GCRA arithmetic — these values only size the
    "how many attempts before we give up looking for a reject" bounds and the
    pacing that keeps a cap test from tripping the rate bucket first.
    """

    place_per_min: int
    place_burst: int
    cancel_per_min: int
    cancel_burst: int
    bulk_cancel_per_min: int
    bulk_cancel_burst: int
    cod_control_per_min: int
    cod_control_burst: int
    open_order_count_cap: int
    open_order_per_market_cap: int
    open_notional_cap: Decimal

    @property
    def place_interval_s(self) -> float:
        """Sustained seconds-per-op implied by the place rate."""
        return 60.0 / max(1, self.place_per_min)


@dataclass(frozen=True)
class SuiteTiming:
    """Timeouts, pacing, and plausibility bounds. All generous by design."""

    poll_interval_s: float
    eject_timeout_s: float
    max_burst_attempts: int
    bucket_recovery_s: float
    place_pace_s: float
    retry_after_max_s: float
    retry_after_slack_s: float
    settle_timeout_s: float


@dataclass(frozen=True)
class RateLimitSuiteConfig:
    """Everything the suite reads from the environment, resolved once."""

    symbol: str
    trigger_symbol: str | None
    unknown_account_id: int
    ws_inbound_msg_burst: int
    limits: StandardTierLimits
    timing: SuiteTiming
    eject_cmd: str | None
    uneject_cmd: str | None

    def burst_attempt_bound(self) -> int:
        """Upper bound on create attempts before we declare "no reject seen".

        Sized well above the burst tolerance plus a few seconds of sustained
        refill, then clamped by ``RL_TEST_MAX_BURST_ATTEMPTS`` so a
        misconfigured deployment cannot spin forever.
        """
        return self.bucket_attempt_bound(self.limits.place_per_min, self.limits.place_burst)

    def bucket_attempt_bound(self, per_min: int, burst: int) -> int:
        """The same sizing for any of the four §4.1 buckets."""
        refill_slack = math.ceil(per_min / 60.0) * 5
        return min(self.timing.max_burst_attempts, burst + refill_slack + 20)


def load_suite_config() -> RateLimitSuiteConfig:
    """Resolve every knob from the environment (defaults documented in README)."""
    limits = StandardTierLimits(
        place_per_min=_env_int("RL_TEST_STANDARD_PLACE_PER_MIN", 60),
        place_burst=_env_int("RL_TEST_STANDARD_PLACE_BURST", 5),
        cancel_per_min=_env_int("RL_TEST_STANDARD_CANCEL_PER_MIN", 120),
        cancel_burst=_env_int("RL_TEST_STANDARD_CANCEL_BURST", 10),
        bulk_cancel_per_min=_env_int("RL_TEST_STANDARD_BULK_CANCEL_PER_MIN", 10),
        bulk_cancel_burst=_env_int("RL_TEST_STANDARD_BULK_CANCEL_BURST", 5),
        cod_control_per_min=_env_int("RL_TEST_COD_CONTROL_PER_MIN", 100),
        cod_control_burst=_env_int("RL_TEST_COD_CONTROL_BURST", 10),
        open_order_count_cap=_env_int("RL_TEST_STANDARD_OPEN_ORDER_COUNT_CAP", 10),
        open_order_per_market_cap=_env_int("RL_TEST_STANDARD_OPEN_ORDER_PER_MARKET_CAP", 5),
        open_notional_cap=_env_decimal("RL_TEST_STANDARD_OPEN_NOTIONAL_CAP", "5000"),
    )

    # A GCRA cell refills at the sustained rate, so a fully-drained burst is
    # back after burst * interval seconds. Sleeping that between tests keeps
    # one bucket-exhausting test from poisoning the next one.
    default_recovery_s = min(30.0, max(1.0, limits.place_burst * limits.place_interval_s * 1.5))

    timing = SuiteTiming(
        poll_interval_s=_env_float("RL_TEST_POLL_INTERVAL_S", 2.0),
        eject_timeout_s=_env_float("RL_TEST_EJECT_TIMEOUT_S", 60.0),
        max_burst_attempts=_env_int("RL_TEST_MAX_BURST_ATTEMPTS", 200),
        bucket_recovery_s=_env_float("RL_TEST_BUCKET_RECOVERY_S", default_recovery_s),
        place_pace_s=_env_float("RL_TEST_PLACE_PACE_S", limits.place_interval_s * 1.25),
        retry_after_max_s=_env_float("RL_TEST_RETRY_AFTER_MAX_S", 60.0),
        retry_after_slack_s=_env_float("RL_TEST_RETRY_AFTER_SLACK_S", 1.0),
        settle_timeout_s=_env_float("RL_TEST_SETTLE_TIMEOUT_S", 30.0),
    )

    return RateLimitSuiteConfig(
        symbol=_env_str("RL_TEST_SYMBOL", "WETHRUSD"),
        # Optional and deliberately without a default: SL/TP is perp-only while
        # the rest of the suite is spot-only, so the eject test can only prove
        # trigger RETENTION where the wiring points it at a perp market.
        trigger_symbol=os.environ.get("RL_TEST_TRIGGER_SYMBOL") or None,
        unknown_account_id=_env_int("RL_TEST_UNKNOWN_ACCOUNT_ID", 999_999_999),
        ws_inbound_msg_burst=_env_int("RL_TEST_WS_INBOUND_MSG_BURST", 100),
        limits=limits,
        timing=timing,
        eject_cmd=os.environ.get("RL_TEST_EJECT_CMD") or None,
        uneject_cmd=os.environ.get("RL_TEST_UNEJECT_CMD") or None,
    )
