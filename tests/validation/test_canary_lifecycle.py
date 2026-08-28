"""Offline tests for the run-owned PRO-657 canary order lifecycle."""

from __future__ import annotations

from typing import Any

import asyncio
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from scripts.canary_lifecycle import (
    LifecycleError,
    OpenOrderUnverifiedError,
    OrderExpectation,
    OrderPlan,
    build_lifecycle_evidence,
    derive_client_order_id,
    run_order_lifecycle,
)
from scripts.canary_preflight import (
    MAINNET_MUTATION_ACKNOWLEDGEMENT,
    SUPPORTED_ENVIRONMENTS,
    CanaryPolicy,
    CanaryProfile,
)

pytestmark = pytest.mark.offline


class FakeLifecycleAdapter:
    """Records the exact surface calls without constructing an SDK client."""

    def __init__(
        self,
        *,
        order_id: str = "123456789",
        fail_once: str | None = None,
        fail_always: str | None = None,
        hang_once: str | None = None,
        unverified_open: bool = False,
    ) -> None:
        self.order_id = order_id
        self.fail_once = fail_once
        self.fail_always = fail_always
        self.hang_once = hang_once
        self.unverified_open = unverified_open
        self.calls: list[str] = []

    async def _record(self, call: str) -> None:
        self.calls.append(call)
        if self.hang_once == call:
            self.hang_once = None
            await asyncio.Event().wait()
        if self.fail_once == call:
            self.fail_once = None
            raise RuntimeError("synthetic failure")
        if self.fail_always == call:
            raise RuntimeError("synthetic failure")

    async def place_post_only_gtc(self, plan: OrderPlan, client_order_id: int) -> str:
        del plan, client_order_id
        await self._record("place")
        if self.unverified_open:
            raise OpenOrderUnverifiedError(self.order_id, "synthetic invariant failure")
        return self.order_id

    async def modify_post_only_gtc(self, order_id: str, plan: OrderPlan, client_order_id: int) -> None:
        del plan, client_order_id
        await self._record(f"modify:{order_id}")

    async def cancel_order(self, order_id: str, plan: OrderPlan) -> None:
        del plan
        await self._record(f"cancel:{order_id}")

    async def wait_rest(self, expectation: OrderExpectation, plan: OrderPlan, timeout_s: float) -> None:
        del plan, timeout_s
        await self._record(_observation_call("rest", expectation))

    async def wait_ws(self, expectation: OrderExpectation, plan: OrderPlan, timeout_s: float) -> None:
        del plan, timeout_s
        await self._record(_observation_call("ws", expectation))


def _observation_call(surface: str, expectation: OrderExpectation) -> str:
    price = "none" if expectation.limit_px is None else str(expectation.limit_px)
    return f"{surface}:{expectation.status}:{price}:{expectation.order_id}"


@pytest.fixture(name="profile")
def fixture_profile() -> CanaryProfile:
    return CanaryProfile(
        name="devnet1-pro-657",
        enabled=True,
        environment="devnet1",
        identity=SUPPORTED_ENVIRONMENTS["devnet1"],
        release_manifest_id="perp-ob-f989de0",
        rpc_url_env="REYA_CANARY_RPC_URL",
        policy=CanaryPolicy(
            market_symbol="BTCRUSDPERP",
            market_id=1,
            max_quantity=Decimal("2"),
            max_notional=Decimal("1000"),
            allowed_account_ids=(42,),
            allowed_wallet_addresses=("0x1111111111111111111111111111111111111111",),
        ),
    )


@pytest.fixture(name="plan")
def fixture_plan() -> OrderPlan:
    return OrderPlan(
        account_id=42,
        wallet_address="0x1111111111111111111111111111111111111111",
        market_symbol="BTCRUSDPERP",
        market_id=1,
        is_buy=True,
        quantity=Decimal("1"),
        initial_limit_px=Decimal("100"),
        modified_limit_px=Decimal("101"),
    )


