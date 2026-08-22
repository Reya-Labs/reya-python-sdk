# pylint: disable=protected-access,redefined-outer-name
"""Client-side entry guards for cancelAllAfter and modifyOrder.

Offline (no devnet): builds payloads with a fixed key + a hand-seeded
symbol→marketId map (same seam as tests/parity/test_wire_serialization.py)
and asserts on the client-layer validation rules:

- ``timeout_ms`` bounds: 0 (disarm) or [5000, 60000]; out-of-range raises
  BEFORE a nonce is consumed (a rejected arm must not burn the per-wallet
  nonce counter).
- modify targeting: at least one of ``order_id`` / ``client_order_id``;
  when both are present, ``order_id`` targets and ``client_order_id`` restates
  the resting order's non-zero client id.
- ``client_order_id`` resolution: the SIGNED ``OrderDetails.clientOrderId`` is
  the resting order's id, or 0 under ``order_id`` targeting with no client id
  (verified by re-signing with the expected values).
- post-only gate-lift: postOnly=True + GTC flows AND is covered by the
  signature (the entry rejections — postOnly+IOC, GTT — are pinned in
  tests/parity/test_wire_serialization.py).
- trigger admission: the TIF↔``expiresAfter`` coupling on creates, the
  restate-immutability of both on modifies, and the three states of the
  per-market ``triggerLimitBandFraction``.
"""

from __future__ import annotations

from typing import Any

from decimal import Decimal

import pytest

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, TimeInForceInt
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters

pytestmark = pytest.mark.offline

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CHAIN_ID = 89346162
PERP_SYMBOL = "ETHRUSDPERP"
# The three states triggerLimitBandFraction can be in. Presence and value carry
# different rules, and absence is NOT a fraction of "0".
BANDED_SYMBOL = PERP_SYMBOL  # publishes "0.05"
UNBANDED_SYMBOL = "BTCRUSDPERP"  # publishes "0" — band disabled, any positive limit
NO_BAND_SYMBOL = "SOLRUSDPERP"  # publishes nothing — the venue arms no trigger at all

PINNED_NONCE = 1700000000000005
PINNED_DEADLINE = 1745000300


@pytest.fixture
def client() -> ReyaTradingClient:
    """A ReyaTradingClient that can build payloads offline.

    Seeds the symbol→marketId map directly instead of calling ``start()``
    (which loads market definitions over the network) and pins
    ``dex_id_override=2`` so re-signed expectations are env-independent.
    """
    config = TradingConfig(
        api_url="https://invalid.example",  # never called — building is pure
        chain_id=CHAIN_ID,
        owner_wallet_address=SIGNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=12345,
        dex_id_override=2,
    )
    c = ReyaTradingClient(config)
    c._symbol_to_market_id = {PERP_SYMBOL: 1, UNBANDED_SYMBOL: 2, NO_BAND_SYMBOL: 3}
    c._symbol_to_tick_size = {PERP_SYMBOL: "0.001", UNBANDED_SYMBOL: "0.001", NO_BAND_SYMBOL: "0.001"}
    # NO_BAND_SYMBOL is deliberately ABSENT here: start() only records a market
    # that publishes a fraction, so absence is what the client actually sees.
    c._symbol_to_trigger_band = {BANDED_SYMBOL: "0.05", UNBANDED_SYMBOL: "0"}
    c._initialized = True
    return c


def _last_nonce(client: ReyaTradingClient) -> int:
    """The wallet's current class-level nonce watermark (0 if untouched)."""
    return ReyaTradingClient._wallet_nonces.get(client.owner_wallet_address.lower(), 0)


# ============================================================================
# cancelAllAfter timeout_ms bounds
# ============================================================================


@pytest.mark.cod
@pytest.mark.parametrize("timeout_ms", [1, 4999, 60001])
def test_cancel_all_after_out_of_range_timeout_rejected_before_nonce(
    client: ReyaTradingClient, timeout_ms: int
) -> None:
    """timeout_ms outside {0} ∪ [5000, 60000] raises BEFORE a nonce is
    consumed — a rejected arm must not advance the per-wallet counter."""
    nonce_before = _last_nonce(client)
    with pytest.raises(ValueError, match="timeout_ms must be"):
        client.build_cancel_all_after_payload(timeout_ms=timeout_ms)
    assert _last_nonce(client) == nonce_before, "rejected cancelAllAfter consumed a nonce"


