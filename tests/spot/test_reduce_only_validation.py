"""
Server-side rejection of `reduceOnly` on spot orders — live e2e, raw request.

Split out of test_api_validation.py (that module sits at pylint's module-size
cap); same raw-request conventions.

The SDK's local guard is pinned offline (tests/parity), but it raises before
anything reaches the wire — a raw API user can still submit the field, so the
server-side rule needs its own pin. Per the locked PRO-133/PRO-208 model,
reduce-only is perp-IOC-only; on spot an EXPLICIT field (even false) is
rejected with INPUT_VALIDATION_ERROR.
"""

import time
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from tests.helpers import ReyaTester
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.reya_tester import logger


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_reduce_only_rejected(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that the SERVER rejects an explicit reduceOnly field on a spot order.

    The request is correctly SIGNED (reduce_only=True is part of the 14-field
    digest) so the validator's market-type rule, not signature recovery, is
    what rejects. The order is priced far from the touch so a validation
    regression cannot produce a fill.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER REDUCE-ONLY REJECTED TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    order_price = spot_config.price(0.96)
    deadline = int(time.time()) + 60
    nonce = spot_tester.get_next_nonce()

    signature = spot_tester.client.signature_generator.sign_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        order_type=0,  # LIMIT
        is_buy=True,
        qty=Decimal(spot_config.min_qty),
        limit_price=Decimal(str(order_price)),
        trigger_price=Decimal(0),
        time_in_force=0,  # GTC
        client_order_id=0,
        reduce_only=True,
        expires_after=0,
        nonce=nonce,
        deadline=deadline,
    )

    order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        deadline=deadline,
        expiresAfter=0,
        reduceOnly=True,
        signature=signature,
        nonce=str(nonce),
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info("Sending correctly-signed spot order with reduceOnly=true...")
    try:
        response = await spot_tester.client.orders.create_order(create_order_request=order_request)
        # Defensive cleanup if the validation regressed and the order rested.
        if response.order_id is not None:
            await spot_tester.client.cancel_order(
                order_id=response.order_id, symbol=spot_config.symbol, account_id=spot_tester.account_id
            )
        pytest.fail(f"reduceOnly on a spot order should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        assert "INPUT_VALIDATION_ERROR" in error_msg, f"Expected INPUT_VALIDATION_ERROR, got: {error_msg[:200]}"
        assert (
            "reduceOnly field is not supported for spot markets" in error_msg
        ), f"Expected the spot reduceOnly message, got: {error_msg[:200]}"
        logger.info(f"✅ reduceOnly on spot rejected by the server: {error_msg[:120]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER REDUCE-ONLY REJECTED TEST COMPLETED")
