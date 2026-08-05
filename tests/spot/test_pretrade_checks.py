"""Matching-engine spot balance admission checks — live e2e."""

from decimal import ROUND_FLOOR, Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models import OrderStatus, RequestError, RequestErrorCode
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.helpers.reya_tester import logger
from tests.helpers.spot_prerequisites import spot_prerequisite_missing


async def _balance(tester: ReyaTester, asset: str) -> Decimal:
    balance = await tester.data.balance(asset)
    if balance is None:
        spot_prerequisite_missing(f"Account {tester.account_id} has no {asset} balance")
    assert balance is not None
    return Decimal(balance.real_balance)


def _first_step_above(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step + step


def _assert_insufficient_balance(error: ApiException) -> None:
    assert isinstance(error.data, RequestError)
    assert error.data.error == RequestErrorCode.INSUFFICIENT_BALANCE_ERROR
    assert "insufficient spot balance" in error.data.message.lower()
    assert "shortfall" in error.data.message.lower()


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_ioc_insufficient_balance_buy(
    spot_config: SpotTestConfig,
    maker_tester: ReyaTester,
    taker_tester: ReyaTester,
) -> None:
    """A crossing buy beyond the taker's quote balance is rejected by risk."""
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    price = spot_config.circuit_breaker_ceiling
    qty_step = Decimal(spot_config.qty_step_size)
    buyer = taker_tester
    seller = maker_tester
    qty = _first_step_above(await _balance(buyer, "RUSD") / price, qty_step)
    qty_str = format(qty, "f")

    maker_order_id = await seller.orders.create_limit(
        OrderBuilder().symbol(spot_config.symbol).sell().price(str(price)).qty(qty_str).gtc().build()
    )
    await seller.wait.for_order_creation(maker_order_id)

    try:
        with pytest.raises(ApiException) as exc_info:
            await buyer.orders.create_limit(
                OrderBuilder().symbol(spot_config.symbol).buy().price(str(price)).qty(qty_str).ioc().build()
            )
        _assert_insufficient_balance(exc_info.value)
    finally:
        await seller.client.cancel_order(
            order_id=maker_order_id,
            symbol=spot_config.symbol,
            account_id=seller.account_id,
        )
        await seller.wait.for_order_state(maker_order_id, OrderStatus.CANCELLED)

    await buyer.check.no_open_orders()
    await seller.check.no_open_orders()
    logger.info("✅ crossing spot buy rejected for insufficient quote balance")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_ioc_insufficient_balance_sell(
    spot_config: SpotTestConfig,
    maker_tester: ReyaTester,
    taker_tester: ReyaTester,
) -> None:
    """A crossing sell beyond the taker's base balance is rejected by risk."""
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    price = spot_config.circuit_breaker_floor
    qty_step = Decimal(spot_config.qty_step_size)
    seller = taker_tester
    buyer = maker_tester
    qty = _first_step_above(await _balance(seller, spot_config.base_asset), qty_step)
    qty_str = format(qty, "f")

    maker_order_id = await buyer.orders.create_limit(
        OrderBuilder().symbol(spot_config.symbol).buy().price(str(price)).qty(qty_str).gtc().build()
    )
    await buyer.wait.for_order_creation(maker_order_id)

    try:
        with pytest.raises(ApiException) as exc_info:
            await seller.orders.create_limit(
                OrderBuilder().symbol(spot_config.symbol).sell().price(str(price)).qty(qty_str).ioc().build()
            )
        _assert_insufficient_balance(exc_info.value)
    finally:
        await buyer.client.cancel_order(
            order_id=maker_order_id,
            symbol=spot_config.symbol,
            account_id=buyer.account_id,
        )
        await buyer.wait.for_order_state(maker_order_id, OrderStatus.CANCELLED)

    await buyer.check.no_open_orders()
    await seller.check.no_open_orders()
    logger.info("✅ crossing spot sell rejected for insufficient base balance")
