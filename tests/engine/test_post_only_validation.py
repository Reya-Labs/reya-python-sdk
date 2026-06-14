"""
post-only + IOC validation — the case is split between the SDK and the API.

The combination is self-contradictory (IOC is taker-only; post_only requires
the order to rest), so the SDK refuses to even build the payload (client
guard, pinned here against the live client). The raw half drives a generated
CreateOrderRequest with a REAL signature over the exact wire values
(post_only=True, IOC) — mirroring tests/spot/test_api_validation.py —
to prove the server independently rejects it with INPUT_VALIDATION_ERROR.
Both probes price at the safe no-match buy price so even a validation
regression could not move the book (an IOC buy at $10 cancels unfilled).
"""

import time
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, TimeInForceInt
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.post_only, pytest.mark.validation]


@pytest.mark.spot
@pytest.mark.asyncio
async def test_post_only_ioc_rejected_by_client_guard(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """The SDK raises locally on post_only + IOC — nothing reaches the wire."""
    params = (
        OrderBuilder.from_config(spot_config)
        .buy()
        .price(str(spot_config.get_safe_no_match_buy_price()))
        .ioc()
        .post_only()
        .build()
    )

    with pytest.raises(ValueError, match="post_only is not supported on IOC orders"):
        await spot_tester.client.create_limit_order(params)
    logger.info("✅ SDK client guard refused post_only + IOC locally")

    await spot_tester.check.no_open_orders()


@pytest.mark.spot
@pytest.mark.asyncio
async def test_post_only_ioc_raw_rejected_by_api(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """Bypassing the client guard with a raw CreateOrderRequest (real
    signature over the exact post_only=True + IOC wire values) is rejected by
    the API with INPUT_VALIDATION_ERROR."""
    buy_px = str(spot_config.get_safe_no_match_buy_price())
    nonce = spot_tester.get_next_nonce()
    deadline = int(time.time()) + 60
    signature = spot_tester.client.signature_generator.sign_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        order_type=int(OrderTypeInt.LIMIT),
        is_buy=True,
        qty=Decimal(spot_config.min_qty),
        limit_price=Decimal(buy_px),
        trigger_price=Decimal(0),
        time_in_force=int(TimeInForceInt.IOC),
        client_order_id=0,
        reduce_only=False,
        expires_after=0,
        nonce=nonce,
        deadline=deadline,
        post_only=True,
    )
    request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=buy_px,
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.IOC,
        reduceOnly=None,
        postOnly=True,
        expiresAfter=0,
        signature=signature,
        nonce=str(nonce),
        signerWallet=spot_tester.client.signer_wallet_address,
        deadline=deadline,
    )

    with pytest.raises(ApiException) as exc_info:
        await spot_tester.client.orders.create_order(create_order_request=request)
    error_msg = str(exc_info.value)
    assert "INPUT_VALIDATION_ERROR" in error_msg, f"Expected INPUT_VALIDATION_ERROR, got: {error_msg[:200]}"
    logger.info("✅ Raw post_only + IOC rejected by the API with INPUT_VALIDATION_ERROR")

    await spot_tester.check.no_open_orders()