@pytest.mark.cod
@pytest.mark.parametrize("timeout_ms", [0, 5000, 60000])
def test_cancel_all_after_in_range_timeout_builds(client: ReyaTradingClient, timeout_ms: int) -> None:
    """0 (disarm) and the [5000, 60000] bounds inclusive all build a signed payload."""
    payload = client.build_cancel_all_after_payload(timeout_ms=timeout_ms)
    assert payload["timeoutMs"] == timeout_ms
    assert payload["signature"].startswith("0x")


# ============================================================================
# modifyOrder targeting
# ============================================================================


def _modify_params(**overrides: Any) -> ModifyOrderParameters:
    """A complete, valid post-modify state targeting by order_id.

    The resting order is GTT (its ``expiresAfter`` is strictly after the
    deadline), so the non-zero ``expires_after`` here satisfies the
    GTC/GTT↔``expiresAfter`` coupling. A modify cannot flip TIF — the caller
    restates the resting order's immutable TIF.
    """
    fields: dict[str, Any] = {
        "symbol": PERP_SYMBOL,
        "is_buy": True,
        "limit_px": "2950",
        "qty": "0.75",
        "post_only": True,
        "expires_after": 1745003600,
        "time_in_force": TimeInForce.GTT,
        "order_id": 63552420354981888,
        "deadline": PINNED_DEADLINE,
        "nonce": PINNED_NONCE,
    }
    fields.update(overrides)
    return ModifyOrderParameters(**fields)


def _trigger_modify_params(**overrides: Any) -> ModifyOrderParameters:
    """A valid trigger reprice restating the armed trigger's immutables.

    The armed trigger is GTC with no expiry, and ``limit_px`` sits inside the
    fixture market's 0.05 band around ``trigger_px``.
    """
    fields: dict[str, Any] = {
        "order_type": OrderType.STOP_LOSS,
        "trigger_px": "1500",
        "limit_px": "1450",
        "qty": None,
        "post_only": False,
        "expires_after": None,
        "time_in_force": TimeInForce.GTC,
    }
    fields.update(overrides)
    return _modify_params(**fields)


@pytest.mark.modify
def test_modify_with_both_identifiers_builds_and_restates_client_id(client: ReyaTradingClient) -> None:
    payload, _nonce = client.build_modify_order_payload(_modify_params(client_order_id=777))

    assert payload["orderId"] == "63552420354981888"
    assert payload["clientOrderId"] == "777"
    assert payload["signature"] == _expected_modify_signature(client, signed_client_order_id=777)


@pytest.mark.modify
def test_modify_with_neither_identifier_rejected(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="Provide order_id or client_order_id"):
        client.build_modify_order_payload(_modify_params(order_id=None))


@pytest.mark.modify
def test_modify_with_client_order_id_zero_rejected(client: ReyaTradingClient) -> None:
    """client_order_id=0 is not a JSON no-tag placeholder."""
    with pytest.raises(ValueError, match="client_order_id must be omitted"):
        client.build_modify_order_payload(_modify_params(order_id=None, client_order_id=0))


@pytest.mark.modify
def test_modify_parameters_rejects_resting_client_order_id_alias() -> None:
    """PRO-438 collapsed modify IDs to one client_order_id field."""
    with pytest.raises(TypeError, match="resting_client_order_id"):
        _modify_params(resting_client_order_id=42)


@pytest.mark.modify
@pytest.mark.parametrize("order_type", [OrderType.STOP_LOSS, OrderType.TAKE_PROFIT])
def test_modify_trigger_order_type_requires_trigger_px(client: ReyaTradingClient, order_type: OrderType) -> None:
    """A STOP_LOSS/TAKE_PROFIT reprice must carry trigger_px (full-restate: the
    ME re-validates the trigger price as positive), rejected before signing. qty
    is omitted (the signer derives the full-position sentinel)."""
    with pytest.raises(ValueError, match="trigger_px is required"):
        client.build_modify_order_payload(_trigger_modify_params(order_type=order_type, trigger_px=None))


