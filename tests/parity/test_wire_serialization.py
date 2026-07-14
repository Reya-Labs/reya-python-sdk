# pylint: disable=protected-access,redefined-outer-name
"""Wire-serialization guards for the order-payload builders.

Offline (no devnet): builds payloads with a fixed key + a hand-seeded
symbol→marketId map, and asserts on the emitted wire shape — numeric fields as
plain decimal strings (never scientific notation), the decoupled
``deadline`` / ``expiresAfter`` fields, and the order/cancel entry-rule guards
(``reduceOnly``, ``postOnly``, the GTC/GTT↔``expiresAfter`` coupling, and the
cancel-identifier rules).

Regression: the sell-trigger sentinel limit price is ``Decimal("0.000000001")``,
and ``str(Decimal("0.000000001"))`` is ``"1E-9"``. The server's ethers
``FixedNumber`` parser rejects ``"1E-9"`` with INVALID_ARGUMENT, so a TP/SL
sell with no explicit limit price failed on the wire even though the signature
was correct. The builder now uses ``format(value, "f")``.
"""

from __future__ import annotations

from typing import Any

import time
from decimal import Decimal

import pytest

from sdk.open_api.models.cancel_all_after_request import CancelAllAfterRequest
from sdk.open_api.models.modify_order_request import ModifyOrderRequest
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, TimeInForceInt
from sdk.reya_rest_api.client import _SPOT_MARKET_ID_OFFSET
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, ModifyOrderParameters, TriggerOrderParameters

pytestmark = pytest.mark.offline

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CHAIN_ID = 89346162
PERP_SYMBOL = "ETHRUSDPERP"
SPOT_SYMBOL = "WETHRUSD"  # market_id >= _SPOT_MARKET_ID_OFFSET => spot namespace


@pytest.fixture
def client() -> ReyaTradingClient:
    """A ReyaTradingClient that can build payloads offline.

    Seeds the symbol→marketId map directly instead of calling ``start()``
    (which loads market definitions over the network) so the builders are
    exercised without any devnet dependency.
    """
    config = TradingConfig(
        api_url="https://invalid.example",  # never called — building is pure
        chain_id=CHAIN_ID,
        owner_wallet_address=SIGNER_ADDRESS,
        private_key=PRIVATE_KEY,
        account_id=12345,
    )
    c = ReyaTradingClient(config)
    c._symbol_to_market_id = {PERP_SYMBOL: 1, SPOT_SYMBOL: _SPOT_MARKET_ID_OFFSET + 1}
    c._symbol_to_tick_size = {PERP_SYMBOL: "0.001"}  # tick size drives the sell-trigger sentinel
    c._initialized = True
    return c


def test_sell_trigger_sentinel_is_one_tick(client: ReyaTradingClient) -> None:
    """is_buy=False + no limit_px → sentinel is exactly one tick (spacing-conforming),
    not a sub-tick value the matching engine would reject as off-grid."""
    payload, _ = client.build_create_trigger_order_payload(
        TriggerOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            trigger_px="1",
            trigger_type=OrderType.STOP_LOSS,
        )
    )
    assert payload["limitPx"] == "0.001"  # == market tick size
    assert "E" not in payload["limitPx"].upper(), f"limitPx in scientific notation: {payload['limitPx']!r}"
    assert "qty" not in payload
    assert "expiresAfter" not in payload


def test_buy_trigger_sentinel_limit_px_is_plain_decimal(client: ReyaTradingClient) -> None:
    """is_buy=True + no limit_px → huge sentinel must also be plain (no 'E')."""
    payload, _ = client.build_create_trigger_order_payload(
        TriggerOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            trigger_px="1000000",
            trigger_type=OrderType.TAKE_PROFIT,
        )
    )
    assert payload["limitPx"] == "100000000000000000000"
    assert "E" not in payload["limitPx"].upper()


