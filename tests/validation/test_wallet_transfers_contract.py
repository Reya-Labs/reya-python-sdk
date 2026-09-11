"""Offline checks for the public transfer-history request contract."""

from types import SimpleNamespace
from typing import cast

import inspect
from unittest.mock import AsyncMock

import pytest

from sdk.open_api.api.wallet_data_api import WalletDataApi
from sdk.open_api.models.transfer_list import TransferList
from sdk.open_api.models.transfer_type import TransferType
from sdk.reya_rest_api.client import ReyaTradingClient

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_transfer_wrapper_forwards_supported_filters_only():
    history = TransferList.model_validate({"data": [], "meta": {"limit": 2, "count": 0}})
    wallet = SimpleNamespace(get_wallet_transfers=AsyncMock(return_value=history))
    client = cast(ReyaTradingClient, SimpleNamespace(owner_wallet_address="0xwallet", wallet=wallet))

    result = await ReyaTradingClient.get_transfers(
        client, limit=2, cursor="cursor", start_time=1000, end_time=2000, types=[TransferType.DEPOSIT]
    )

    assert result is history
    wallet.get_wallet_transfers.assert_awaited_once_with(
        address="0xwallet", limit=2, cursor="cursor", start_time=1000, end_time=2000, type=[TransferType.DEPOSIT]
    )
    with pytest.raises(TypeError, match="include_zero"):
        inspect.signature(ReyaTradingClient.get_transfers).bind(client, include_zero=True)


@pytest.mark.parametrize(
    "method_name",
    ["get_wallet_transfers", "get_wallet_transfers_with_http_info", "get_wallet_transfers_without_preload_content"],
)
def test_generated_transfer_methods_do_not_accept_zero_amount_opt_in(method_name):
    signature = inspect.signature(getattr(WalletDataApi, method_name))
    with pytest.raises(TypeError, match="include_zero"):
        signature.bind(None, address="0xwallet", include_zero=True)
