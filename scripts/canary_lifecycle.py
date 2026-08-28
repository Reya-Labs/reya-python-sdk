"""Bounded, run-owned order lifecycle for the Perp OB migration canary."""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar

import asyncio
import hashlib
import math
import re
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal

from scripts.canary_preflight import CanaryProfile, PreflightError, validate_profile

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ResultT = TypeVar("_ResultT")


@dataclass(frozen=True)
class OrderPlan:
    """One deliberately resting order and its complete modified state."""

    account_id: int
    wallet_address: str
    market_symbol: str
    market_id: int
    is_buy: bool
    quantity: Decimal
    initial_limit_px: Decimal
    modified_limit_px: Decimal


@dataclass(frozen=True)
class OrderExpectation:
    """State that both REST and read WebSocket surfaces must prove."""

    order_id: str
    status: Literal["OPEN", "CANCELLED"]
    limit_px: Decimal | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class LifecycleEvent:
    """Credential-free evidence for one completed lifecycle step."""

    step: str
    order_id: str
    detail: str


@dataclass(frozen=True)
class LifecycleResult:
    """Successful run result suitable for inclusion in canary evidence."""

    run_id: str
    client_order_id: int
    owned_order_ids: tuple[str, ...]
    events: tuple[LifecycleEvent, ...]


