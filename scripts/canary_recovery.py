"""Operator-coordinated restart recovery checks for the PRO-657 canary."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

import asyncio
import math
import re
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from scripts.canary_lifecycle import LifecycleError, derive_client_order_id
from scripts.canary_preflight import CanaryProfile, PreflightError, validate_profile

MAINNET_RECOVERY_ACKNOWLEDGEMENT = "PRO-657-MAINNET-RECOVERY-CHECKPOINT-APPROVED"

_EVIDENCE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}$")
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class ProjectionSnapshot:
    """Public read-model state captured at one recovery boundary."""

    account_id: int
    wallet_address: str
    market_symbol: str
    latest_sequence: int
    open_order_ids: tuple[str, ...]
    position_size: Decimal


@dataclass(frozen=True)
class RecoveryObservation:
    """Fresh post-reconnect projection plus every event sequence observed."""

    snapshot: ProjectionSnapshot
    observed_sequences: tuple[int, ...]


@dataclass(frozen=True)
class OperatorResume:
    """Credential-free acknowledgement supplied after the operator action."""

    evidence_ref: str


@dataclass(frozen=True)
class RecoveryResult:
    """Validated recovery result suitable for the migration evidence bundle."""

    run_id: str
    client_order_id: int
    checkpoint_id: str
    operator_evidence_ref: str
    baseline: ProjectionSnapshot
    recovered: ProjectionSnapshot
    observed_sequences: tuple[int, ...]


class RecoveryError(RuntimeError):
    """Fail-closed recovery failure with a stable stage and safe detail."""

    def __init__(self, stage: str, detail: str) -> None:
        super().__init__(f"canary recovery failed at {stage}: {detail}")
        self.stage = stage
        self.detail = detail


class RecoveryAdapter(Protocol):
    """Read-only recovery boundary; it deliberately has no restart method."""

    async def snapshot_projection(self, timeout_s: float) -> ProjectionSnapshot:
        """Capture the current REST/WS projection before an operator action."""
        raise NotImplementedError

    async def reconnect_and_collect(self, after_sequence: int, timeout_s: float) -> RecoveryObservation:
        """Reconnect, resubscribe, and collect a contiguous fresh event window."""
        raise NotImplementedError


class OperatorCheckpoint(Protocol):
    """Pause boundary implemented by external orchestration, never the SDK."""

    async def wait_for_resume(self, checkpoint_id: str, timeout_s: float) -> OperatorResume:
        """Wait for an operator to complete the planned action and resume."""
        raise NotImplementedError


async def _invoke(stage: str, operation: Awaitable[_ResultT], timeout_s: float) -> _ResultT:
    try:
        return await asyncio.wait_for(operation, timeout=timeout_s)
    except RecoveryError:
        raise
    except Exception as error:
        raise RecoveryError(stage, type(error).__name__) from error


def _validate_snapshot(profile: CanaryProfile, snapshot: ProjectionSnapshot, *, stage: str) -> None:
    errors: list[str] = []
    policy = profile.policy
    if snapshot.account_id not in policy.allowed_account_ids:
        errors.append("account is not allowlisted")
    if snapshot.wallet_address.lower() not in {address.lower() for address in policy.allowed_wallet_addresses}:
        errors.append("wallet is not allowlisted")
    if snapshot.market_symbol != policy.market_symbol:
        errors.append("market does not match the profile")
    if snapshot.latest_sequence < 0:
        errors.append("latest sequence cannot be negative")
    if not snapshot.position_size.is_finite():
        errors.append("position size must be finite")
    if len(snapshot.open_order_ids) != len(set(snapshot.open_order_ids)):
        errors.append("open order IDs contain duplicates")
    if any(not order_id.isdecimal() or int(order_id) <= 0 for order_id in snapshot.open_order_ids):
        errors.append("open order IDs must be positive canonical IDs")
    if errors:
        raise RecoveryError(stage, "; ".join(errors))


def _validate_recovery(baseline: ProjectionSnapshot, observation: RecoveryObservation) -> None:
    recovered = observation.snapshot
    if recovered.account_id != baseline.account_id:
        raise RecoveryError("projection", "account changed across the checkpoint")
    if recovered.wallet_address.lower() != baseline.wallet_address.lower():
        raise RecoveryError("projection", "wallet changed across the checkpoint")
    if recovered.market_symbol != baseline.market_symbol:
        raise RecoveryError("projection", "market changed across the checkpoint")
    if set(recovered.open_order_ids) != set(baseline.open_order_ids):
        raise RecoveryError("cleanup", "open-order projection did not return to the baseline")
    if recovered.position_size != baseline.position_size:
        raise RecoveryError("cleanup", "position delta remained after recovery")

    sequences = observation.observed_sequences
    if not sequences:
        raise RecoveryError("sequence", "no post-reconnect canary event was observed")
    if recovered.latest_sequence != sequences[-1]:
        raise RecoveryError("sequence", "projection sequence does not match the observed event window")
    expected = tuple(range(baseline.latest_sequence + 1, recovered.latest_sequence + 1))
    if sequences != expected:
        raise RecoveryError("sequence", "post-reconnect event sequences are not contiguous and unique")


def _validate_operator_resume(resume: OperatorResume) -> None:
    if not _EVIDENCE_REF_PATTERN.fullmatch(resume.evidence_ref):
        raise RecoveryError("checkpoint", "operator evidence reference is missing or unsafe")


async def run_recovery_checkpoint(
    profile: CanaryProfile,
    adapter: RecoveryAdapter,
    checkpoint: OperatorCheckpoint,
    *,
    run_id: str,
    timeout_s: float = 60.0,
    mainnet_acknowledgement: str | None = None,
) -> RecoveryResult:
    """Snapshot, pause for an operator action, reconnect, and prove recovery."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise RecoveryError("plan", "timeout must be finite and greater than zero")
    try:
        validate_profile(profile, mutating=False)
    except PreflightError as error:
        raise RecoveryError("preflight", "profile did not pass validation") from error
    if profile.environment == "mainnet" and mainnet_acknowledgement != MAINNET_RECOVERY_ACKNOWLEDGEMENT:
        raise RecoveryError("preflight", "mainnet recovery checkpoint acknowledgement is required")

    try:
        client_order_id = derive_client_order_id(run_id)
    except LifecycleError as error:
        raise RecoveryError("plan", "run_id must be 1-64 safe filename characters") from error
    checkpoint_id = f"PRO-657:{profile.environment}:{run_id}:restart-recovery"
    baseline = await _invoke("baseline", adapter.snapshot_projection(timeout_s), timeout_s)
    _validate_snapshot(profile, baseline, stage="baseline")
    resume = await _invoke("checkpoint", checkpoint.wait_for_resume(checkpoint_id, timeout_s), timeout_s)
    _validate_operator_resume(resume)
    observation = await _invoke(
        "reconnect",
        adapter.reconnect_and_collect(baseline.latest_sequence, timeout_s),
        timeout_s,
    )
    _validate_snapshot(profile, observation.snapshot, stage="projection")
    _validate_recovery(baseline, observation)
    return RecoveryResult(
        run_id=run_id,
        client_order_id=client_order_id,
        checkpoint_id=checkpoint_id,
        operator_evidence_ref=resume.evidence_ref,
        baseline=baseline,
        recovered=observation.snapshot,
        observed_sequences=observation.observed_sequences,
    )


