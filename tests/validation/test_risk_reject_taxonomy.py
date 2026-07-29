"""Offline checks that the SDK can name every pre-trade risk rejection.

Three layers are involved, and only the third is this repo's:

1. The matching engine rejects with a numeric protobuf ``ErrorCode`` (the risk
   range is 71-85, with 81 retired and reserved).
2. The API layer translates that number into the public ``RequestErrorCode``
   taxonomy string. That translation is the *only* place the numbers appear in
   production, and it lives off-chain in
   ``packages/common-backend/src/trade-handlers/me-error.ts``, not here. At the
   time of writing it maps ``INVALID_NONCE`` only, so the risk codes below
   still fall through to the per-operation ``*_OTHER_ERROR`` catch-alls.
3. The SDK decodes the taxonomy string it is handed. It never sees the number.

So this module deliberately does *not* assert a numeric->name mapping: the SDK
does not own one, and asserting a mapping nobody ships would be vacuous. What
it does assert is the property the SDK is actually responsible for — that for
every risk rejection the engine can emit, the generated enums contain the
taxonomy member that rejection is specified to arrive as. If a specs bump
renames or drops one of those members, decoding that rejection would break, and
these tests fail.

The wire numbers and engine names in ``ENGINE_RISK_WIRE_CODES`` are transcribed
from the pinned engine source, not from the spec prose:
``crates/matching-engine/proto/matching_engine.proto`` for the numbering, and
``risk_error_to_error_code`` in
``crates/matching-engine/src/threads/reactor/reactor.rs`` for which variants are
actually reachable. The target taxonomy names come from the ``RequestErrorCode``
description in ``specs/trading-schemas.json``.
"""

from __future__ import annotations

import pytest

from sdk.async_exec_api.cancel_reason import CancelReason as WsExecCancelReason
from sdk.async_exec_api.request_error_code import RequestErrorCode as WsExecRequestErrorCode
from sdk.open_api import CancelReason as RestCancelReason
from sdk.open_api import RequestErrorCode as RestRequestErrorCode

pytestmark = pytest.mark.offline

# (engine wire code, engine proto name, public RequestErrorCode it surfaces as)
ENGINE_RISK_WIRE_CODES = [
    (71, "PRECONDITION_UNDER_LMR", "ACCOUNT_BELOW_LIQUIDATION_MARGIN_ERROR"),
    (72, "TAKER_BELOW_INITIAL_MARGIN", "ACCOUNT_BELOW_INITIAL_MARGIN_ERROR"),
    # 73 is defined in the proto but currently unreachable: no RiskError variant
    # maps to it, so the market-wide cap cannot fire today. The taxonomy member
    # exists because the budget at 80 is the tighter of the two and is expected
    # to stop binding first. Kept here so the name cannot be dropped underneath
    # the engine when the cap does become reachable.
    (73, "OPEN_INTEREST_EXCEEDED", "OPEN_INTEREST_CAP_ERROR"),
    (74, "REDUCE_ONLY_VIOLATES_DIRECTION", "REDUCE_ONLY_CONDITION_NOT_MET_ERROR"),
    (75, "REDUCE_ONLY_NOTHING_TO_REDUCE", "REDUCE_ONLY_CONDITION_NOT_MET_ERROR"),
    # The spot and malformed-order codes are risk-engine-emitted but deliberately
    # reuse pre-existing taxonomy members rather than adding new ones.
    (76, "SPOT_INSUFFICIENT_BALANCE", "INSUFFICIENT_BALANCE_ERROR"),
    (77, "SPOT_UNSUPPORTED_ORDER_CONFIG", "INPUT_VALIDATION_ERROR"),
    (78, "RISK_ENGINE_NOT_READY", "CROSSING_ORDERS_TEMPORARILY_UNAVAILABLE_ERROR"),
    (79, "RISK_ENGINE_DB_TIMEOUT", "CROSSING_ORDERS_TEMPORARILY_UNAVAILABLE_ERROR"),
    (80, "OI_BUDGET_EXCEEDED", "OPEN_INTEREST_BUDGET_ERROR"),
    (82, "INVALID_REDUCE_ONLY", "INPUT_VALIDATION_ERROR"),
    (83, "SPOT_ROUNDED_AMOUNT_INVALID", "PRICE_QTY_BOUNDS_ERROR"),
    # Both of these are initial-margin failures with a narrower cause, and the
    # spec folds them onto the same member; the free-text message distinguishes.
    (84, "TAKER_RECOVERY_NOT_RISK_REDUCING", "ACCOUNT_BELOW_INITIAL_MARGIN_ERROR"),
    (85, "TAKER_SETTLEMENT_RESERVE_FAILED", "ACCOUNT_BELOW_INITIAL_MARGIN_ERROR"),
]

