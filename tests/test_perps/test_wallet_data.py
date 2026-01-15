#!/usr/bin/env python3
"""Tests for wallet-related perp API endpoints (positions, perp executions, accounts, balances).

Note: WS+REST consistency verification is handled centrally by wait_for_order_execution()
in the waiters module, which checks both REST and WebSocket for executions and positions.
"""

import pytest

from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import limit_order_params_to_order, logger


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
async def test_get_wallet_positions_with_position(reya_tester: ReyaTester):
    """Test getting positions when a position exists"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check.no_open_orders()
    await reya_tester.check.position_not_open(symbol)

    market_price = await reya_tester.data.current_price()
    test_qty = "0.01"

    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=test_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.orders.create_limit(limit_order_params)

    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait.for_order_execution(expected_order)

    positions = await reya_tester.data.positions()
    assert isinstance(positions, dict), "Positions should be a dictionary"
    assert symbol in positions, f"Should have position for {symbol}"

    position = positions[symbol]
    assert position.symbol == symbol, "Position symbol should match"
    assert position.account_id == reya_tester.account_id, "Position account ID should match"
    assert position.exchange_id == REYA_DEX_ID, "Position exchange ID should match"
    assert float(position.qty) == float(test_qty), "Position qty should match"
    assert position.side == Side.B, "Position side should be BUY"

    logger.info("✅ Wallet positions (with position) test completed successfully")


@pytest.mark.asyncio
async def test_get_wallet_perp_executions(reya_tester: ReyaTester):
    """Test getting perp execution history for wallet"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check.no_open_orders()
    await reya_tester.check.position_not_open(symbol)

    last_execution_before = await reya_tester.get_last_wallet_perp_execution()
    sequence_before = last_execution_before.sequence_number if last_execution_before else 0

    market_price = await reya_tester.data.current_price()
    test_qty = "0.01"

    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=test_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.orders.create_limit(limit_order_params)

    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait.for_order_execution(expected_order)

    last_execution_after = await reya_tester.get_last_wallet_perp_execution()
    assert last_execution_after is not None, "Should have execution after order"
    assert last_execution_after.sequence_number > sequence_before, "Sequence number should increase"
    assert last_execution_after.symbol == symbol, "Execution symbol should match"
    assert last_execution_after.account_id == reya_tester.account_id, "Execution account ID should match"
    assert last_execution_after.exchange_id == REYA_DEX_ID, "Execution exchange ID should match"
    assert float(last_execution_after.qty) == float(test_qty), "Execution qty should match"
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
async def test_get_single_position(reya_tester: ReyaTester):
    """Test getting a single position by symbol"""
    symbol = "ETHRUSDPERP"

    await reya_tester.check.no_open_orders()
    await reya_tester.check.position_not_open(symbol)

    position = await reya_tester.data.position(symbol)
    assert position is None, "Should not have position initially"

    market_price = await reya_tester.data.current_price()
    test_qty = "0.01"

    limit_order_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=str(float(market_price) * 1.1),
        qty=test_qty,
        time_in_force=TimeInForce.IOC,
        reduce_only=False,
    )

    await reya_tester.orders.create_limit(limit_order_params)

    expected_order = limit_order_params_to_order(limit_order_params, reya_tester.account_id)
    await reya_tester.wait.for_order_execution(expected_order)

    position = await reya_tester.data.position(symbol)
    assert position is not None, "Should have position after order"
    assert position.symbol == symbol, "Position symbol should match"
    assert float(position.qty) == float(test_qty), "Position qty should match"

    logger.info("✅ Get single position test completed successfully")
