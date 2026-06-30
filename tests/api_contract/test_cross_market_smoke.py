"""
Cross-market smoke for the raw envelope validation path.

The full raw-validation matrix in test_api_validation.py is pinned to the
SPOT market because signature recovery / nonce tracking / deadline checks run
BEFORE market-specific dispatch — parametrizing all ~24 of them over both
markets would double live cost for near-zero marginal coverage. This smoke
pair proves the equivalence claim on the PERP market: one signature-recovery
rejection and one nonce-replay rejection, signed against the perp marketId.
"""

import time
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.perp, pytest.mark.validation]

PERP_SYMBOL = "ETHRUSDPERP"


def _perp_order_request(tester: ReyaTester, limit_px: str, qty: str, signature: str, nonce: int, deadline: int):
    return CreateOrderRequest(
        accountId=tester.account_id,
        symbol=PERP_SYMBOL,
        exchangeId=tester.client.config.dex_id,
        isBuy=True,
        limitPx=limit_px,
        qty=qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        deadline=deadline,
        expiresAfter=0,
        reduceOnly=None,
        signature=signature,
        nonce=str(nonce),
        signerWallet=tester.client.signer_wallet_address,
    )


@pytest.mark.asyncio
async def test_perp_order_invalid_signature_rejected(perp_maker_tester: ReyaTester):
    """A tampered signature over a perp-market order is rejected exactly like
    the spot equivalent (test_api_validation.py::test_spot_order_invalid_signature)."""
    market_def = await perp_maker_tester.get_market_definition(PERP_SYMBOL)
    oracle_price = float(await perp_maker_tester.data.current_price(PERP_SYMBOL))
    limit_px = str(round(oracle_price * 0.5, 2))

    request = _perp_order_request(
        perp_maker_tester,
        limit_px=limit_px,
        qty=str(market_def.min_order_qty),
        signature="0x" + "ab" * 65,
        nonce=perp_maker_tester.get_next_nonce(),
        deadline=int(time.time()) + 60,
    )

    with pytest.raises(ApiException) as exc_info:
        await perp_maker_tester.client.orders.create_order(create_order_request=request)
    error_msg = str(exc_info.value)
    assert "UNAUTHORIZED_SIGNATURE_ERROR" in error_msg, f"Expected UNAUTHORIZED_SIGNATURE_ERROR, got: {error_msg[:200]}"
    assert "Invalid signature" in error_msg, f"Expected 'Invalid signature', got: {error_msg[:200]}"
    logger.info("✅ Perp-market invalid signature rejected like spot")


@pytest.mark.asyncio
async def test_perp_order_reused_nonce_rejected(perp_maker_tester: ReyaTester):
    """Replaying a consumed nonce on a perp-market order is rejected exactly
    like the spot equivalent (test_api_validation.py::test_spot_order_reused_nonce)."""
    market_def = await perp_maker_tester.get_market_definition(PERP_SYMBOL)
    min_qty = str(market_def.min_order_qty)
    oracle_price = float(await perp_maker_tester.data.current_price(PERP_SYMBOL))
    limit_px = str(round(oracle_price * 0.5, 2))
    market_id = perp_maker_tester.client.get_market_id_from_symbol(PERP_SYMBOL)

    nonce = perp_maker_tester.get_next_nonce()
    deadline = int(time.time()) + 60
    sig_gen = perp_maker_tester.client.signature_generator

    def sign(target_deadline: int) -> str:
        return sig_gen.sign_order(
            account_id=perp_maker_tester.account_id,
            market_id=market_id,
            exchange_id=perp_maker_tester.client.config.dex_id,
            order_type=0,  # LIMIT
            is_buy=True,
            qty=Decimal(min_qty),
            limit_price=Decimal(limit_px),
            trigger_price=Decimal(0),
            time_in_force=0,  # GTC
            client_order_id=0,
            reduce_only=False,
            expires_after=0,
            nonce=nonce,
            deadline=target_deadline,
        )

    first = _perp_order_request(perp_maker_tester, limit_px, min_qty, sign(deadline), nonce, deadline)
    response = await perp_maker_tester.client.orders.create_order(create_order_request=first)
    assert response.order_id is not None
    await perp_maker_tester.client.cancel_order(
        order_id=response.order_id, symbol=PERP_SYMBOL, account_id=perp_maker_tester.account_id
    )

    reused_deadline = int(time.time()) + 60
    replay = _perp_order_request(perp_maker_tester, limit_px, min_qty, sign(reused_deadline), nonce, reused_deadline)
    with pytest.raises(ApiException) as exc_info:
        await perp_maker_tester.client.orders.create_order(create_order_request=replay)
    error_msg = str(exc_info.value)
    assert "nonce" in error_msg.lower(), f"Expected a nonce rejection, got: {error_msg[:200]}"
    logger.info("✅ Perp-market nonce replay rejected like spot")