@pytest.mark.modify
@pytest.mark.parametrize("order_type", [OrderType.STOP_LOSS, OrderType.TAKE_PROFIT])
def test_modify_trigger_order_rejects_qty(client: ReyaTradingClient, order_type: OrderType) -> None:
    """A TP/SL modify must omit qty (the signer derives the full-position
    sentinel); a supplied qty is a targeted client-side ValueError."""
    with pytest.raises(ValueError, match="qty on TP/SL trigger orders is not supported"):
        client.build_modify_order_payload(_trigger_modify_params(order_type=order_type, qty="0.75"))


@pytest.mark.modify
def test_modify_trigger_rejects_post_only(client: ReyaTradingClient) -> None:
    """A trigger create is never post-only, so a modify restating one signs a
    shape the matching engine must reject."""
    with pytest.raises(ValueError, match="post_only on TP/SL"):
        client.build_modify_order_payload(_trigger_modify_params(post_only=True))


@pytest.mark.modify
@pytest.mark.trigger
@pytest.mark.parametrize(
    ("armed", "label"),
    [
        ({"time_in_force": TimeInForce.GTC, "expires_after": None}, "GTC"),
        ({"time_in_force": TimeInForce.IOC, "expires_after": None}, "IOC"),
        ({"time_in_force": TimeInForce.GTT, "expires_after": PINNED_DEADLINE + 600}, "GTT"),
    ],
)
def test_modify_trigger_restating_the_armed_tif_and_expiry_builds(
    client: ReyaTradingClient, armed: dict[str, Any], label: str
) -> None:
    """A reprice restates the armed TIF and expiry unchanged and is admitted.

    Restating them is the ONLY legal thing a trigger modify does with these two
    fields, so every armed shape must survive the round trip — including a
    re-sign at unchanged prices."""
    payload, _nonce = client.build_modify_order_payload(_trigger_modify_params(**armed))

    assert payload["timeInForce"] == label
    if armed["expires_after"] is None:
        assert "expiresAfter" not in payload
    else:
        assert payload["expiresAfter"] == armed["expires_after"]


@pytest.mark.modify
@pytest.mark.trigger
@pytest.mark.parametrize(
    ("armed", "changed"),
    [
        # Armed GTC/IOC (no expiry) re-signed as GTT: the restated expiry is
        # still absent, which is not a GTT any client could have armed.
        ({"time_in_force": TimeInForce.GTC, "expires_after": None}, {"time_in_force": TimeInForce.GTT}),
        ({"time_in_force": TimeInForce.IOC, "expires_after": None}, {"time_in_force": TimeInForce.GTT}),
        # Armed GTT re-signed as GTC/IOC while restating the armed expiry.
        (
            {"time_in_force": TimeInForce.GTT, "expires_after": PINNED_DEADLINE + 600},
            {"time_in_force": TimeInForce.GTC},
        ),
        (
            {"time_in_force": TimeInForce.GTT, "expires_after": PINNED_DEADLINE + 600},
            {"time_in_force": TimeInForce.IOC},
        ),
    ],
    ids=["gtc-to-gtt", "ioc-to-gtt", "gtt-to-gtc", "gtt-to-ioc"],
)
def test_modify_trigger_changing_the_armed_tif_is_refused(
    client: ReyaTradingClient, armed: dict[str, Any], changed: dict[str, Any]
) -> None:
    """Changing the fired child's TIF means cancel-and-recreate, and the message
    says so rather than leaving the caller to decode a server rejection."""
    with pytest.raises(ValueError, match="cancel the trigger and create a new one"):
        client.build_modify_order_payload(_trigger_modify_params(**{**armed, **changed}))


@pytest.mark.modify
@pytest.mark.trigger
@pytest.mark.parametrize("armed_tif", [TimeInForce.GTC, TimeInForce.IOC], ids=["gtc", "ioc"])
def test_modify_trigger_adding_an_expiry_is_refused(client: ReyaTradingClient, armed_tif: TimeInForce) -> None:
    """`expires_after` is restate-immutable too: a trigger armed without one
    cannot grow a lifetime through a reprice."""
    with pytest.raises(ValueError, match="cancel the trigger and create a new one"):
        client.build_modify_order_payload(
            _trigger_modify_params(time_in_force=armed_tif, expires_after=PINNED_DEADLINE + 600)
        )


