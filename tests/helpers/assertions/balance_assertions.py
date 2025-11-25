"""Balance-related assertions for test verification."""

from typing import Optional
import logging

import pytest

from sdk.open_api.models.account_balance import AccountBalance

from tests.helpers.clients.rest_client import RestClient
from tests.helpers.clients.websocket_client import WebSocketClient


logger = logging.getLogger("reya.test.assertions.balance")


async def assert_balance(
    rest_client: RestClient,
    asset: str,
    expected_account_id: Optional[int] = None,
    expected_min_balance: Optional[str] = None,
    expected_max_balance: Optional[str] = None,
    account_id: Optional[int] = None,
) -> AccountBalance:
    """
    Assert that a balance exists with expected properties.
    
    Args:
        rest_client: REST client for verification
        asset: Asset symbol to check
        expected_account_id: Expected account ID
        expected_min_balance: Minimum expected balance
        expected_max_balance: Maximum expected balance
        account_id: Account ID to filter by (uses default if not specified)
    
    Returns:
        The verified AccountBalance
    """
    balance = await rest_client.get_balance(asset, account_id)
    
    assert balance is not None, f"Balance not found for asset {asset}"
    
    if expected_account_id is not None:
        assert balance.account_id == expected_account_id, (
            f"Account ID mismatch. Expected: {expected_account_id}, Got: {balance.account_id}"
        )
    
    if expected_min_balance is not None:
        assert float(balance.real_balance) >= float(expected_min_balance), (
            f"Balance {balance.real_balance} should be >= {expected_min_balance}"
        )
    
    if expected_max_balance is not None:
        assert float(balance.real_balance) <= float(expected_max_balance), (
            f"Balance {balance.real_balance} should be <= {expected_max_balance}"
        )
    
    logger.info(f"✅ Balance verified: {asset} = {balance.real_balance}")
    return balance


def assert_balance_change(
    initial_balance: AccountBalance,
    final_balance: AccountBalance,
    expected_change: float,
    tolerance: float = 0.001,
) -> None:
    """
    Assert that a balance changed by the expected amount.
    
    Args:
        initial_balance: Balance before operation
        final_balance: Balance after operation
        expected_change: Expected change (positive for increase, negative for decrease)
        tolerance: Relative tolerance for comparison (default 0.1%)
    """
    initial = float(initial_balance.real_balance)
    final = float(final_balance.real_balance)
    actual_change = final - initial
    
    assert actual_change == pytest.approx(expected_change, rel=tolerance), (
        f"Balance change mismatch. Expected: {expected_change}, Got: {actual_change}"
    )
    
    logger.info(
        f"✅ Balance change verified: {initial_balance.asset} "
        f"{initial:.6f} → {final:.6f} (change: {actual_change:.6f})"
    )


