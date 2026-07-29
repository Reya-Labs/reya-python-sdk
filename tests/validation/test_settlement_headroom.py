"""Client-side admission rules for order lifetimes and envelope deadlines.

Offline. The matching engine's admission rules for `expiresAfter` are pure
functions of the request and the deployment's settlement headroom, so an order
that violates one is refused deterministically — asserting them here is what
makes them coverage. A live run cannot do it alone: a deployment pinned to a
small headroom admits lifetimes the production 60s headroom refuses, so a live
pass proves "admissible here", not "admissible".

The rules, as the engine states them:

* `expiresAfter == 0` means no lifetime and is exempt from every timing check.
* otherwise `expiresAfter` must be STRICTLY greater than
  `now + settlement_headroom` — equal to the boundary is refused.
* a signed envelope `deadline` must be non-zero; zero makes the settlement
  calldata builder infer it from `expiresAfter` and recover the wrong signer.
"""

from __future__ import annotations

import pytest

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.client import (
    PERPETUAL_LIFETIME,
    _reject_zero_deadline,
    _require_settlement_headroom,
)
from sdk.reya_rest_api.config import DEFAULT_SETTLEMENT_HEADROOM_S, TradingConfig, settlement_headroom_from_env
from tests.engine.test_gtt_lifecycle import GTT_REAP_OBSERVATION_WINDOW_S
from tests.helpers.offline_clock import OFFLINE_CLOCK_S

pytestmark = pytest.mark.offline

# The headroom Localnet pins, and the production default. An assertion that
# only holds at one of them is an artefact of the deployment, not a rule — so
# every timing case below runs against both.
LOCALNET_HEADROOM_S = 5
PRODUCTION_HEADROOM_S = DEFAULT_SETTLEMENT_HEADROOM_S

HEADROOMS = [LOCALNET_HEADROOM_S, PRODUCTION_HEADROOM_S]


def test_production_headroom_is_the_default() -> None:
    """Guessing low would sign orders the deployment refuses, so the default is
    the production value; a deployment running a smaller one opts in."""
    assert PRODUCTION_HEADROOM_S == 60


def test_headroom_reads_the_deployment_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REYA_SETTLEMENT_HEADROOM_S", "5")
    assert settlement_headroom_from_env() == LOCALNET_HEADROOM_S
    monkeypatch.delenv("REYA_SETTLEMENT_HEADROOM_S")
    assert settlement_headroom_from_env() == PRODUCTION_HEADROOM_S


def test_config_carries_the_headroom() -> None:
    config = TradingConfig(api_url="https://invalid.example", chain_id=31337, owner_wallet_address="0x" + "11" * 20)
    assert config.settlement_headroom_s == PRODUCTION_HEADROOM_S


@pytest.mark.parametrize("headroom_s", HEADROOMS)
def test_expiry_on_the_boundary_is_refused(headroom_s: int) -> None:
    """The rule is strict: landing exactly on `now + headroom` is not enough."""
    with pytest.raises(ValueError, match="settlement headroom"):
        _require_settlement_headroom(OFFLINE_CLOCK_S + headroom_s, headroom_s, OFFLINE_CLOCK_S)


@pytest.mark.parametrize("headroom_s", HEADROOMS)
def test_expiry_one_second_beyond_the_boundary_is_admitted(headroom_s: int) -> None:
    _require_settlement_headroom(OFFLINE_CLOCK_S + headroom_s + 1, headroom_s, OFFLINE_CLOCK_S)


@pytest.mark.parametrize("headroom_s", HEADROOMS)
def test_expiry_already_in_the_past_is_refused(headroom_s: int) -> None:
    with pytest.raises(ValueError, match="settlement headroom"):
        _require_settlement_headroom(OFFLINE_CLOCK_S - 1, headroom_s, OFFLINE_CLOCK_S)


@pytest.mark.parametrize("headroom_s", HEADROOMS)
def test_no_lifetime_is_exempt(headroom_s: int) -> None:
    _require_settlement_headroom(PERPETUAL_LIFETIME, headroom_s, OFFLINE_CLOCK_S)


def test_a_short_deadline_does_not_excuse_a_short_lifetime() -> None:
    """The headroom rule is independent of the `expiresAfter > deadline` coupling.

    A caller that pins a short deadline satisfies the coupling while still
    signing a lifetime the engine refuses — which is precisely the shape the
    GTT reap fixtures had: `deadline = now + 20`, `expiresAfter = now + 55`,
    admissible only where the headroom is under 55s.
    """
    deadline = OFFLINE_CLOCK_S + 20
    expires_after = OFFLINE_CLOCK_S + 55
    assert expires_after > deadline
    with pytest.raises(ValueError, match="settlement headroom"):
        _require_settlement_headroom(expires_after, PRODUCTION_HEADROOM_S, OFFLINE_CLOCK_S)


def test_reap_fixture_expiry_clears_every_headroom() -> None:
    """The reap fixtures derive their expiry from the headroom, so they stay
    admissible on a production-headroom deployment as well as a local one."""
    for headroom_s in HEADROOMS:
        expires_after = OFFLINE_CLOCK_S + headroom_s + GTT_REAP_OBSERVATION_WINDOW_S
        _require_settlement_headroom(expires_after, headroom_s, OFFLINE_CLOCK_S)


def test_zero_deadline_is_refused() -> None:
    with pytest.raises(ValueError, match="non-zero signature-validity window"):
        _reject_zero_deadline(0)


def test_non_zero_deadline_is_accepted() -> None:
    _reject_zero_deadline(OFFLINE_CLOCK_S + 60)


def test_time_in_force_enum_covers_the_engine_values() -> None:
    """GTT is the only TIF that carries a lifetime, so it is the only one the
    headroom rule can bind on."""
    assert {member.value for member in TimeInForce} == {"IOC", "GTC", "GTT"}