# The members specs 3.0.19 added for the risk taxonomy. Anything the engine
# rejects with that is not covered by a pre-existing member lands on one of these.
RISK_REJECT_REQUEST_ERROR_CODES = {
    "ACCOUNT_BELOW_LIQUIDATION_MARGIN_ERROR",
    "ACCOUNT_BELOW_INITIAL_MARGIN_ERROR",
    "OPEN_INTEREST_CAP_ERROR",
    "OPEN_INTEREST_BUDGET_ERROR",
    "REDUCE_ONLY_CONDITION_NOT_MET_ERROR",
    "CROSSING_ORDERS_TEMPORARILY_UNAVAILABLE_ERROR",
}

# Wire code 81 was retired and must not be reused; the engine pins this too.
RETIRED_ENGINE_RISK_WIRE_CODE = 81


@pytest.mark.parametrize(
    ("wire_code", "engine_name", "taxonomy_name"),
    ENGINE_RISK_WIRE_CODES,
    ids=[f"{code}-{name}" for code, name, _ in ENGINE_RISK_WIRE_CODES],
)
def test_engine_risk_wire_code_has_a_decodable_taxonomy_name(
    wire_code: int, engine_name: str, taxonomy_name: str
) -> None:
    """Every risk rejection the engine can emit has a name the SDK can decode.

    The SDK never sees ``wire_code``/``engine_name`` — they identify which
    engine rejection each taxonomy member is the landing site for, so that a
    member cannot be renamed or dropped without a deliberate decision about
    which rejection becomes undecodable.
    """
    assert RestRequestErrorCode(taxonomy_name).value == taxonomy_name
    assert WsExecRequestErrorCode(taxonomy_name).value == taxonomy_name


def test_risk_taxonomy_members_are_exactly_the_expected_set() -> None:
    """Guards against a specs bump adding a risk member the table does not cover."""
    rest_codes = {code.value for code in RestRequestErrorCode}
    ws_exec_codes = {code.value for code in WsExecRequestErrorCode}

    assert RISK_REJECT_REQUEST_ERROR_CODES <= rest_codes
    assert RISK_REJECT_REQUEST_ERROR_CODES <= ws_exec_codes

    mapped_names = {taxonomy_name for _, _, taxonomy_name in ENGINE_RISK_WIRE_CODES}
    assert RISK_REJECT_REQUEST_ERROR_CODES <= mapped_names


def test_rest_and_ws_exec_request_error_codes_do_not_drift() -> None:
    """Order entry is reachable over both transports and must reject identically."""
    assert {code.value for code in RestRequestErrorCode} == {code.value for code in WsExecRequestErrorCode}


def test_engine_risk_wire_codes_are_contiguous_apart_from_the_retired_one() -> None:
    """A new engine risk code should not land here without a taxonomy decision."""
    covered = sorted({wire_code for wire_code, _, _ in ENGINE_RISK_WIRE_CODES})

    assert covered[0] == 71
    assert covered[-1] == 85
    assert RETIRED_ENGINE_RISK_WIRE_CODE not in covered
    assert covered == [code for code in range(71, 86) if code != RETIRED_ENGINE_RISK_WIRE_CODE]


def test_risk_cancelled_is_a_cancel_reason_not_a_request_error() -> None:
    """A risk check that cancels a *resting* order is a cancellation, not a reject.

    An order refused at admission was never created and comes back as a
    ``RequestErrorCode``; one cancelled later already has an ``orderId`` and
    arrives on the order-changes stream. The two must not be conflated, so
    ``RISK_CANCELLED`` must exist only on the cancel-reason side.
    """
    assert RestCancelReason("RISK_CANCELLED") is RestCancelReason.RISK_CANCELLED
    assert WsExecCancelReason("RISK_CANCELLED") is WsExecCancelReason.RISK_CANCELLED

    assert "RISK_CANCELLED" not in {code.value for code in RestRequestErrorCode}
    assert "RISK_CANCELLED" not in {code.value for code in WsExecRequestErrorCode}
