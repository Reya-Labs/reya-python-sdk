"""Publication metadata is optional and must not restrict API market discovery."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import logging
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from sdk.open_api.models.market_definition import MarketDefinition
from sdk.reya_rest_api import ReyaTradingClient

pytestmark = pytest.mark.offline


def market_payload() -> dict[str, Any]:
    return {
        "symbol": "MSFTRUSDPERP",
        "marketId": 79,
        "minOrderQty": "0.02",
        "qtyStepSize": "0.001",
        "tickSize": "0.01",
        "liquidationMarginParameter": "0.04",
        "initialMarginParameter": "0.05",
        "maxLeverage": 20,
        "oiCap": "1000",
    }


@pytest.mark.parametrize("visible", [True, False])
def test_visibility_round_trips_as_typed_boolean(visible: bool) -> None:
    payload = {**market_payload(), "uiVisible": visible}
    market = MarketDefinition.from_dict(payload)
    assert market is not None
    assert market.ui_visible is visible
    assert "uiVisible" not in market.additional_properties
    assert market.to_dict() == payload


def test_older_api_payload_needs_no_visibility_or_operating_status() -> None:
    market = MarketDefinition.from_dict(market_payload())
    assert market is not None
    assert market.ui_visible is None
    assert market.to_dict() == market_payload()
    # Public status should be introduced when published non-trading states exist.
    assert "status" not in MarketDefinition.model_fields


@pytest.mark.parametrize("invalid", ["false", 0, 1])
def test_visibility_does_not_coerce_non_boolean_values(invalid: object) -> None:
    with pytest.raises(ValidationError):
        MarketDefinition.from_dict({**market_payload(), "uiVisible": invalid})


async def test_api_only_market_remains_in_signing_map() -> None:
    market = MarketDefinition.from_dict({**market_payload(), "uiVisible": False})
    client = ReyaTradingClient.__new__(ReyaTradingClient)
    # No session, key, environment file or network is needed to exercise startup.
    client._resources = cast(  # pylint: disable=protected-access
        Any,
        SimpleNamespace(
            reference=SimpleNamespace(
                get_perp_market_definitions=AsyncMock(return_value=[market]),
                get_spot_market_definitions=AsyncMock(return_value=[]),
            )
        ),
    )
    client.logger = logging.getLogger(__name__)
    await client.start()
    assert client.get_market_id_from_symbol("MSFTRUSDPERP") == 79
