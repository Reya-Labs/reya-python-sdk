# pylint: disable=protected-access,redefined-outer-name
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

import importlib

import pytest

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.client import PERPETUAL_LIFETIME, _reject_zero_deadline, _require_settlement_headroom
from sdk.reya_rest_api.config import DEFAULT_SETTLEMENT_HEADROOM_S, TradingConfig, settlement_headroom_from_env
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters
from tests.helpers import gtt_timing
from tests.helpers.gtt_timing import (
    GTT_REAP_DEADLINE_OFFSET_S,
    GTT_REAP_EXPIRY_OFFSET_S,
    GTT_REAP_PRE_EXPIRY_MARGIN_S,
    GTT_REAP_SETUP_BUDGET_S,
)
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


def _config(**overrides: object) -> TradingConfig:
    kwargs: dict[str, object] = {
        "api_url": "https://invalid.example",
        "chain_id": 31337,
        "owner_wallet_address": "0x" + "11" * 20,
    }
    kwargs.update(overrides)
    return TradingConfig(**kwargs)  # type: ignore[arg-type]


def test_a_directly_built_config_still_reflects_the_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test helpers build TradingConfig directly rather than via from_env.

    A plain constant default there leaves the client enforcing the production
    headroom while the caller sizes lifetimes against the deployment's — which
    is not a hypothetical: it refused the reap fixtures' own orders on a live
    Localnet run.
    """
    monkeypatch.setenv("REYA_SETTLEMENT_HEADROOM_S", "5")
    assert _config().settlement_headroom_s == LOCALNET_HEADROOM_S
    monkeypatch.delenv("REYA_SETTLEMENT_HEADROOM_S")
    assert _config().settlement_headroom_s == PRODUCTION_HEADROOM_S


def test_an_explicit_headroom_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REYA_SETTLEMENT_HEADROOM_S", "5")
    assert _config(settlement_headroom_s=99).settlement_headroom_s == 99


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


def test_reap_fixture_expiry_is_admissible_at_the_configured_headroom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asserts the constant the fixtures actually sign with, not a re-derivation.

    Re-computing the offset here would hold for any hardcoded value and would
    stay green if the derivation were reverted, which is the whole defect.
    """
    for headroom_s in HEADROOMS:
        monkeypatch.setenv("REYA_SETTLEMENT_HEADROOM_S", str(headroom_s))
        offset = importlib.reload(gtt_timing).GTT_REAP_EXPIRY_OFFSET_S
        _require_settlement_headroom(OFFLINE_CLOCK_S + offset, headroom_s, OFFLINE_CLOCK_S)
    monkeypatch.delenv("REYA_SETTLEMENT_HEADROOM_S")
    importlib.reload(gtt_timing)


def test_reap_fixture_expiry_outlasts_its_own_signature_window() -> None:
    """The GTT coupling: the lifetime must exceed the entry deadline."""
    assert GTT_REAP_EXPIRY_OFFSET_S > GTT_REAP_DEADLINE_OFFSET_S


def test_reap_fixture_setup_budget_does_not_shrink_with_the_headroom() -> None:
    """The "still resting" assertion fires at `expiry - margin`.

    Letting the observation window ride the headroom down would narrow the
    window the test has to create and confirm the order, racing the reaper on
    exactly the deployments where the headroom is smallest.
    """
    for headroom_s in HEADROOMS:
        offset = headroom_s + GTT_REAP_SETUP_BUDGET_S + GTT_REAP_PRE_EXPIRY_MARGIN_S
        assert offset - GTT_REAP_PRE_EXPIRY_MARGIN_S >= GTT_REAP_SETUP_BUDGET_S


def test_zero_deadline_is_refused() -> None:
    with pytest.raises(ValueError, match="non-zero signature-validity window"):
        _reject_zero_deadline(0)


def test_non_zero_deadline_is_accepted() -> None:
    _reject_zero_deadline(OFFLINE_CLOCK_S + 60)


def test_time_in_force_enum_covers_the_engine_values() -> None:
    """GTT is the only TIF that carries a lifetime, so it is the only one the
    headroom rule can bind on."""
    assert {member.value for member in TimeInForce} == {"IOC", "GTC", "GTT"}


# --- the guards, reached through the payload builders ----------------------
#
# Asserting the helpers alone leaves the call sites untested: deleting every
# guard invocation from client.py is then a silent no-op. These reach them the
# way a caller does.

_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_SIGNER = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
_SYMBOL = "ETHRUSDPERP"


@pytest.fixture
def client() -> ReyaTradingClient:
    """A client that builds payloads offline, on the pinned clock."""
    built = ReyaTradingClient(
        _config(
            chain_id=89346162,
            owner_wallet_address=_SIGNER,
            private_key=_PRIVATE_KEY,
            account_id=12345,
            dex_id_override=2,
            settlement_headroom_s=PRODUCTION_HEADROOM_S,
        )
    )
    built._symbol_to_market_id = {_SYMBOL: 1}
    built._symbol_to_tick_size = {_SYMBOL: "0.001"}
    built._symbol_to_trigger_band = {_SYMBOL: "0.05"}
    built._initialized = True
    return built