def test_caller_supplied_small_limit_px_is_plain_decimal(client: ReyaTradingClient) -> None:
    """A small explicit limit_px must serialize as a plain decimal, never sci
    notation: str(Decimal("0.0000001")) == "1E-7", which the server's ethers
    FixedNumber parser rejects — this is exactly what format(..., "f") guards."""
    payload, _ = client.build_create_trigger_order_payload(
        TriggerOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            trigger_px="1",
            trigger_type=OrderType.STOP_LOSS,
            limit_px="0.0000001",
        )
    )
    assert payload["limitPx"] == "0.0000001"
    assert "E" not in payload["limitPx"].upper(), f"limitPx in scientific notation: {payload['limitPx']!r}"


def test_limit_payload_decouples_deadline_from_expires_after(client: ReyaTradingClient) -> None:
    """GTC limit: ``expiresAfter`` is omitted from JSON and ``deadline`` is a
    short, independent unix-seconds window — not the old far-future
    ``deadline == expiresAfter`` lifetime stopgap."""
    before = int(time.time())
    payload, _ = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px="3000",
            qty="0.01",
            time_in_force=TimeInForce.GTC,
        )
    )
    assert "expiresAfter" not in payload
    # A near-future unix-seconds deadline, decoupled from order lifetime.
    assert before <= payload["deadline"] <= before + 600


def test_limit_payload_post_only_defaults_false(client: ReyaTradingClient) -> None:
    """postOnly is signed into the 14-field digest and carried on the wire as
    False by default (no caller opt-in)."""
    payload, _ = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px="3000",
            qty="0.01",
            time_in_force=TimeInForce.GTC,
        )
    )
    assert payload["postOnly"] is False


def test_post_only_true_flows_to_wire(client: ReyaTradingClient) -> None:
    """post_only=True on a GTC limit travels end-to-end now that the off-chain
    verifies the 14-field digest and the matching engine enforces would-cross:
    it is signed and carried on the wire, no longer rejected at entry."""
    payload, _ = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px="3000",
            qty="0.01",
            time_in_force=TimeInForce.GTC,
            post_only=True,
        )
    )
    assert payload["postOnly"] is True


def test_post_only_with_ioc_rejected(client: ReyaTradingClient) -> None:
    """post_only + IOC is self-contradictory (IOC is taker-only; post_only must
    rest) and is always rejected, independent of the rollout gate."""
    with pytest.raises(ValueError, match="post_only is not supported on IOC"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.IOC,
                post_only=True,
            )
        )


def test_gtt_accepted_and_signs_expires_after(client: ReyaTradingClient) -> None:
    """GTT rests like GTC but auto-expires at ``expiresAfter``: the order signs
    and the non-zero ``expiresAfter`` (strictly after the deadline) travels onto
    the wire — no longer rejected at entry now that the off-chain digest + ME
    rest/reap GTT end-to-end."""
    deadline = int(time.time()) + 60
    expires_after = deadline + 600
    payload, _nonce = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px="3000",
            qty="0.01",
            time_in_force=TimeInForce.GTT,
            deadline=deadline,
            expires_after=expires_after,
        )
    )
    assert payload["timeInForce"] == TimeInForce.GTT.value
    assert payload["expiresAfter"] == expires_after


def test_gtt_without_expiry_rejected(client: ReyaTradingClient) -> None:
    """GTT requires a non-zero ``expiresAfter`` — a GTT that never expires is a
    contradiction (that is GTC). Rejected at entry before signing."""
    with pytest.raises(ValueError, match="GTT orders require a non-zero expires_after"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTT,
            )
        )


def test_gtt_expiry_not_after_deadline_rejected(client: ReyaTradingClient) -> None:
    """A GTT whose ``expiresAfter`` is not strictly after the deadline would
    expire within (or before) its own entry window — rejected at entry."""
    deadline = int(time.time()) + 600
    with pytest.raises(ValueError, match="GTT expires_after must be greater than deadline"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTT,
                deadline=deadline,
                expires_after=deadline,
            )
        )


