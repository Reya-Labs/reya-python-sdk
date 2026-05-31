# pylint: disable=protected-access,redefined-outer-name
"""Wire-serialization guards for the order-payload builders.

Offline (no devnet): builds payloads with a fixed key + a hand-seeded
symbol→marketId map, and asserts numeric wire fields are emitted as
plain decimal strings — never scientific notation.

Regression: the sell-trigger sentinel limit price is ``Decimal("0.000000001")``,
and ``str(Decimal("0.000000001"))`` is ``"1E-9"``. The server's ethers
``FixedNumber`` parser rejects ``"1E-9"`` with INVALID_ARGUMENT, so a TP/SL
sell with no explicit limit price failed on the wire even though the signature
was correct. The builder now uses ``format(value, "f")``.
"""

from __future__ import annotations

import pytest

from sdk.open_api.models.order_type import OrderType
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import TriggerOrderParameters

PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SIGNER_ADDRESS = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
CHAIN_ID = 89346162
PERP_SYMBOL = "ETHRUSDPERP"


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
    c._symbol_to_market_id = {PERP_SYMBOL: 1}  # perp core id, unified == raw
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