def build_lifecycle_evidence(
    profile: CanaryProfile,
    plan: OrderPlan,
    *,
    result: LifecycleResult | None = None,
    error: LifecycleError | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build a credential-free success or failure record for one lifecycle run."""
    if (result is None) == (error is None):
        raise ValueError("provide exactly one of result or error")
    resolved_run_id: str | None
    client_order_id: int | None
    if result is not None:
        resolved_run_id = result.run_id
        events = result.events
        order_ids = result.owned_order_ids
        client_order_id = result.client_order_id
    else:
        assert error is not None
        resolved_run_id = run_id
        events = error.events
        order_ids = tuple(dict.fromkeys(event.order_id for event in events))
        client_order_id = derive_client_order_id(resolved_run_id) if resolved_run_id is not None else None
    if resolved_run_id is None:
        raise ValueError("run_id is required for failure evidence")
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "result": "pass" if result is not None else "fail",
        "mode": "order-lifecycle",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": profile.environment,
        "release_manifest_id": profile.release_manifest_id,
        "run_id": resolved_run_id,
        "client_order_id": client_order_id,
        "order_plan": {
            "account_id": plan.account_id,
            "wallet_address": plan.wallet_address,
            "market_symbol": plan.market_symbol,
            "market_id": plan.market_id,
            "is_buy": plan.is_buy,
            "quantity": str(plan.quantity),
            "initial_limit_px": str(plan.initial_limit_px),
            "modified_limit_px": str(plan.modified_limit_px),
            "time_in_force": "GTC",
            "post_only": True,
            "reduce_only": False,
        },
        "owned_order_ids": list(order_ids),
        "events": [asdict(event) for event in events],
    }
    if error is not None:
        evidence["failure"] = {
            "stage": error.stage,
            "detail": error.detail,
            "cleanup_failures": list(error.cleanup_failures),
        }
    return evidence


class LifecycleError(RuntimeError):
    """Fail-closed lifecycle failure with bounded diagnostic evidence."""

    def __init__(
        self,
        stage: str,
        detail: str,
        *,
        events: tuple[LifecycleEvent, ...] = (),
        cleanup_failures: tuple[str, ...] = (),
    ) -> None:
        super().__init__(f"canary lifecycle failed at {stage}: {detail}")
        self.stage = stage
        self.detail = detail
        self.events = events
        self.cleanup_failures = cleanup_failures


class OpenOrderUnverifiedError(LifecycleError):
    """An OPEN response returned an ID but failed another response invariant."""

    def __init__(self, order_id: str, detail: str) -> None:
        super().__init__("place", detail)
        self.order_id = order_id


class LifecycleAdapter(Protocol):
    """Narrow boundary that a future SDK-backed live adapter must implement."""

    async def place_post_only_gtc(self, plan: OrderPlan, client_order_id: int) -> str:
        """Place the plan as post-only GTC and return its canonical order ID."""
        raise NotImplementedError

    async def modify_post_only_gtc(self, order_id: str, plan: OrderPlan, client_order_id: int) -> None:
        """Apply the complete modified state without changing immutable fields."""
        raise NotImplementedError

    async def cancel_order(self, order_id: str, plan: OrderPlan) -> None:
        """Cancel exactly one canonical order ID."""
        raise NotImplementedError

    async def wait_rest(self, expectation: OrderExpectation, plan: OrderPlan, timeout_s: float) -> None:
        """Wait for REST open-order presence or terminal absence."""
        raise NotImplementedError

    async def wait_ws(self, expectation: OrderExpectation, plan: OrderPlan, timeout_s: float) -> None:
        """Wait for the wallet order-change stream to prove the state."""
        raise NotImplementedError


class OwnedOrderRegistry:
    """Tracks only canonical order IDs created by the current canary run."""

    def __init__(self) -> None:
        self._created: list[str] = []
        self._active: set[str] = set()

    @property
    def created(self) -> tuple[str, ...]:
        return tuple(self._created)

    @property
    def active(self) -> tuple[str, ...]:
        return tuple(order_id for order_id in self._created if order_id in self._active)

    def add(self, order_id: str) -> None:
        if order_id in self._active:
            raise LifecycleError("place", "adapter returned a duplicate order ID")
        self._created.append(order_id)
        self._active.add(order_id)

    def mark_closed(self, order_id: str) -> None:
        self._active.discard(order_id)


def derive_client_order_id(run_id: str) -> int:
    """Derive a stable non-zero signed-64-bit client order ID from a run ID."""
    if not _RUN_ID_PATTERN.fullmatch(run_id):
        raise LifecycleError("plan", "run_id must be 1-64 safe filename characters")
    digest = hashlib.sha256(f"PRO-657:{run_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1) or 1


def validate_order_plan(
    profile: CanaryProfile,
    plan: OrderPlan,
    *,
    mutation_acknowledgement: str | None = None,
) -> None:
    """Validate the exact mutation against the fail-closed profile policy."""
    try:
        validate_profile(
            profile,
            mutating=True,
            mutation_acknowledgement=mutation_acknowledgement,
        )
    except PreflightError as error:
        raise LifecycleError("preflight", "profile did not pass mutation validation") from error

    errors: list[str] = []
    policy = profile.policy
    if plan.account_id not in policy.allowed_account_ids:
        errors.append("account ID is not allowlisted")
    if plan.wallet_address.lower() not in {address.lower() for address in policy.allowed_wallet_addresses}:
        errors.append("wallet address is not allowlisted")
    if plan.market_symbol != policy.market_symbol:
        errors.append("market symbol does not match the designated market")
    if plan.market_id != policy.market_id:
        errors.append("market ID does not match the designated market")
    if not plan.quantity.is_finite():
        errors.append("quantity must be finite")
    elif plan.quantity <= 0:
        errors.append("quantity must be greater than zero")
    elif plan.quantity > policy.max_quantity:
        errors.append("quantity exceeds the profile maximum")
    prices_are_finite = plan.initial_limit_px.is_finite() and plan.modified_limit_px.is_finite()
    if not prices_are_finite:
        errors.append("limit prices must be finite")
    elif plan.initial_limit_px <= 0 or plan.modified_limit_px <= 0:
        errors.append("limit prices must be greater than zero")
    elif plan.initial_limit_px == plan.modified_limit_px:
        errors.append("modified limit price must differ from the initial price")
    if plan.quantity.is_finite() and plan.quantity > 0 and prices_are_finite:
        for label, price in (("initial", plan.initial_limit_px), ("modified", plan.modified_limit_px)):
            if price > 0 and plan.quantity * price > policy.max_notional:
                errors.append(f"{label} order notional exceeds the profile maximum")
    if errors:
        raise LifecycleError("plan", "; ".join(errors))


async def _invoke(stage: str, operation: Awaitable[_ResultT], timeout_s: float) -> _ResultT:
    try:
        return await asyncio.wait_for(operation, timeout=timeout_s)
    except LifecycleError:
        raise
    except Exception as error:
        raise LifecycleError(stage, type(error).__name__) from error


async def _confirm(
    adapter: LifecycleAdapter,
    plan: OrderPlan,
    expectation: OrderExpectation,
    timeout_s: float,
    events: list[LifecycleEvent],
) -> None:
    await _invoke("rest-visibility", adapter.wait_rest(expectation, plan, timeout_s), timeout_s)
    events.append(LifecycleEvent(f"rest.{expectation.status.lower()}", expectation.order_id, "confirmed"))
    await _invoke("ws-visibility", adapter.wait_ws(expectation, plan, timeout_s), timeout_s)
    events.append(LifecycleEvent(f"ws.{expectation.status.lower()}", expectation.order_id, "confirmed"))


async def cleanup_owned_orders(
    adapter: LifecycleAdapter,
    plan: OrderPlan,
    registry: OwnedOrderRegistry,
    *,
    timeout_s: float,
) -> tuple[str, ...]:
    """Best-effort cleanup of this run's active order IDs, never account-wide."""
    failures: list[str] = []
    for order_id in registry.active:
        expectation = OrderExpectation(order_id, "CANCELLED")
        steps = (
            ("cancel", adapter.cancel_order(order_id, plan)),
            ("rest", adapter.wait_rest(expectation, plan, timeout_s)),
            ("ws", adapter.wait_ws(expectation, plan, timeout_s)),
        )
        order_failures: list[str] = []
        for label, operation in steps:
            try:
                await asyncio.wait_for(operation, timeout=timeout_s)
            except Exception as error:  # pylint: disable=broad-exception-caught
                order_failures.append(f"{label}:{type(error).__name__}")
        if order_failures:
            failures.append(f"{order_id}[{','.join(order_failures)}]")
        else:
            registry.mark_closed(order_id)
    return tuple(failures)


async def run_order_lifecycle(
    profile: CanaryProfile,
    plan: OrderPlan,
    adapter: LifecycleAdapter,
    *,
    run_id: str,
    timeout_s: float = 10.0,
    mutation_acknowledgement: str | None = None,
) -> LifecycleResult:
    """Run place, dual visibility, modify, dual visibility, and exact cancel."""
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise LifecycleError("plan", "timeout must be finite and greater than zero")
    validate_order_plan(profile, plan, mutation_acknowledgement=mutation_acknowledgement)
    client_order_id = derive_client_order_id(run_id)
    registry = OwnedOrderRegistry()
    events: list[LifecycleEvent] = []

    try:
        try:
            raw_order_id = await _invoke(
                "place",
                adapter.place_post_only_gtc(plan, client_order_id),
                timeout_s,
            )
        except OpenOrderUnverifiedError as error:
            if error.order_id.isdecimal() and int(error.order_id) > 0:
                registry.add(error.order_id)
                events.append(LifecycleEvent("place.unverified", error.order_id, "OPEN response invariant failed"))
            raise
        order_id = str(raw_order_id)
        if not order_id.isdecimal() or int(order_id) <= 0:
            raise LifecycleError("place", "adapter returned an invalid canonical order ID")
        registry.add(order_id)
        events.append(LifecycleEvent("place.accepted", order_id, "post-only GTC"))

        initial = OrderExpectation(order_id, "OPEN", plan.initial_limit_px, plan.quantity)
        await _confirm(adapter, plan, initial, timeout_s, events)

        await _invoke(
            "modify",
            adapter.modify_post_only_gtc(order_id, plan, client_order_id),
            timeout_s,
        )
        events.append(LifecycleEvent("modify.accepted", order_id, "complete post-only GTC state"))
        modified = OrderExpectation(order_id, "OPEN", plan.modified_limit_px, plan.quantity)
        await _confirm(adapter, plan, modified, timeout_s, events)

        await _invoke("cancel", adapter.cancel_order(order_id, plan), timeout_s)
        events.append(LifecycleEvent("cancel.accepted", order_id, "exact order ID"))
        cancelled = OrderExpectation(order_id, "CANCELLED")
        await _confirm(adapter, plan, cancelled, timeout_s, events)
        registry.mark_closed(order_id)
    except LifecycleError as error:
        cleanup_failures = await cleanup_owned_orders(adapter, plan, registry, timeout_s=timeout_s)
        raise LifecycleError(
            error.stage,
            error.detail,
            events=tuple(events),
            cleanup_failures=cleanup_failures,
        ) from error

    return LifecycleResult(run_id, client_order_id, registry.created, tuple(events))