def test_gtc_with_expiry_rejected(client: ReyaTradingClient) -> None:
    """GTC never expires — pairing it with a non-zero ``expiresAfter`` is the
    legacy GTC-with-expiry shape that is now GTT. Rejected at entry."""
    with pytest.raises(ValueError, match="GTC orders must omit expires_after"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTC,
                expires_after=int(time.time()) + 600,
            )
        )


def test_reduce_only_on_spot_rejected(client: ReyaTradingClient) -> None:
    """reduce_only is perp-IOC-only; on a spot order it must be rejected at entry
    (the server forbids it on spot), not silently dropped."""
    with pytest.raises(ValueError, match="reduce_only is only supported on perp IOC"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=SPOT_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )
        )


def test_reduce_only_on_trigger_rejected(client: ReyaTradingClient) -> None:
    """reduce_only / close-on-trigger TP/SL isn't supported yet — an explicit
    reduce_only on a trigger order is rejected rather than signed + sent."""
    with pytest.raises(ValueError, match="reduce_only on TP/SL trigger orders is not supported"):
        client.build_create_trigger_order_payload(
            TriggerOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=False,
                trigger_px="1000",
                trigger_type=OrderType.STOP_LOSS,
                reduce_only=True,
            )
        )


def test_trigger_qty_rejected_client_side(client: ReyaTradingClient) -> None:
    """TP/SL trigger creates omit qty; callers must not send a JSON sentinel."""
    with pytest.raises(ValueError, match="qty on TP/SL trigger orders is not supported"):
        client.build_create_trigger_order_payload(
            TriggerOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=False,
                trigger_px="1000",
                trigger_type=OrderType.STOP_LOSS,
                qty="0.01",
            )
        )


def test_cancel_requires_an_identifier(client: ReyaTradingClient) -> None:
    """A cancel must carry at least one of order_id / client_order_id; neither is rejected."""
    with pytest.raises(ValueError, match="Provide either order_id or client_order_id"):
        client.build_cancel_order_payload(symbol=PERP_SYMBOL)


def test_cancel_accepts_both_identifiers(client: ReyaTradingClient) -> None:
    """The client accepts both order_id and client_order_id and carries both on the
    wire (the server resolves precedence in favour of order_id) rather than rejecting
    the combination."""
    payload = client.build_cancel_order_payload(
        symbol=PERP_SYMBOL,
        order_id="123",
        client_order_id=456,
    )
    assert payload["orderId"] == "123"
    assert payload["clientOrderId"] == "456"


def test_cancel_rejects_zero_client_order_id_target(client: ReyaTradingClient) -> None:
    """client_order_id=0 is not a JSON no-tag placeholder."""
    with pytest.raises(ValueError, match="client_order_id must be omitted"):
        client.build_cancel_order_payload(symbol=PERP_SYMBOL, client_order_id=0)