def test_client_order_id_is_stable_nonzero_and_rejects_unsafe_run_ids() -> None:
    first = derive_client_order_id("cutover-20260828T1100Z")
    assert first == derive_client_order_id("cutover-20260828T1100Z")
    assert 0 < first < 2**63
    assert first != derive_client_order_id("cutover-20260828T1101Z")
    with pytest.raises(LifecycleError, match="safe filename"):
        derive_client_order_id("contains whitespace")


@pytest.mark.asyncio
async def test_lifecycle_proves_initial_modified_and_cancelled_state_on_both_surfaces(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter()

    result = await run_order_lifecycle(profile, plan, adapter, run_id="run-001")

    assert result.owned_order_ids == ("123456789",)
    assert result.client_order_id == derive_client_order_id("run-001")
    assert [event.step for event in result.events] == [
        "place.accepted",
        "rest.open",
        "ws.open",
        "modify.accepted",
        "rest.open",
        "ws.open",
        "cancel.accepted",
        "rest.cancelled",
        "ws.cancelled",
    ]
    assert adapter.calls == [
        "place",
        "rest:OPEN:100:123456789",
        "ws:OPEN:100:123456789",
        "modify:123456789",
        "rest:OPEN:101:123456789",
        "ws:OPEN:101:123456789",
        "cancel:123456789",
        "rest:CANCELLED:none:123456789",
        "ws:CANCELLED:none:123456789",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"account_id": 99}, "account ID is not allowlisted"),
        ({"wallet_address": "0x2222222222222222222222222222222222222222"}, "wallet address is not allowlisted"),
        ({"market_symbol": "ETHRUSDPERP"}, "market symbol does not match"),
        ({"market_id": 2}, "market ID does not match"),
        ({"quantity": Decimal("3")}, "quantity exceeds"),
        ({"quantity": Decimal("NaN")}, "quantity must be finite"),
        ({"modified_limit_px": Decimal("1001")}, "modified order notional exceeds"),
        ({"initial_limit_px": Decimal("Infinity")}, "limit prices must be finite"),
        ({"modified_limit_px": Decimal("100")}, "must differ"),
    ],
)
async def test_plan_validation_fails_before_adapter_calls(
    profile: CanaryProfile,
    plan: OrderPlan,
    override: dict[str, Any],
    message: str,
) -> None:
    adapter = FakeLifecycleAdapter()

    with pytest.raises(LifecycleError, match=message):
        await run_order_lifecycle(profile, replace(plan, **override), adapter, run_id="run-invalid")

    assert not adapter.calls


@pytest.mark.asyncio
async def test_invalid_timeout_fails_before_adapter_calls(profile: CanaryProfile, plan: OrderPlan) -> None:
    adapter = FakeLifecycleAdapter()

    with pytest.raises(LifecycleError, match="timeout must be finite"):
        await run_order_lifecycle(profile, plan, adapter, run_id="run-invalid-timeout", timeout_s=float("nan"))

    assert not adapter.calls