@pytest.mark.modify
@pytest.mark.trigger
def test_modify_trigger_dropping_the_armed_expiry_is_refused(client: ReyaTradingClient) -> None:
    """The mirror image: a GTT trigger cannot be repriced into a lifetime-free one."""
    with pytest.raises(ValueError, match="cancel the trigger and create a new one"):
        client.build_modify_order_payload(_trigger_modify_params(time_in_force=TimeInForce.GTT, expires_after=None))


@pytest.mark.modify
def test_modify_limit_order_requires_qty(client: ReyaTradingClient) -> None:
    """A LIMIT modify still requires qty — omitting it is a clear ValueError."""
    with pytest.raises(ValueError, match="qty is required when modifying a LIMIT order"):
        client.build_modify_order_payload(_modify_params(qty=None))


# ============================================================================
# client_order_id resolution into the SIGNED OrderDetails.clientOrderId
# ============================================================================


def _expected_modify_signature(client: ReyaTradingClient, signed_client_order_id: int) -> str:
    """Re-sign the _modify_params() post-modify state with an explicit
    OrderDetails.clientOrderId; comparing against the builder's signature
    pins which clientOrderId actually got signed."""
    return client.signature_generator.sign_order(
        account_id=12345,
        market_id=1,
        exchange_id=2,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal("0.75"),
        limit_price=Decimal("2950"),
        trigger_price=Decimal("0"),
        time_in_force=int(TimeInForceInt.GTT),
        client_order_id=signed_client_order_id,
        reduce_only=False,
        expires_after=1745003600,
        nonce=PINNED_NONCE,
        deadline=PINNED_DEADLINE,
        post_only=True,
    )


@pytest.mark.modify
def test_modify_client_order_id_targeting_defaults_signed_client_order_id(
    client: ReyaTradingClient,
) -> None:
    """Targeting by client_order_id uses the same value as the signed
    OrderDetails.clientOrderId."""
    payload, _nonce = client.build_modify_order_payload(_modify_params(order_id=None, client_order_id=777))
    assert payload["signature"] == _expected_modify_signature(client, signed_client_order_id=777)
    # Sanity: the default actually matters — 0 would sign different bytes.
    assert payload["signature"] != _expected_modify_signature(client, signed_client_order_id=0)


@pytest.mark.modify
def test_modify_order_id_targeting_client_order_id_restates_immutable(client: ReyaTradingClient) -> None:
    """When targeting by order_id, client_order_id restates the resting order's
    signed OrderDetails.clientOrderId."""
    payload, _nonce = client.build_modify_order_payload(_modify_params(client_order_id=777))
    assert payload["signature"] == _expected_modify_signature(client, signed_client_order_id=777)
    assert payload["signature"] != _expected_modify_signature(client, signed_client_order_id=0)
    assert payload["clientOrderId"] == "777"


@pytest.mark.modify
def test_modify_order_id_targeting_rejects_explicit_client_order_id_zero(client: ReyaTradingClient) -> None:
    """No-tag order-id targeting omits client_order_id instead of sending 0."""
    with pytest.raises(ValueError, match="client_order_id must be omitted"):
        client.build_modify_order_payload(_modify_params(client_order_id=0))


@pytest.mark.modify
def test_modify_order_id_targeting_signs_client_order_id_zero(client: ReyaTradingClient) -> None:
    """Targeting by order_id without client_order_id signs 0 and omits the
    JSON field."""
    payload, _nonce = client.build_modify_order_payload(_modify_params())
    assert payload["signature"] == _expected_modify_signature(client, signed_client_order_id=0)
    assert "clientOrderId" not in payload


# ============================================================================
# modify TIF<->expiresAfter coupling (against the resting order's immutable TIF)
# ============================================================================


@pytest.mark.modify
def test_modify_gtt_with_future_expiry_builds(client: ReyaTradingClient) -> None:
    """A GTT modify whose expiresAfter is strictly after the deadline builds —
    the resting GTT stays auto-expiring after the in-place edit."""
    payload, _nonce = client.build_modify_order_payload(_modify_params())
    assert payload["expiresAfter"] == 1745003600