def _modify_params(**overrides: Any) -> ModifyOrderParameters:
    """A complete, valid post-modify state targeting by order_id.

    The resting order is GTT — the modifiable order that legitimately carries a
    non-zero ``expiresAfter`` (strictly after the deadline), satisfying the
    GTC/GTT↔``expiresAfter`` coupling.
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
        "deadline": 1745000300,
        "nonce": 1700000000000005,
    }
    fields.update(overrides)
    return ModifyOrderParameters(**fields)


@pytest.mark.modify
def test_modify_payload_wire_shape(client: ReyaTradingClient) -> None:
    """The modify body carries the post-modify fields, target identifier, and
    signerWallet, with unset optional JSON fields omitted."""
    payload, nonce = client.build_modify_order_payload(_modify_params())

    assert set(payload.keys()) == {
        "orderId",
        "symbol",
        "accountId",
        "exchangeId",
        "isBuy",
        "orderType",
        "timeInForce",
        "reduceOnly",
        "limitPx",
        "qty",
        "postOnly",
        "expiresAfter",
        "signature",
        "nonce",
        "signerWallet",
        "deadline",
    }
    # The four post-modify fields — always present, never None.
    assert payload["limitPx"] == "2950"
    assert payload["qty"] == "0.75"
    assert payload["postOnly"] is True
    assert payload["expiresAfter"] == 1745003600
    # Restated immutables (full-restate) — always present.
    assert payload["isBuy"] is True
    assert payload["orderType"] == "LIMIT"
    assert payload["timeInForce"] == "GTT"
    assert payload["reduceOnly"] is False
    assert "triggerPx" not in payload  # LIMIT carries no trigger
    assert isinstance(payload["exchangeId"], int)
    # Targeting + auth.
    assert payload["orderId"] == "63552420354981888"  # orderId is a STRING on the wire
    assert isinstance(payload["orderId"], str)
    # The fixture targets by orderId and has no clientOrderId. The signature
    # uses 0 internally, but JSON omits absent clientOrderId.
    assert "clientOrderId" not in payload
    assert payload["accountId"] == 12345
    assert payload["signerWallet"] == SIGNER_ADDRESS
    assert payload["signature"].startswith("0x")
    assert payload["nonce"] == str(nonce)  # nonce serializes as a string
    assert isinstance(payload["deadline"], int)


@pytest.mark.modify
def test_modify_payload_round_trips_generated_model(client: ReyaTradingClient) -> None:
    """The payload round-trips through the generated ModifyOrderRequest with
    orderId kept as a string (StrictStr per the OpenAPI model) and the four
    post-modify fields + targeting + signerWallet surviving serialization."""
    payload, _nonce = client.build_modify_order_payload(_modify_params())
    body = ModifyOrderRequest(**payload).to_dict()

    # to_dict drops None optionals: triggerPx (None for LIMIT) disappears;
    # clientOrderId is absent because this order has none; orderId stays.
    assert set(body.keys()) == {
        "orderId",
        "symbol",
        "accountId",
        "exchangeId",
        "isBuy",
        "orderType",
        "timeInForce",
        "reduceOnly",
        "limitPx",
        "qty",
        "postOnly",
        "expiresAfter",
        "signature",
        "nonce",
        "signerWallet",
        "deadline",
    }
    assert body["orderId"] == "63552420354981888"
    assert isinstance(body["orderId"], str)
    assert isinstance(body["postOnly"], bool)
    assert isinstance(body["expiresAfter"], int)
    assert isinstance(body["nonce"], str)


@pytest.mark.modify
def test_modify_payload_client_order_id_targeting_wire_shape(client: ReyaTradingClient) -> None:
    """Targeting by client_order_id: the body carries clientOrderId as a decimal
    string (uint64) and omits orderId."""
    payload, _nonce = client.build_modify_order_payload(_modify_params(order_id=None, client_order_id=777))
    assert "orderId" not in payload
    assert payload["clientOrderId"] == "777"
    body = ModifyOrderRequest(**payload).to_dict()
    assert "orderId" not in body
    assert body["clientOrderId"] == "777"


@pytest.mark.modify
def test_modify_payload_order_id_targeting_with_restated_client_order_id(client: ReyaTradingClient) -> None:
    """Targeting by order_id while restating a non-zero clientOrderId:
    the body carries both ids, and orderId remains the canonical target."""
    payload, _nonce = client.build_modify_order_payload(_modify_params(client_order_id=777))
    assert payload["orderId"] == "63552420354981888"
    assert payload["clientOrderId"] == "777"
    body = ModifyOrderRequest(**payload).to_dict()
    assert body["orderId"] == "63552420354981888"
    assert body["clientOrderId"] == "777"


@pytest.mark.modify
def test_modify_payload_carries_order_type(client: ReyaTradingClient) -> None:
    """A STOP_LOSS trigger reprice emits ``orderType: STOP_LOSS`` + ``triggerPx``
    on the wire and SIGNS the non-LIMIT order type — guarding the passthrough
    that replaced the two hardcoded LIMITs in ``build_modify_order_payload``.
    The default (no ``order_type``) stays an unchanged LIMIT modify."""
    # A trigger modify OMITS qty on the wire and signs quantity 0 (protect the
    # whole position), exactly like a trigger create.
    sl_payload, nonce = client.build_modify_order_payload(
        _modify_params(order_type=OrderType.STOP_LOSS, trigger_px="1500", qty=None)
    )
    assert sl_payload["orderType"] == "STOP_LOSS"
    assert sl_payload["triggerPx"] == "1500"
    assert "qty" not in sl_payload

    # The signature covers orderType=STOP_LOSS AND signed quantity 0: recompute
    # the digest with the builder's own signer over STOP_LOSS / qty=0 (pinned
    # nonce/deadline) and match it.
    market_id = client.get_market_id_from_symbol(PERP_SYMBOL)
    expected_sig = client.signature_generator.sign_order(
        account_id=12345,  # the client fixture's pinned account_id
        market_id=market_id,
        exchange_id=client.config.dex_id,
        order_type=int(OrderTypeInt.STOP_LOSS),
        is_buy=True,
        qty=Decimal(0),
        limit_price=Decimal("2950"),
        trigger_price=Decimal("1500"),
        time_in_force=int(TimeInForceInt.GTT),
        client_order_id=0,
        reduce_only=False,
        expires_after=1745003600,
        nonce=nonce,
        deadline=1745000300,
        post_only=True,
    )
    assert sl_payload["signature"] == expected_sig

    # LIMIT default unchanged: same pinned params, only order_type differs — so a
    # different signature proves order_type flows into signing (not hardcoded),
    # and the wire keeps the LIMIT shape (no triggerPx). qty stays present.
    limit_payload, _ = client.build_modify_order_payload(_modify_params())
    assert limit_payload["orderType"] == "LIMIT"
    assert "triggerPx" not in limit_payload
    assert limit_payload["qty"] == "0.75"
    assert limit_payload["signature"] != expected_sig


@pytest.mark.modify
def test_modify_trigger_qty_rejected_client_side(client: ReyaTradingClient) -> None:
    """A TP/SL modify must omit qty (the signed quantity restates 0); a supplied
    qty is a targeted client-side ValueError, mirroring the trigger-create guard."""
    with pytest.raises(ValueError, match="qty on TP/SL trigger orders is not supported"):
        client.build_modify_order_payload(
            _modify_params(order_type=OrderType.TAKE_PROFIT, trigger_px="1500", qty="0.75")
        )


@pytest.mark.modify
def test_modify_limit_qty_required_client_side(client: ReyaTradingClient) -> None:
    """A LIMIT modify still requires qty — omitting it is a clear ValueError."""
    with pytest.raises(ValueError, match="qty is required when modifying a LIMIT order"):
        client.build_modify_order_payload(_modify_params(qty=None))


@pytest.mark.cod
def test_cancel_all_after_payload_wire_shape(client: ReyaTradingClient) -> None:
    """The cancelAllAfter body is exactly {accountId, timeoutMs, signature,
    nonce, signerWallet, deadline} with the documented wire types, and
    round-trips through the generated CancelAllAfterRequest."""
    payload = client.build_cancel_all_after_payload(timeout_ms=30000)

    assert set(payload.keys()) == {
        "accountId",
        "timeoutMs",
        "signature",
        "nonce",
        "signerWallet",
        "deadline",
    }
    assert payload["accountId"] == 12345
    assert payload["timeoutMs"] == 30000
    assert isinstance(payload["timeoutMs"], int)
    assert payload["signature"].startswith("0x")
    assert isinstance(payload["nonce"], str)  # nonce serializes as a string
    assert payload["signerWallet"] == SIGNER_ADDRESS
    assert isinstance(payload["deadline"], int)

    body = CancelAllAfterRequest(**payload).to_dict()
    assert set(body.keys()) == set(payload.keys())
    assert body["timeoutMs"] == 30000