@pytest.mark.asyncio
async def test_visibility_failure_cleans_up_only_the_created_order(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter(fail_once="rest:OPEN:100:123456789")

    with pytest.raises(LifecycleError) as raised:
        await run_order_lifecycle(profile, plan, adapter, run_id="run-cleanup")

    assert raised.value.stage == "rest-visibility"
    assert raised.value.cleanup_failures == ()
    assert adapter.calls == [
        "place",
        "rest:OPEN:100:123456789",
        "cancel:123456789",
        "rest:CANCELLED:none:123456789",
        "ws:CANCELLED:none:123456789",
    ]
    assert not any("999999999" in call for call in adapter.calls)


@pytest.mark.asyncio
async def test_cleanup_failure_is_reported_without_raw_exception_text(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter(
        fail_once="modify:123456789",
        fail_always="cancel:123456789",
    )

    with pytest.raises(LifecycleError) as raised:
        await run_order_lifecycle(profile, plan, adapter, run_id="run-cleanup-failure")

    assert raised.value.stage == "modify"
    assert raised.value.detail == "RuntimeError"
    assert raised.value.cleanup_failures == ("123456789[cancel:RuntimeError]",)
    assert "synthetic failure" not in str(raised.value)


@pytest.mark.asyncio
async def test_visibility_timeout_is_bounded_and_still_cleans_up(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter(hang_once="ws:OPEN:100:123456789")

    with pytest.raises(LifecycleError) as raised:
        await run_order_lifecycle(profile, plan, adapter, run_id="run-timeout", timeout_s=0.01)

    assert raised.value.stage == "ws-visibility"
    assert raised.value.detail == "TimeoutError"
    assert "cancel:123456789" in adapter.calls


@pytest.mark.asyncio
async def test_invalid_order_id_is_not_added_to_owned_cleanup_registry(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter(order_id="not-an-order-id")

    with pytest.raises(LifecycleError, match="invalid canonical order ID"):
        await run_order_lifecycle(profile, plan, adapter, run_id="run-bad-id")

    assert adapter.calls == ["place"]


@pytest.mark.asyncio
async def test_unverified_open_response_is_registered_before_exact_cleanup(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter(unverified_open=True)

    with pytest.raises(LifecycleError) as raised:
        await run_order_lifecycle(profile, plan, adapter, run_id="run-unverified-open")

    assert [event.step for event in raised.value.events] == ["place.unverified"]
    assert raised.value.cleanup_failures == ()
    assert adapter.calls == [
        "place",
        "cancel:123456789",
        "rest:CANCELLED:none:123456789",
        "ws:CANCELLED:none:123456789",
    ]


@pytest.mark.asyncio
async def test_mainnet_requires_the_exact_mutation_acknowledgement(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    mainnet = replace(profile, environment="mainnet", identity=SUPPORTED_ENVIRONMENTS["mainnet"])
    adapter = FakeLifecycleAdapter()

    with pytest.raises(LifecycleError, match="profile did not pass mutation validation"):
        await run_order_lifecycle(mainnet, plan, adapter, run_id="mainnet-without-ack")
    assert not adapter.calls

    await run_order_lifecycle(
        mainnet,
        plan,
        adapter,
        run_id="mainnet-with-ack",
        mutation_acknowledgement=MAINNET_MUTATION_ACKNOWLEDGEMENT,
    )
    assert adapter.calls[0] == "place"


@pytest.mark.asyncio
async def test_success_evidence_is_credential_free_and_preserves_bounded_intent(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    result = await run_order_lifecycle(profile, plan, FakeLifecycleAdapter(), run_id="evidence-success")

    evidence = build_lifecycle_evidence(profile, plan, result=result)
    encoded = json.dumps(evidence, sort_keys=True)

    assert evidence["result"] == "pass"
    assert evidence["run_id"] == "evidence-success"
    assert evidence["order_plan"]["post_only"] is True
    assert evidence["order_plan"]["quantity"] == "1"
    assert evidence["owned_order_ids"] == ["123456789"]
    assert "private_key" not in encoded.lower()
    assert "signature" not in encoded.lower()
    assert "rpc_url" not in encoded.lower()


@pytest.mark.asyncio
async def test_failure_evidence_records_sanitized_stage_and_cleanup_status(
    profile: CanaryProfile,
    plan: OrderPlan,
) -> None:
    adapter = FakeLifecycleAdapter(
        fail_once="modify:123456789",
        fail_always="cancel:123456789",
    )
    with pytest.raises(LifecycleError) as raised:
        await run_order_lifecycle(profile, plan, adapter, run_id="evidence-failure")

    evidence = build_lifecycle_evidence(
        profile,
        plan,
        error=raised.value,
        run_id="evidence-failure",
    )

    assert evidence["result"] == "fail"
    assert evidence["failure"] == {
        "stage": "modify",
        "detail": "RuntimeError",
        "cleanup_failures": ["123456789[cancel:RuntimeError]"],
    }
    assert "synthetic failure" not in json.dumps(evidence)


def test_evidence_requires_exactly_one_outcome(profile: CanaryProfile, plan: OrderPlan) -> None:
    with pytest.raises(ValueError, match="exactly one"):
        build_lifecycle_evidence(profile, plan)
