# pylint: disable=protected-access,redefined-outer-name
"""Wire-serialization guards for the order-payload builders.

Offline (no devnet): builds payloads with a fixed key + a hand-seeded
symbol→marketId map, and asserts on the emitted wire shape — numeric fields as
plain decimal strings (never scientific notation), the decoupled
``deadline`` / ``expiresAfter`` fields, and the order/cancel entry-rule guards
(``reduceOnly``, ``postOnly``, GTT, and the cancel-identifier rules).

Regression: the sell-trigger sentinel limit price is ``Decimal("0.000000001")``,
and ``str(Decimal("0.000000001"))`` is ``"1E-9"``. The server's ethers
``FixedNumber`` parser rejects ``"1E-9"`` with INVALID_ARGUMENT, so a TP/SL
sell with no explicit limit price failed on the wire even though the signature
was correct. The builder now uses ``format(value, "f")``.
"""

from __future__ import annotations

import time

import pytest

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.client import _SPOT_MARKET_ID_OFFSET
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters

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
            qty="0.01",
            trigger_px="1",
            trigger_type=OrderType.STOP_LOSS,
        )
    )
    assert payload["limitPx"] == "0.001"  # == market tick size
    assert "E" not in payload["limitPx"].upper(), f"limitPx in scientific notation: {payload['limitPx']!r}"


def test_buy_trigger_sentinel_limit_px_is_plain_decimal(client: ReyaTradingClient) -> None:
    """is_buy=True + no limit_px → huge sentinel must also be plain (no 'E')."""
    payload, _ = client.build_create_trigger_order_payload(
        TriggerOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            qty="0.01",
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
            qty="0.01",
            trigger_px="1",
            trigger_type=OrderType.STOP_LOSS,
            limit_px="0.0000001",
        )
    )
    assert payload["limitPx"] == "0.0000001"
    assert "E" not in payload["limitPx"].upper(), f"limitPx in scientific notation: {payload['limitPx']!r}"


def test_limit_payload_decouples_deadline_from_expires_after(client: ReyaTradingClient) -> None:
    """GTC limit: ``expiresAfter`` is 0 / perpetual and ``deadline`` is a short,
    independent unix-seconds window — not the old far-future
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
    # Perpetual lifetime — rests until filled or cancelled.
    assert payload["expiresAfter"] == 0
    # A near-future unix-seconds deadline, decoupled from (and not equal to) expiresAfter.
    assert before <= payload["deadline"] <= before + 600
    assert payload["deadline"] != payload["expiresAfter"]


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


def test_post_only_true_rejected_pending_offchain(client: ReyaTradingClient) -> None:
    """post_only=True on a GTC limit is rejected until the off-chain 14-field digest
    reconstruction lands — no silently un-settleable order. (The OpenAPI/wire field
    is already present; the off-chain side is the remaining gate.)"""
    with pytest.raises(ValueError, match="post_only=True is not yet supported"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTC,
                post_only=True,
            )
        )


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


def test_gtt_rejected_pending_offchain(client: ReyaTradingClient) -> None:
    """GTT is exposed in the OpenAPI enum and signing-capable, but rejected at entry
    until the off-chain 14-field digest + GTT expiresAfter validation land — rather
    than a KeyError on the GTC/IOC-only TIF map or an un-settleable order."""
    with pytest.raises(ValueError, match="GTT time-in-force is not yet supported"):
        client.build_create_limit_order_payload(
            LimitOrderParameters(
                symbol=PERP_SYMBOL,
                is_buy=True,
                limit_px="3000",
                qty="0.01",
                time_in_force=TimeInForce.GTT,
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
                qty="0.01",
                trigger_px="1000",
                trigger_type=OrderType.STOP_LOSS,
                reduce_only=True,
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
    assert payload["clientOrderId"] == 456
