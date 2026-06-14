"""
Post-only rejection tests parametrized over [spot, perp] — live e2e.

A post-only order whose entry WOULD cross the best opposite price is rejected
with POST_ONLY_WOULD_CROSS_ERROR — the touch counts as a cross — with NO order
created (openOrders unchanged) and the book left untouched. postOnly is a
LIMIT-order flag: a TP/SL trigger carrying it is rejected at request
validation (perp-only, since triggers exist only on perp).

Controlled-counterparty pattern: account B (`taker`) rests a known maker on
the opposite side FIRST, then account A (`maker`) probes with the post-only
order; after the rejection A's openOrders snapshot is unchanged and B's maker
still rests at its original px/qty. All engineered probes need an empty book
(external liquidity would shift the best opposite price).
"""

import time
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.order_type import OrderType
from sdk.reya_rest_api.auth.signatures import OrderTypeInt, TimeInForceInt
from tests.engine.post_only_helpers import (
    PERP_SYMBOL,
    open_order_ids,
    perp_min_qty_and_oracle,
    rest_gtc_at_price,
)
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.liquidity_detector import skip_if_external_config_liquidity
from tests.helpers.market_config import PerpTestConfig, SpotTestConfig
from tests.helpers.order_lifecycle import assert_px_qty
from tests.helpers.reya_tester import logger

pytestmark = [pytest.mark.e2e, pytest.mark.post_only]

REJECTION_REASON = "Engineered would-cross probes need a controlled (empty) book."