@pytest.mark.modify
def test_modify_gtt_without_expiry_rejected(client: ReyaTradingClient) -> None:
    """A GTT modify must keep a non-zero expiresAfter — dropping it would turn a
    resting GTT into a never-expiring order (which is GTC). Rejected."""
    with pytest.raises(ValueError, match="GTT orders require a non-zero expires_after"):
        client.build_modify_order_payload(_modify_params(expires_after=None))


@pytest.mark.modify
def test_modify_gtt_expiry_not_after_deadline_rejected(client: ReyaTradingClient) -> None:
    """A GTT modify whose expiresAfter is not strictly after the deadline is
    rejected — the order would expire within its own entry window."""
    with pytest.raises(ValueError, match="GTT expires_after must be greater than deadline"):
        client.build_modify_order_payload(_modify_params(expires_after=PINNED_DEADLINE))


@pytest.mark.modify
def test_modify_gtc_with_expiry_rejected(client: ReyaTradingClient) -> None:
    """A GTC modify must not carry an expiry — GTC never expires. Pairing it
    with a non-zero expiresAfter is the legacy GTC-with-expiry shape (now GTT)."""
    with pytest.raises(ValueError, match="GTC orders must omit expires_after"):
        client.build_modify_order_payload(_modify_params(time_in_force=TimeInForce.GTC))


@pytest.mark.modify
def test_modify_gtc_without_expiry_builds(client: ReyaTradingClient) -> None:
    """A GTC modify with no expiresAfter builds — GTC rests until cancelled.
    The signer encodes no-expiry internally; JSON omits the field."""
    payload, _nonce = client.build_modify_order_payload(
        _modify_params(time_in_force=TimeInForce.GTC, expires_after=None)
    )
    assert "expiresAfter" not in payload


def test_create_rejects_explicit_zero_client_order_id(client: ReyaTradingClient) -> None:
    with pytest.raises(ValueError, match="client_order_id must be omitted"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTC,
                client_order_id=0,
            )
        )


# ============================================================================
# post-only gate-lift (signed-level extension of the wire-shape tests)
# ============================================================================


@pytest.mark.post_only
def test_post_only_gtc_flows_and_is_signed(client: ReyaTradingClient) -> None:
    """post_only=True + GTC flows (gate lifted), the payload carries
    postOnly=true, AND the signature covers postOnly=True — re-signing the
    same envelope with the returned nonce reproduces the payload's bytes.
    The IOC and GTT rejections stay pinned in test_wire_serialization.py."""
    payload, nonce = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px="3000",
            qty="0.5",
            time_in_force=TimeInForce.GTC,
            post_only=True,
            client_order_id=42,
            deadline=PINNED_DEADLINE,
        )
    )
    assert payload["postOnly"] is True
    expected = client.signature_generator.sign_order(
        account_id=12345,
        market_id=1,
        exchange_id=2,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal("0.5"),
        limit_price=Decimal("3000"),
        trigger_price=Decimal("0"),
        time_in_force=int(TimeInForceInt.GTC),
        client_order_id=42,
        reduce_only=False,
        expires_after=0,
        nonce=nonce,
        deadline=PINNED_DEADLINE,
        post_only=True,
    )
    assert payload["signature"] == expected


@pytest.mark.post_only
def test_post_only_gtt_flows_and_is_signed(client: ReyaTradingClient) -> None:
    """post_only=True + GTT flows (a GTT rests until it expires, so it can be
    maker-only), the payload carries postOnly=true AND a non-zero expiresAfter,
    and the signature covers BOTH postOnly=True and timeInForce==GTT(2) —
    re-signing the same envelope reproduces the payload's bytes. Falsifiability:
    re-signing as GTC (the only other resting TIF) must NOT match, proving the
    GTT int is what got signed."""
    expires_after = PINNED_DEADLINE + 600
    payload, nonce = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px="3000",
            qty="0.5",
            time_in_force=TimeInForce.GTT,
            post_only=True,
            client_order_id=42,
            deadline=PINNED_DEADLINE,
            expires_after=expires_after,
        )
    )
    assert payload["postOnly"] is True
    assert payload["timeInForce"] == TimeInForce.GTT.value
    assert payload["expiresAfter"] == expires_after

    def _resign(tif_int: int) -> str:
        return client.signature_generator.sign_order(
            account_id=12345,
            market_id=1,
            exchange_id=2,
            order_type=int(OrderTypeInt.LIMIT),
            is_buy=True,
            qty=Decimal("0.5"),
            limit_price=Decimal("3000"),
            trigger_price=Decimal("0"),
            time_in_force=tif_int,
            client_order_id=42,
            reduce_only=False,
            expires_after=expires_after,
            nonce=nonce,
            deadline=PINNED_DEADLINE,
            post_only=True,
        )

    assert payload["signature"] == _resign(int(TimeInForceInt.GTT))
    # Sanity: the GTT int matters — re-signing as GTC signs different bytes.
    assert payload["signature"] != _resign(int(TimeInForceInt.GTC))