def assert_spot_trade_balance_changes(
    maker_initial_balances: dict[str, AccountBalance],
    maker_final_balances: dict[str, AccountBalance],
    taker_initial_balances: dict[str, AccountBalance],
    taker_final_balances: dict[str, AccountBalance],
    base_asset: str,
    quote_asset: str,
    qty: str,
    price: str,
    is_maker_buyer: bool,
    tolerance: float = 0.001,
) -> None:
    """
    Assert that balance changes for a spot trade match expected amounts.
    
    This verifies that:
    - Maker's base/quote balances changed correctly
    - Taker's base/quote balances changed correctly
    - Changes are consistent with the trade direction
    
    Args:
        maker_initial_balances: Maker balances before trade (keyed by asset)
        maker_final_balances: Maker balances after trade
        taker_initial_balances: Taker balances before trade
        taker_final_balances: Taker balances after trade
        base_asset: Base asset symbol (e.g., "ETH")
        quote_asset: Quote asset symbol (e.g., "RUSD")
        qty: Trade quantity as string
        price: Trade price as string
        is_maker_buyer: True if maker is buying (taker is selling)
        tolerance: Relative tolerance for comparisons (default 0.1%)
    """
    qty_float = float(qty)
    price_float = float(price)
    notional = qty_float * price_float
    
    logger.info("\n💰 Verifying spot trade balance changes...")
    logger.info(f"Trade: qty={qty} {base_asset} at price={price} {quote_asset}")
    logger.info(f"Notional: {notional} {quote_asset}")
    logger.info(f"Maker is {'BUYER' if is_maker_buyer else 'SELLER'}")
    
    # Get balances
    maker_initial_base = maker_initial_balances.get(base_asset)
    maker_final_base = maker_final_balances.get(base_asset)
    maker_initial_quote = maker_initial_balances.get(quote_asset)
    maker_final_quote = maker_final_balances.get(quote_asset)
    
    taker_initial_base = taker_initial_balances.get(base_asset)
    taker_final_base = taker_final_balances.get(base_asset)
    taker_initial_quote = taker_initial_balances.get(quote_asset)
    taker_final_quote = taker_final_balances.get(quote_asset)
    
    # Log balances
    logger.info(f"Maker balances - initial: base={maker_initial_base}, quote={maker_initial_quote}")
    logger.info(f"Maker balances - final: base={maker_final_base}, quote={maker_final_quote}")
    logger.info(f"Taker balances - initial: base={taker_initial_base}, quote={taker_initial_quote}")
    logger.info(f"Taker balances - final: base={taker_final_base}, quote={taker_final_quote}")
    
    # Verify all balances exist
    assert maker_initial_base and maker_final_base, f"Maker {base_asset} balance not found"
    assert maker_initial_quote and maker_final_quote, f"Maker {quote_asset} balance not found"
    assert taker_initial_base and taker_final_base, f"Taker {base_asset} balance not found"
    assert taker_initial_quote and taker_final_quote, f"Taker {quote_asset} balance not found"
    
    # Calculate balance changes
    maker_base_change = float(maker_final_base.real_balance) - float(maker_initial_base.real_balance)
    maker_quote_change = float(maker_final_quote.real_balance) - float(maker_initial_quote.real_balance)
    taker_base_change = float(taker_final_base.real_balance) - float(taker_initial_base.real_balance)
    taker_quote_change = float(taker_final_quote.real_balance) - float(taker_initial_quote.real_balance)
    
    # Calculate expected changes
    if is_maker_buyer:
        # Maker buys: base increases, quote decreases
        # Taker sells: base decreases, quote increases
        expected_maker_base_change = qty_float
        expected_maker_quote_change = -notional
        expected_taker_base_change = -qty_float
        expected_taker_quote_change = notional
    else:
        # Maker sells: base decreases, quote increases
        # Taker buys: base increases, quote decreases
        expected_maker_base_change = -qty_float
        expected_maker_quote_change = notional
        expected_taker_base_change = qty_float
        expected_taker_quote_change = -notional
    
    # Log expected vs actual
    logger.info(f"Maker {base_asset} change: {maker_base_change:.6f} (expected: {expected_maker_base_change:.6f})")
    logger.info(f"Maker {quote_asset} change: {maker_quote_change:.6f} (expected: {expected_maker_quote_change:.6f})")
    logger.info(f"Taker {base_asset} change: {taker_base_change:.6f} (expected: {expected_taker_base_change:.6f})")
    logger.info(f"Taker {quote_asset} change: {taker_quote_change:.6f} (expected: {expected_taker_quote_change:.6f})")
    
    # Verify maker base change
    assert abs(maker_base_change - expected_maker_base_change) <= abs(expected_maker_base_change * tolerance), (
        f"Maker {base_asset} change {maker_base_change:.6f} does not match expected {expected_maker_base_change:.6f}"
    )
    
    # Verify maker quote change
    assert abs(maker_quote_change - expected_maker_quote_change) <= abs(expected_maker_quote_change * tolerance), (
        f"Maker {quote_asset} change {maker_quote_change:.6f} does not match expected {expected_maker_quote_change:.6f}"
    )
    
    # Verify taker base change
    assert abs(taker_base_change - expected_taker_base_change) <= abs(expected_taker_base_change * tolerance), (
        f"Taker {base_asset} change {taker_base_change:.6f} does not match expected {expected_taker_base_change:.6f}"
    )
    
    # Verify taker quote change
    assert abs(taker_quote_change - expected_taker_quote_change) <= abs(expected_taker_quote_change * tolerance), (
        f"Taker {quote_asset} change {taker_quote_change:.6f} does not match expected {expected_taker_quote_change:.6f}"
    )
    
    logger.info("✅ All balance changes verified successfully!")