async def _assert_probe_rejected_book_untouched(
    prober: ReyaTester,
    counterparty_tester: ReyaTester,
    market_config: SpotTestConfig | PerpTestConfig,
    market_type: str,
    counterparty_order_id: str,
    counterparty_px: str,
    probe_px: str,
) -> None:
    """Submit account A's post-only buy at `probe_px` against B's resting ask;
    assert POST_ONLY_WOULD_CROSS_ERROR, A's openOrders unchanged, B's maker
    untouched."""
    before_ids = await open_order_ids(prober)

    params = (
        OrderBuilder()
        .symbol(market_config.symbol)
        .buy()
        .price(probe_px)
        .qty(market_config.min_qty)
        .post_only()
        .gtc()
        .build()
    )
    with pytest.raises(ApiException) as exc_info:
        await prober.client.create_limit_order(params)
    error_msg = str(exc_info.value)
    assert (
        "POST_ONLY_WOULD_CROSS_ERROR" in error_msg
    ), f"[{market_type}] expected POST_ONLY_WOULD_CROSS_ERROR, got: {error_msg[:200]}"
    logger.info(f"[{market_type}] ✅ post-only buy @ {probe_px} against ask @ {counterparty_px} rejected")

    assert await open_order_ids(prober) == before_ids, "Rejected post-only must not create an order"
    untouched = await counterparty_tester.data.open_order(counterparty_order_id)
    assert untouched is not None, "Book must be untouched: the resting ask was consumed or cancelled"
    assert_px_qty(untouched, expected_px=counterparty_px, expected_qty=market_config.min_qty)
    logger.info(f"[{market_type}] ✅ openOrders unchanged for the prober; counterparty maker untouched")


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_post_only_would_cross_rejected(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """A post-only buy strictly ABOVE the best ask is rejected with
    POST_ONLY_WOULD_CROSS_ERROR; no order created, book untouched."""
    await skip_if_external_config_liquidity(market_config, maker, REJECTION_REASON)

    ask_px = str(market_config.price(1.01))
    counterparty = await rest_gtc_at_price(
        taker, market_config.symbol, price=ask_px, qty=market_config.min_qty, is_buy=False
    )

    try:
        await _assert_probe_rejected_book_untouched(
            prober=maker,
            counterparty_tester=taker,
            market_config=market_config,
            market_type=market_type,
            counterparty_order_id=counterparty.order_id,
            counterparty_px=ask_px,
            probe_px=str(market_config.price(1.02)),
        )
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_post_only_touch_price_rejected(
    market_config: SpotTestConfig | PerpTestConfig, market_type: str, maker: ReyaTester, taker: ReyaTester
) -> None:
    """A post-only buy at EXACTLY the best ask is rejected — the touch counts
    as a cross (the >=-vs-> boundary of the book's cross check)."""
    await skip_if_external_config_liquidity(market_config, maker, REJECTION_REASON)

    ask_px = str(market_config.price(1.01))
    counterparty = await rest_gtc_at_price(
        taker, market_config.symbol, price=ask_px, qty=market_config.min_qty, is_buy=False
    )

    try:
        await _assert_probe_rejected_book_untouched(
            prober=maker,
            counterparty_tester=taker,
            market_config=market_config,
            market_type=market_type,
            counterparty_order_id=counterparty.order_id,
            counterparty_px=ask_px,
            probe_px=ask_px,
        )
    finally:
        await maker.orders.close_all(fail_if_none=False)
        await taker.orders.close_all(fail_if_none=False)


@pytest.mark.perp
@pytest.mark.trigger
@pytest.mark.asyncio
async def test_post_only_on_trigger_order_rejected(perp_maker_tester: ReyaTester):
    """postOnly is a LIMIT-order flag — a TP trigger carrying postOnly=True is
    rejected and no order is created. Perp-only: trigger orders exist only on
    perp markets.

    Driven as a RAW CreateOrderRequest because TriggerOrderParameters has no
    post_only field (the typed path cannot express the invalid combination).
    The signature covers the EXACT wire values — post_only=True included, and
    the trigger envelope mirrors build_create_trigger_order_payload (GTC
    time-in-force, expiresAfter=0, no reduceOnly) — so the server's
    trigger/post-only validation, not signature recovery, is what rejects.
    """
    min_qty, oracle_price = await perp_min_qty_and_oracle(perp_maker_tester)
    trigger_px = str(round(oracle_price * 10, 2))
    limit_px = str(round(oracle_price * 9, 2))

    nonce = perp_maker_tester.get_next_nonce()
    deadline = int(time.time()) + 60
    signature = perp_maker_tester.client.signature_generator.sign_order(
        account_id=perp_maker_tester.account_id,
        market_id=perp_maker_tester.client.get_market_id_from_symbol(PERP_SYMBOL),
        exchange_id=perp_maker_tester.client.config.dex_id,
        order_type=int(OrderTypeInt.TAKE_PROFIT),
        is_buy=False,
        qty=Decimal(min_qty),
        limit_price=Decimal(limit_px),
        trigger_price=Decimal(trigger_px),
        time_in_force=int(TimeInForceInt.GTC),
        client_order_id=0,
        reduce_only=False,
        expires_after=0,
        nonce=nonce,
        deadline=deadline,
        post_only=True,
    )
    request = CreateOrderRequest(
        accountId=perp_maker_tester.account_id,
        symbol=PERP_SYMBOL,
        exchangeId=perp_maker_tester.client.config.dex_id,
        isBuy=False,
        limitPx=limit_px,
        qty=min_qty,
        triggerPx=trigger_px,
        orderType=OrderType.TAKE_PROFIT,
        reduceOnly=None,
        postOnly=True,
        expiresAfter=0,
        signature=signature,
        nonce=str(nonce),
        signerWallet=perp_maker_tester.client.signer_wallet_address,
        deadline=deadline,
    )

    try:
        response = await perp_maker_tester.client.orders.create_order(create_order_request=request)
        # Defensive cleanup if validation regressed and the trigger was accepted.
        if response.order_id is not None:
            await perp_maker_tester.client.cancel_order(
                order_id=response.order_id, symbol=PERP_SYMBOL, account_id=perp_maker_tester.account_id
            )
        pytest.fail(f"postOnly on a TP trigger should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # Request-shape validation pins this exact case: the off-chain order
        # validator pushes 'postOnly is not supported for TP/SL orders',
        # surfaced as INPUT_VALIDATION_ERROR. Pinning the message keeps an
        # unrelated rejection (rate limit, kill switch, balance) from
        # false-passing without exercising the postOnly/trigger rule.
        assert "INPUT_VALIDATION_ERROR" in error_msg, f"Expected INPUT_VALIDATION_ERROR, got: {error_msg[:200]}"
        assert (
            "postOnly is not supported for TP/SL orders" in error_msg
        ), f"Expected the TP/SL postOnly message, got: {error_msg[:200]}"
        logger.info(f"✅ postOnly on a TP trigger rejected: {error_msg[:120]}")

    await perp_maker_tester.check.no_open_orders()