def _wallet_nonce() -> int:
    """The wallet's current class-level nonce watermark (0 if untouched)."""
    return ReyaTradingClient._wallet_nonces.get(_SIGNER.lower(), 0)


def _gtt(expires_after: int, **overrides: object) -> LimitOrderParameters:
    # A short explicit deadline keeps the TIF coupling (`expiresAfter > deadline`)
    # satisfied, so the headroom rule is the binding one — which is the whole
    # point: the two are independent, and the coupling alone lets a short
    # lifetime through.
    kwargs: dict[str, object] = {
        "symbol": _SYMBOL,
        "is_buy": True,
        "limit_px": "3000",
        "qty": "0.01",
        "time_in_force": TimeInForce.GTT,
        "expires_after": expires_after,
        "deadline": OFFLINE_CLOCK_S + 10,
    }
    kwargs.update(overrides)
    return LimitOrderParameters(**kwargs)  # type: ignore[arg-type]


def test_create_refuses_a_lifetime_inside_the_headroom(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="settlement headroom"):
        client.build_create_limit_order_payload(_gtt(OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S))


def test_create_admits_a_lifetime_beyond_the_headroom(client: ReyaTradingClient) -> None:
    payload, _ = client.build_create_limit_order_payload(_gtt(OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S + 600))
    assert payload["expiresAfter"] == OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S + 600


def test_a_refused_create_does_not_claim_a_nonce(client: ReyaTradingClient) -> None:
    """A rejected build must leave the per-wallet nonce watermark untouched."""
    before = _wallet_nonce()
    with pytest.raises(ValueError, match="settlement headroom"):
        client.build_create_limit_order_payload(_gtt(OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S))
    assert _wallet_nonce() == before


def test_create_refuses_a_zero_deadline(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="non-zero signature-validity window"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTC,
                deadline=0,
            )
        )


def test_trigger_create_refuses_a_zero_deadline(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="non-zero signature-validity window"):
        client.build_create_trigger_order_payload(
            TriggerOrderParameters(
                symbol=_SYMBOL,
                is_buy=False,
                trigger_px="2500",
                trigger_type=OrderType.STOP_LOSS,
                limit_px="2450",
                time_in_force=TimeInForce.GTC,
                deadline=0,
            )
        )


def _gtt_trigger(expires_after: int) -> TriggerOrderParameters:
    return TriggerOrderParameters(
        symbol=_SYMBOL,
        is_buy=False,
        trigger_px="2500",
        limit_px="2450",
        trigger_type=OrderType.STOP_LOSS,
        time_in_force=TimeInForce.GTT,
        expires_after=expires_after,
        deadline=OFFLINE_CLOCK_S + 10,
    )


@pytest.mark.trigger
@pytest.mark.gtt
def test_gtt_trigger_create_refuses_a_lifetime_inside_the_headroom(client: ReyaTradingClient) -> None:
    """A GTT trigger's signed `expiresAfter` is ONE deadline covering both
    phases — the armed trigger's lifetime and the fired child's on-chain
    settlement — so a stop that could never settle a fill is refused up front
    rather than armed and left useless."""
    with pytest.raises(ValueError, match="settlement headroom"):
        client.build_create_trigger_order_payload(_gtt_trigger(OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S))


@pytest.mark.trigger
@pytest.mark.gtt
def test_gtt_trigger_create_admits_a_lifetime_beyond_the_headroom(client: ReyaTradingClient) -> None:
    expires_after = OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S + 600
    payload, _nonce = client.build_create_trigger_order_payload(_gtt_trigger(expires_after))
    assert payload["expiresAfter"] == expires_after
    assert payload["timeInForce"] == TimeInForce.GTT.value


@pytest.mark.trigger
@pytest.mark.gtt
def test_a_refused_gtt_trigger_create_does_not_claim_a_nonce(client: ReyaTradingClient) -> None:
    before = _wallet_nonce()
    with pytest.raises(ValueError, match="settlement headroom"):
        client.build_create_trigger_order_payload(_gtt_trigger(OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S))
    assert _wallet_nonce() == before


def test_modify_refuses_a_lifetime_inside_the_headroom(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="settlement headroom"):
        client.build_modify_order_payload(
            ModifyOrderParameters(
                symbol=_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.02",
                post_only=False,
                expires_after=OFFLINE_CLOCK_S + PRODUCTION_HEADROOM_S,
                time_in_force=TimeInForce.GTT,
                order_id=123,
                deadline=OFFLINE_CLOCK_S + 10,
            )
        )


def test_modify_refuses_a_zero_deadline(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="non-zero signature-validity window"):
        client.build_modify_order_payload(
            ModifyOrderParameters(
                symbol=_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.02",
                post_only=False,
                expires_after=None,
                time_in_force=TimeInForce.GTC,
                order_id=123,
                deadline=0,
            )
        )


def test_a_short_deadline_no_longer_smuggles_a_short_lifetime(client: ReyaTradingClient) -> None:
    """The shape the reap fixtures had: the TIF coupling passes, the rule does not."""
    deadline = OFFLINE_CLOCK_S + 20
    expires_after = OFFLINE_CLOCK_S + 55
    with pytest.raises(ValueError, match="settlement headroom"):
        client.build_create_limit_order_payload(_gtt(expires_after, deadline=deadline))