def build_recovery_evidence(
    profile: CanaryProfile,
    *,
    result: RecoveryResult | None = None,
    error: RecoveryError | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build credential-free recovery evidence without copying operator output."""
    if (result is None) == (error is None):
        raise ValueError("provide exactly one of result or error")
    resolved_run_id = result.run_id if result is not None else run_id
    if resolved_run_id is None:
        raise ValueError("run_id is required for failure evidence")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "result": "pass" if result is not None else "fail",
        "mode": "restart-recovery",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": profile.environment,
        "release_manifest_id": profile.release_manifest_id,
        "run_id": resolved_run_id,
        "client_order_id": _evidence_client_order_id(resolved_run_id),
    }
    if result is not None:
        evidence["checkpoint"] = {
            "id": result.checkpoint_id,
            "operator_evidence_ref": result.operator_evidence_ref,
        }
        evidence["baseline"] = _snapshot_evidence(result.baseline)
        evidence["recovered"] = _snapshot_evidence(result.recovered)
        evidence["observed_sequences"] = list(result.observed_sequences)
        evidence["assertions"] = {
            "projection_converged": True,
            "sequence_contiguous": True,
            "no_duplicate_sequence": True,
            "no_resting_order_delta": True,
            "no_position_delta": True,
        }
    else:
        assert error is not None
        evidence["failure"] = {"stage": error.stage, "detail": error.detail}
    return evidence


def _snapshot_evidence(snapshot: ProjectionSnapshot) -> dict[str, Any]:
    return {
        "account_id": snapshot.account_id,
        "wallet_address": snapshot.wallet_address,
        "market_symbol": snapshot.market_symbol,
        "latest_sequence": snapshot.latest_sequence,
        "open_order_ids": list(snapshot.open_order_ids),
        "position_size": str(snapshot.position_size),
    }


def _evidence_client_order_id(run_id: str) -> int:
    try:
        return derive_client_order_id(run_id)
    except LifecycleError as error:
        raise ValueError("run_id must be 1-64 safe filename characters") from error
