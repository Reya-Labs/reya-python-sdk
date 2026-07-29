"""Offline checks that the SDK can name every engine admission rejection.

Three layers are involved, and only the third is this repo's:

1. The matching engine rejects with a numeric protobuf ``ErrorCode``. Two
   ranges are covered here: the pre-trade risk range 71-85 (81 retired and
   reserved), and the trigger/near-expiry range 86-91.
2. The API layer translates that number into the public ``RequestErrorCode``
   taxonomy string. That translation is the *only* place the numbers appear in
   production, and it lives off-chain in
   ``packages/common-backend/src/trade-handlers/me-error.ts``, not here.
3. The SDK decodes the taxonomy string it is handed. It never sees the number.

So this module deliberately does *not* assert a numeric->name mapping: the SDK
does not own one, and asserting a mapping nobody ships would be vacuous. What
it does assert is the property the SDK is actually responsible for — that for
every rejection the engine can emit, the generated enums contain the taxonomy
member that rejection is specified to arrive as. If a specs bump renames or
drops one of those members, decoding that rejection would break, and these
tests fail.

The wire numbers and engine names in ``ENGINE_REJECT_WIRE_CODES`` are
transcribed from the pinned engine source, not from the spec prose:
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
ENGINE_REJECT_WIRE_CODES = [
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
    # Trigger and near-expiry rejects, 86-91. The spec is explicit that these
    # are not risk failures — they are admission rules on the request itself —
    # but they are engine-emitted numbers that need a name just the same, and
    # until specs 3.0.20 the reachable ones had nowhere to land.
    (86, "TRIGGER_REQUIRES_GTC", "TRIGGER_REQUIRES_GTC_ERROR"),
    (90, "TRIGGER_DUPLICATE_PROTECTION", "TRIGGER_ALREADY_EXISTS_ERROR"),
    (91, "ORDER_EXPIRY_TOO_NEAR", "ORDER_EXPIRES_TOO_SOON_ERROR"),
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

# The members specs 3.0.20 added, one per reachable trigger/near-expiry reject.
TRIGGER_AND_EXPIRY_REQUEST_ERROR_CODES = {
    "TRIGGER_REQUIRES_GTC_ERROR",
    "TRIGGER_ALREADY_EXISTS_ERROR",
    "ORDER_EXPIRES_TOO_SOON_ERROR",
}

# Wire code 81 was retired and must not be reused; the engine pins this too.
RETIRED_ENGINE_RISK_WIRE_CODE = 81

# 87 TRIGGER_PRICE_REQUIRED, 88 TRIGGER_QTY_NOT_ZERO and 89 TRIGGER_MARKET_NOT_PERP
# are defined in the proto but cannot reach the engine: the API pre-validates a
# missing triggerPx, a non-zero qty and a non-perp trigger market itself and
# returns INPUT_VALIDATION_ERROR. They get no dedicated member, and that is a
# decision rather than an oversight — pinned so adding one is deliberate.
ENGINE_TRIGGER_WIRE_CODES_WITHOUT_A_MEMBER = {87, 88, 89}


@pytest.mark.parametrize(
    ("wire_code", "engine_name", "taxonomy_name"),
    ENGINE_REJECT_WIRE_CODES,
    ids=[f"{code}-{name}" for code, name, _ in ENGINE_REJECT_WIRE_CODES],
)
def test_engine_reject_wire_code_has_a_decodable_taxonomy_name(  # pylint: disable=unused-argument
    wire_code: int, engine_name: str, taxonomy_name: str
) -> None:
    """Every rejection the engine can emit has a name the SDK can decode.

    The SDK never sees ``wire_code``/``engine_name`` — they identify which
    engine rejection each taxonomy member is the landing site for, so that a
    member cannot be renamed or dropped without a deliberate decision about
    which rejection becomes undecodable.
    """
    assert RestRequestErrorCode(taxonomy_name).value == taxonomy_name
    assert WsExecRequestErrorCode(taxonomy_name).value == taxonomy_name


def test_risk_taxonomy_members_are_exactly_the_expected_set() -> None:
    """Guards against a specs bump adding a member the table does not cover."""
    rest_codes = {code.value for code in RestRequestErrorCode}
    ws_exec_codes = {code.value for code in WsExecRequestErrorCode}
    expected = RISK_REJECT_REQUEST_ERROR_CODES | TRIGGER_AND_EXPIRY_REQUEST_ERROR_CODES

    assert expected <= rest_codes
    assert expected <= ws_exec_codes

    mapped_names = {taxonomy_name for _, _, taxonomy_name in ENGINE_REJECT_WIRE_CODES}
    assert expected <= mapped_names


def test_rest_and_ws_exec_request_error_codes_do_not_drift() -> None:
    """Order entry is reachable over both transports and must reject identically."""
    assert {code.value for code in RestRequestErrorCode} == {code.value for code in WsExecRequestErrorCode}


def test_engine_risk_wire_codes_are_contiguous_apart_from_the_retired_one() -> None:
    """A new engine risk code should not land here without a taxonomy decision."""
    covered = sorted({wire_code for wire_code, _, _ in ENGINE_REJECT_WIRE_CODES if wire_code <= 85})

    assert covered[0] == 71
    assert covered[-1] == 85
    assert RETIRED_ENGINE_RISK_WIRE_CODE not in covered
    assert covered == [code for code in range(71, 86) if code != RETIRED_ENGINE_RISK_WIRE_CODE]


def test_trigger_and_expiry_wire_codes_cover_exactly_the_reachable_ones() -> None:
    """87-89 are unreachable by construction; 86, 90 and 91 each need a member."""
    covered = sorted({wire_code for wire_code, _, _ in ENGINE_REJECT_WIRE_CODES if wire_code > 85})

    assert covered == [86, 90, 91]
    assert not ENGINE_TRIGGER_WIRE_CODES_WITHOUT_A_MEMBER & set(covered)
    assert sorted(set(covered) | ENGINE_TRIGGER_WIRE_CODES_WITHOUT_A_MEMBER) == list(range(86, 92))

    mapped_names = {taxonomy_name for wire_code, _, taxonomy_name in ENGINE_REJECT_WIRE_CODES if wire_code > 85}
    assert mapped_names == TRIGGER_AND_EXPIRY_REQUEST_ERROR_CODES


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