# ============================================================================
# trigger creates: the TIF <-> expiresAfter coupling and the limit band
# ============================================================================


def _trigger_create_params(**overrides: Any) -> TriggerOrderParameters:
    """A STOP_LOSS whose limit sits inside the banded market's 0.05 band."""
    fields: dict[str, Any] = {
        "symbol": BANDED_SYMBOL,
        "is_buy": False,
        "trigger_px": "1000",
        "limit_px": "990",
        "trigger_type": OrderType.STOP_LOSS,
        "time_in_force": TimeInForce.GTC,
        "deadline": PINNED_DEADLINE,
    }
    fields.update(overrides)
    return TriggerOrderParameters(**fields)


@pytest.mark.trigger
@pytest.mark.parametrize("time_in_force", [TimeInForce.GTC, TimeInForce.IOC], ids=["gtc", "ioc"])
def test_trigger_create_with_a_non_zero_expiry_on_a_lifetimeless_tif_rejected(
    client: ReyaTradingClient, time_in_force: TimeInForce
) -> None:
    """Only GTT carries a lifetime. A GTC or IOC trigger that signs one is
    refused before a nonce is claimed."""
    nonce_before = _last_nonce(client)
    with pytest.raises(ValueError, match="must omit expires_after"):
        client.build_create_trigger_order_payload(
            _trigger_create_params(time_in_force=time_in_force, expires_after=PINNED_DEADLINE + 600)
        )
    assert _last_nonce(client) == nonce_before, "a refused trigger consumed a nonce"


@pytest.mark.trigger
@pytest.mark.gtt
def test_gtt_trigger_create_without_an_expiry_rejected(client: ReyaTradingClient) -> None:
    """A GTT that never expires is a contradiction (that is GTC)."""
    with pytest.raises(ValueError, match="GTT trigger orders require a non-zero expires_after"):
        client.build_create_trigger_order_payload(_trigger_create_params(time_in_force=TimeInForce.GTT))


@pytest.mark.trigger
@pytest.mark.gtt
def test_gtt_trigger_create_expiring_before_its_own_deadline_rejected(client: ReyaTradingClient) -> None:
    """The order has to outlive the window it may be submitted in."""
    with pytest.raises(ValueError, match="GTT expires_after must be greater than deadline"):
        client.build_create_trigger_order_payload(
            _trigger_create_params(time_in_force=TimeInForce.GTT, expires_after=PINNED_DEADLINE - 1)
        )


@pytest.mark.trigger
def test_trigger_create_error_messages_do_not_mention_cancel_and_recreate(client: ReyaTradingClient) -> None:
    """The restate-immutable remedy belongs to a modify. Nothing is armed yet on
    a create, so telling the caller to cancel a trigger would be nonsense."""
    with pytest.raises(ValueError) as excinfo:
        client.build_create_trigger_order_payload(_trigger_create_params(expires_after=PINNED_DEADLINE + 600))
    assert "cancel the trigger" not in str(excinfo.value)


@pytest.mark.trigger
@pytest.mark.parametrize("limit_px", ["1050", "950", "1000", "1049.999", "950.001"])
def test_trigger_limit_inside_the_band_is_admitted(client: ReyaTradingClient, limit_px: str) -> None:
    """`|limit_px - trigger_px| <= trigger_px * fraction`, inclusive at both
    outermost tick-aligned bounds: 1000 * 0.05 = 50, so [950, 1050] on a
    tick of 0.001."""
    payload, _nonce = client.build_create_trigger_order_payload(_trigger_create_params(limit_px=limit_px))
    assert payload["limitPx"] == limit_px


