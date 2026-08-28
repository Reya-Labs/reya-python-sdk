"""Offline tests for the operator-coordinated PRO-657 recovery boundary."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal

import pytest

from scripts.canary_preflight import SUPPORTED_ENVIRONMENTS, CanaryPolicy, CanaryProfile
from scripts.canary_recovery import (
    MAINNET_RECOVERY_ACKNOWLEDGEMENT,
    OperatorResume,
    ProjectionSnapshot,
    RecoveryError,
    RecoveryObservation,
    build_recovery_evidence,
    run_recovery_checkpoint,
)

pytestmark = pytest.mark.offline


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
            max_quantity=Decimal("1"),
            max_notional=Decimal("1000"),
            allowed_account_ids=(42,),
            allowed_wallet_addresses=("0x1111111111111111111111111111111111111111",),
        ),
    )


@pytest.fixture(name="baseline")
def fixture_baseline() -> ProjectionSnapshot:
    return ProjectionSnapshot(
        account_id=42,
        wallet_address="0x1111111111111111111111111111111111111111",
        market_symbol="BTCRUSDPERP",
        latest_sequence=100,
        open_order_ids=("888",),
        position_size=Decimal("2.5"),
    )


class FakeRecoveryAdapter:
    def __init__(self, baseline: ProjectionSnapshot, recovered: ProjectionSnapshot | None = None) -> None:
        self.baseline = baseline
        self.observation = RecoveryObservation(
            snapshot=recovered or replace(baseline, latest_sequence=102),
            observed_sequences=(101, 102),
        )
        self.calls: list[object] = []

    async def snapshot_projection(self, timeout_s: float) -> ProjectionSnapshot:
        self.calls.append(("snapshot", timeout_s))
        return self.baseline

    async def reconnect_and_collect(self, after_sequence: int, timeout_s: float) -> RecoveryObservation:
        self.calls.append(("reconnect", after_sequence, timeout_s))
        return self.observation


class FakeCheckpoint:
    def __init__(self, *, evidence_ref: str = "linear:PRO-261#restart-1", hang: bool = False) -> None:
        self.evidence_ref = evidence_ref
        self.hang = hang
        self.calls: list[object] = []

    async def wait_for_resume(self, checkpoint_id: str, timeout_s: float) -> OperatorResume:
        self.calls.append((checkpoint_id, timeout_s))
        if self.hang:
            await asyncio.Event().wait()
        return OperatorResume(self.evidence_ref)


@pytest.mark.asyncio
async def test_checkpoint_snapshots_then_waits_then_reconnects_and_proves_recovery(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
) -> None:
    adapter = FakeRecoveryAdapter(baseline)
    checkpoint = FakeCheckpoint()

    result = await run_recovery_checkpoint(
        profile,
        adapter,
        checkpoint,
        run_id="recovery-1",
        timeout_s=1,
    )

    assert result.observed_sequences == (101, 102)
    assert result.baseline.open_order_ids == result.recovered.open_order_ids
    assert result.baseline.position_size == result.recovered.position_size
    assert adapter.calls == [("snapshot", 1), ("reconnect", 100, 1)]
    assert checkpoint.calls == [("PRO-657:devnet1:recovery-1:restart-recovery", 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sequences", "latest", "message"),
    [
        ((), 100, "no post-reconnect"),
        ((101, 103), 103, "not contiguous and unique"),
        ((101, 101), 101, "not contiguous and unique"),
        ((101,), 102, "does not match"),
    ],
)
async def test_checkpoint_rejects_missing_gapped_duplicate_or_inconsistent_sequences(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
    sequences: tuple[int, ...],
    latest: int,
    message: str,
) -> None:
    adapter = FakeRecoveryAdapter(baseline)
    adapter.observation = RecoveryObservation(replace(baseline, latest_sequence=latest), sequences)

    with pytest.raises(RecoveryError, match=message):
        await run_recovery_checkpoint(profile, adapter, FakeCheckpoint(), run_id="bad-sequence", timeout_s=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transform", "message"),
    [
        (lambda snapshot: replace(snapshot, open_order_ids=("888", "999")), "open-order projection"),
        (lambda snapshot: replace(snapshot, position_size=Decimal("2.6")), "position delta"),
        (lambda snapshot: replace(snapshot, account_id=43), "account is not allowlisted"),
        (lambda snapshot: replace(snapshot, market_symbol="ETHRUSDPERP"), "market does not match"),
    ],
)
async def test_checkpoint_rejects_projection_or_cleanup_drift(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
    transform: Callable[[ProjectionSnapshot], ProjectionSnapshot],
    message: str,
) -> None:
    adapter = FakeRecoveryAdapter(baseline, replace(transform(baseline), latest_sequence=101))
    adapter.observation = replace(adapter.observation, observed_sequences=(101,))

    with pytest.raises(RecoveryError, match=message):
        await run_recovery_checkpoint(profile, adapter, FakeCheckpoint(), run_id="projection-drift", timeout_s=1)


@pytest.mark.asyncio
async def test_mainnet_checkpoint_requires_separate_ack_before_adapter_io(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
) -> None:
    mainnet = replace(profile, environment="mainnet", identity=SUPPORTED_ENVIRONMENTS["mainnet"])
    adapter = FakeRecoveryAdapter(baseline)
    checkpoint = FakeCheckpoint()

    with pytest.raises(RecoveryError, match="acknowledgement is required"):
        await run_recovery_checkpoint(mainnet, adapter, checkpoint, run_id="mainnet-recovery", timeout_s=1)

    assert not adapter.calls
    assert not checkpoint.calls
    await run_recovery_checkpoint(
        mainnet,
        adapter,
        checkpoint,
        run_id="mainnet-recovery",
        timeout_s=1,
        mainnet_acknowledgement=MAINNET_RECOVERY_ACKNOWLEDGEMENT,
    )


@pytest.mark.asyncio
async def test_invalid_run_id_fails_before_adapter_io(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
) -> None:
    adapter = FakeRecoveryAdapter(baseline)

    with pytest.raises(RecoveryError, match="run_id must be"):
        await run_recovery_checkpoint(profile, adapter, FakeCheckpoint(), run_id="unsafe run", timeout_s=1)

    assert not adapter.calls


@pytest.mark.asyncio
async def test_checkpoint_timeout_is_bounded_and_sanitized(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
) -> None:
    with pytest.raises(RecoveryError) as raised:
        await run_recovery_checkpoint(
            profile,
            FakeRecoveryAdapter(baseline),
            FakeCheckpoint(hang=True),
            run_id="checkpoint-timeout",
            timeout_s=0.001,
        )

    assert raised.value.stage == "checkpoint"
    assert raised.value.detail == "TimeoutError"


@pytest.mark.asyncio
async def test_operator_evidence_reference_must_be_bounded_and_credential_free(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
) -> None:
    with pytest.raises(RecoveryError, match="missing or unsafe"):
        await run_recovery_checkpoint(
            profile,
            FakeRecoveryAdapter(baseline),
            FakeCheckpoint(evidence_ref="https://user:secret@example.invalid/evidence?token=secret"),
            run_id="unsafe-ref",
            timeout_s=1,
        )


@pytest.mark.asyncio
async def test_recovery_evidence_records_only_validated_results(
    profile: CanaryProfile,
    baseline: ProjectionSnapshot,
) -> None:
    result = await run_recovery_checkpoint(
        profile,
        FakeRecoveryAdapter(baseline),
        FakeCheckpoint(),
        run_id="evidence-recovery",
        timeout_s=1,
    )

    evidence = build_recovery_evidence(profile, result=result)
    encoded = json.dumps(evidence, sort_keys=True)

    assert evidence["result"] == "pass"
    assert evidence["assertions"] == {
        "projection_converged": True,
        "sequence_contiguous": True,
        "no_duplicate_sequence": True,
        "no_resting_order_delta": True,
        "no_position_delta": True,
    }
    assert evidence["baseline"]["position_size"] == "2.5"
    assert "private" not in encoded.lower()
    assert "secret" not in encoded.lower()


def test_failure_evidence_is_sanitized(profile: CanaryProfile) -> None:
    error = RecoveryError("reconnect", "TimeoutError")

    evidence = build_recovery_evidence(profile, error=error, run_id="failed-recovery")

    assert evidence["failure"] == {"stage": "reconnect", "detail": "TimeoutError"}
    assert "checkpoint" not in evidence


def test_failure_evidence_rejects_unsafe_run_id(profile: CanaryProfile) -> None:
    with pytest.raises(ValueError, match="run_id must be"):
        build_recovery_evidence(profile, error=RecoveryError("plan", "invalid"), run_id="unsafe run")
