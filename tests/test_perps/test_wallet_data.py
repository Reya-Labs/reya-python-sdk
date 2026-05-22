#!/usr/bin/env python3
"""Tests for wallet-related perp API endpoints (positions, perp executions, accounts, balances).

Tests that need a position to form use ``perp_maker_tester`` + ``perp_taker_tester``
so the IOC has a counterparty under perpOB (no AMM = no fill without a maker).
Read-only tests (accounts, balances, configuration) keep using ``reya_tester``.
"""

import pytest

from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

PERP_SYMBOL = "ETHRUSDPERP"
PERP_QTY = "0.01"


async def _rest_maker_sell(maker: ReyaTester, market_price: float, qty: str = PERP_QTY) -> str:
    """Place a maker sell at 1% below oracle so a taker BUY @ 1.05x oracle crosses it."""
    price = str(round(market_price * 0.99, 2))
    order_id = await maker.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=False,
            limit_px=price,
            qty=qty,
            time_in_force=TimeInForce.GTC,
        )
    )
    assert order_id is not None
    await maker.wait.for_order_creation(order_id=order_id)
    return order_id


@pytest.mark.asyncio
async def test_get_wallet_positions_empty(reya_tester: ReyaTester):
    """Test getting positions when no positions exist for the symbol"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check.no_open_orders()
    await reya_tester.check.position_not_open(symbol)

    positions = await reya_tester.data.positions()
    assert isinstance(positions, dict), "Positions should be a dictionary"
    assert symbol not in positions, f"Should not have position for {symbol}"

    logger.info("✅ Wallet positions (empty) test completed successfully")


@pytest.mark.asyncio
async def test_get_wallet_positions_with_position(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """Position formation is verified via wallet positions endpoint.

    Maker rests a GTC sell, taker crosses with IOC buy. Asserts the taker
    wallet now reports a long ETHRUSDPERP position.
    """
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)
    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_sell(perp_maker_tester, market_price)

    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    positions = await perp_taker_tester.data.positions()
    assert isinstance(positions, dict), "Positions should be a dictionary"
    assert PERP_SYMBOL in positions, f"Should have position for {PERP_SYMBOL}"

    position = positions[PERP_SYMBOL]
    assert position.symbol == PERP_SYMBOL, "Position symbol should match"
    assert position.account_id == perp_taker_tester.account_id, "Position account ID should match"
    assert position.exchange_id == REYA_DEX_ID, "Position exchange ID should match"
    assert float(position.qty) == float(PERP_QTY), "Position qty should match"
    assert position.side == Side.B, "Position side should be BUY"

    logger.info("✅ Wallet positions (with position) test completed successfully")


@pytest.mark.asyncio
async def test_get_wallet_perp_executions(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """Latest wallet perp execution reflects the most recent fill.

    Maker rests a GTC sell, taker crosses with IOC buy. Asserts the taker
    wallet's latest execution matches the fill.
    """
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)

    last_execution_before = await perp_taker_tester.get_last_wallet_perp_execution()
    sequence_before = last_execution_before.sequence_number if last_execution_before else 0

    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_sell(perp_maker_tester, market_price)

    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    last_execution_after = await perp_taker_tester.get_last_wallet_perp_execution()
    assert last_execution_after is not None, "Should have execution after order"
    assert last_execution_after.sequence_number > sequence_before, "Sequence number should increase"
    assert last_execution_after.symbol == PERP_SYMBOL, "Execution symbol should match"
    # PerpExecution uses takerAccountId / makerAccountId — the wallet whose
    # endpoint we queried is the taker, since the taker placed the IOC.
    assert (
        last_execution_after.taker_account_id == perp_taker_tester.account_id
    ), "Execution taker account ID should match the taker tester"
    assert (
        last_execution_after.maker_account_id == perp_maker_tester.account_id
    ), "Execution maker account ID should match the maker tester"
    assert last_execution_after.exchange_id == REYA_DEX_ID, "Execution exchange ID should match"
    assert float(last_execution_after.qty) == float(PERP_QTY), "Execution qty should match"
    assert last_execution_after.side == Side.B, "Execution side should be BUY"

    logger.info("✅ Wallet perp executions test completed successfully")


@pytest.mark.asyncio
async def test_get_wallet_accounts(reya_tester: ReyaTester):
    """Test getting wallet accounts"""
    assert reya_tester.owner_wallet_address is not None, "Owner wallet address required"

    accounts = await reya_tester.client.wallet.get_wallet_accounts(address=reya_tester.owner_wallet_address)

    assert accounts is not None, "Should have accounts"
    assert len(accounts) > 0, "Should have at least one account"

    account_ids = [acc.account_id for acc in accounts]
    assert reya_tester.account_id in account_ids, "Test account should be in accounts list"

    test_account = next(acc for acc in accounts if acc.account_id == reya_tester.account_id)
    assert test_account.name is not None, "Account should have a name"
    assert test_account.type is not None, "Account should have a type"

    logger.info(f"✅ Wallet accounts test completed - found {len(accounts)} account(s)")


@pytest.mark.asyncio
async def test_get_wallet_account_balances(reya_tester: ReyaTester):
    """Test getting wallet account balances"""
    balances = await reya_tester.data.balances()

    assert isinstance(balances, dict), "Balances should be a dictionary"
    assert "RUSD" in balances or len(balances) > 0, "Should have at least one balance"

    for asset, balance in balances.items():
        assert balance.account_id == reya_tester.account_id, f"Balance account ID should match for {asset}"
        assert balance.asset == asset, f"Balance asset should match for {asset}"
        assert balance.real_balance is not None, f"Balance should have real_balance for {asset}"

    logger.info(f"✅ Wallet account balances test completed - found {len(balances)} balance(s)")


@pytest.mark.asyncio
async def test_get_wallet_configuration(reya_tester: ReyaTester):
    """Test getting wallet configuration (fee tier, OG status, affiliate status)"""
    assert reya_tester.owner_wallet_address is not None, "Owner wallet address required"

    config = await reya_tester.client.wallet.get_wallet_configuration(address=reya_tester.owner_wallet_address)

    assert config is not None, "Should have wallet configuration"
    assert config.fee_tier_id is not None, "Should have fee tier ID"
    assert config.fee_tier_id >= 0, "Fee tier ID should be non-negative"
    assert config.og_status is not None, "Should have OG status"
    assert config.affiliate_status is not None, "Should have affiliate status"
    assert config.referee_status is not None, "Should have referee status"

    logger.info(f"✅ Wallet configuration test completed - fee tier: {config.fee_tier_id}")


@pytest.mark.asyncio
async def test_get_single_position(perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester):
    """Single-position lookup by symbol returns the freshly-formed position.

    Maker rests a GTC sell, taker crosses with IOC buy. Asserts the
    single-position lookup on the taker reflects the fill.
    """
    await perp_taker_tester.check.position_not_open(PERP_SYMBOL)

    initial = await perp_taker_tester.data.position(PERP_SYMBOL)
    assert initial is None, "Should not have position initially"

    market_price = float(await perp_taker_tester.data.current_price(PERP_SYMBOL))

    await _rest_maker_sell(perp_maker_tester, market_price)

    await perp_taker_tester.orders.create_limit(
        LimitOrderParameters(
            symbol=PERP_SYMBOL,
            is_buy=True,
            limit_px=str(round(market_price * 1.05, 2)),
            qty=PERP_QTY,
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
    )

    position = await perp_taker_tester.data.position(PERP_SYMBOL)
    assert position is not None, "Should have position after order"
    assert position.symbol == PERP_SYMBOL, "Position symbol should match"
    assert float(position.qty) == float(PERP_QTY), "Position qty should match"

    logger.info("✅ Get single position test completed successfully")