@pytest.mark.trigger
@pytest.mark.parametrize("limit_px", ["1050.001", "949.999", "2000", "0.001"])
def test_trigger_limit_outside_the_band_is_refused_before_signing(client: ReyaTradingClient, limit_px: str) -> None:
    """A limit the venue would answer with TRIGGER_LIMIT_OUTSIDE_BAND_ERROR is
    refused here instead, and the message carries the legal range and the
    fraction so the caller can reprice without reading the spec."""
    nonce_before = _last_nonce(client)
    with pytest.raises(ValueError, match="outside") as excinfo:
        client.build_create_trigger_order_payload(_trigger_create_params(limit_px=limit_px))

    message = str(excinfo.value)
    assert "[950, 1050]" in message, message
    assert "0.05" in message, message
    assert _last_nonce(client) == nonce_before, "a refused trigger consumed a nonce"


@pytest.mark.trigger
def test_trigger_band_bounds_round_inward_to_the_tick(client: ReyaTradingClient) -> None:
    """The outermost legal price is the last tick-aligned one at or INSIDE the
    raw bound. Rounding the lower bound outward would admit a limit the venue
    refuses, which is the whole point of checking client-side."""
    client._symbol_to_tick_size[BANDED_SYMBOL] = "0.03"
    # 1000 * 0.95 = 950 is not a multiple of 0.03, so the lower bound moves UP
    # to 950.01. Truncating toward zero instead would report 949.98 and admit a
    # limit price below the band.
    with pytest.raises(ValueError, match=r"\[950\.01, 1050\]"):
        client.build_create_trigger_order_payload(_trigger_create_params(limit_px="950.00"))

    payload, _nonce = client.build_create_trigger_order_payload(_trigger_create_params(limit_px="950.01"))
    assert payload["limitPx"] == "950.01"


@pytest.mark.trigger
@pytest.mark.parametrize("limit_px", ["0.000001", "1", "999999"])
def test_zero_fraction_disables_the_band(client: ReyaTradingClient, limit_px: str) -> None:
    """A published "0" is the band switched OFF: any positive limit is admitted,
    however far from the trigger."""
    payload, _nonce = client.build_create_trigger_order_payload(
        _trigger_create_params(symbol=UNBANDED_SYMBOL, limit_px=limit_px)
    )
    assert payload["limitPx"] == limit_px


@pytest.mark.trigger
def test_a_non_positive_limit_price_is_refused_even_with_the_band_disabled(client: ReyaTradingClient) -> None:
    """ "0" admits any POSITIVE limit; zero itself is not a price."""
    with pytest.raises(ValueError, match="limit_px must be a positive price"):
        client.build_create_trigger_order_payload(_trigger_create_params(symbol=UNBANDED_SYMBOL, limit_px="0"))


@pytest.mark.trigger
def test_an_absent_band_refuses_every_trigger(client: ReyaTradingClient) -> None:
    """An ABSENT fraction is not a fraction of "0": the market has no band
    configured, the venue arms no stop on it at all, and no limit price is
    admissible. Reading absence as "0" would flip a fail-closed market into one
    that accepts everything."""
    with pytest.raises(ValueError, match="no triggerLimitBandFraction"):
        client.build_create_trigger_order_payload(_trigger_create_params(symbol=NO_BAND_SYMBOL))


@pytest.mark.trigger
@pytest.mark.modify
def test_an_absent_band_refuses_a_reprice_too(client: ReyaTradingClient) -> None:
    """The venue re-checks the pair on modify, so the client does too."""
    with pytest.raises(ValueError, match="no triggerLimitBandFraction"):
        client.build_modify_order_payload(_trigger_modify_params(symbol=NO_BAND_SYMBOL))


@pytest.mark.trigger
@pytest.mark.modify
def test_a_reprice_outside_the_band_is_refused(client: ReyaTradingClient) -> None:
    """Moving the trigger without moving the limit can put the pair outside the
    band even though neither value is new."""
    with pytest.raises(ValueError, match="outside"):
        client.build_modify_order_payload(_trigger_modify_params(trigger_px="3000"))
