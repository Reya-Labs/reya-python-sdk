"""Position-related assertions for test verification."""

from typing import Optional
import logging

import pytest

from sdk.open_api.models.position import Position
from sdk.open_api.models.side import Side

from tests.helpers.clients.rest_client import RestClient
from tests.helpers.clients.websocket_client import WebSocketClient


logger = logging.getLogger("reya.test.assertions.position")


async def assert_position(
    rest_client: RestClient,
    symbol: str,
    expected_account_id: Optional[int] = None,
    expected_exchange_id: Optional[int] = None,
    expected_qty: Optional[str] = None,
    expected_side: Optional[Side] = None,
    expected_avg_entry_price: Optional[str] = None,
    expected_last_trade_seq: Optional[int] = None,
) -> Position:
    """
    Assert that a position exists with expected properties.
    
    Args:
        rest_client: REST client for verification
        symbol: Symbol to check
        expected_account_id: Expected account ID
        expected_exchange_id: Expected exchange ID
        expected_qty: Expected quantity
        expected_side: Expected side (B for long, A for short)
        expected_avg_entry_price: Expected average entry price
        expected_last_trade_seq: Expected last trade sequence number
    
    Returns:
        The verified Position
    """
    position = await rest_client.get_position(symbol)
    
    assert position is not None, f"Position not found for {symbol}"
    
    if expected_account_id is not None:
        assert position.account_id == expected_account_id, (
            f"Account ID mismatch. Expected: {expected_account_id}, Got: {position.account_id}"
        )
    
    if expected_exchange_id is not None:
        assert position.exchange_id == expected_exchange_id, (
            f"Exchange ID mismatch. Expected: {expected_exchange_id}, Got: {position.exchange_id}"
        )
    
    if expected_qty is not None:
        assert position.qty == expected_qty, (
            f"Qty mismatch. Expected: {expected_qty}, Got: {position.qty}"
        )
    
    if expected_side is not None:
        assert position.side == expected_side, (
            f"Side mismatch. Expected: {expected_side}, Got: {position.side}"
        )
    
    if expected_avg_entry_price is not None:
        assert float(position.avg_entry_price) == pytest.approx(
            float(expected_avg_entry_price), rel=1e-6
        ), (
            f"Avg entry price mismatch. Expected: {expected_avg_entry_price}, "
            f"Got: {position.avg_entry_price}"
        )
    
    if expected_last_trade_seq is not None:
        assert position.last_trade_sequence_number == expected_last_trade_seq, (
            f"Last trade seq mismatch. Expected: {expected_last_trade_seq}, "
            f"Got: {position.last_trade_sequence_number}"
        )
    
    logger.info(f"✅ Position verified: {symbol} {position.side} {position.qty}")
    return position


async def assert_position_closed(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    symbol: str,
) -> None:
    """
    Assert that a position is closed (does not exist).
    
    Args:
        rest_client: REST client for verification
        ws_client: WebSocket client for verification
        symbol: Symbol to check
    """
    # Check REST
    position = await rest_client.get_position(symbol)
    assert position is None, f"Position still exists via REST: {position}"
    
    # Check WebSocket
    ws_position = ws_client.get_position(symbol)
    assert ws_position is None or ws_position.qty == "0", (
        f"Position still exists via WS: {ws_position}"
    )
    
    logger.info(f"✅ Position closed: {symbol}")


def assert_position_changes(
    initial_position: Optional[Position],
    final_position: Position,
    expected_qty_change: str,
    expected_side: Side,
    expected_avg_entry_price: Optional[str] = None,
) -> None:
    """
    Assert that position changed as expected after a trade.
    
    This is useful for verifying position updates after order execution.
    
    Args:
        initial_position: Position before trade (None if no position)
        final_position: Position after trade
        expected_qty_change: Expected change in quantity (positive)
        expected_side: Expected final side
        expected_avg_entry_price: Expected average entry price (optional)
    """
    initial_qty = float(initial_position.qty) if initial_position else 0.0
    final_qty = float(final_position.qty)
    qty_change = float(expected_qty_change)
    
    # Verify side
    assert final_position.side == expected_side, (
        f"Side mismatch. Expected: {expected_side}, Got: {final_position.side}"
    )
    
    # Verify quantity change
    if initial_position is None or initial_position.side != expected_side:
        # New position or flipped position
        assert final_qty == pytest.approx(qty_change, rel=1e-6), (
            f"Qty mismatch for new position. Expected: {qty_change}, Got: {final_qty}"
        )
    else:
        # Added to existing position
        expected_final_qty = initial_qty + qty_change
        assert final_qty == pytest.approx(expected_final_qty, rel=1e-6), (
            f"Qty mismatch. Expected: {expected_final_qty}, Got: {final_qty}"
        )
    
    # Verify average entry price if provided
    if expected_avg_entry_price is not None:
        assert float(final_position.avg_entry_price) == pytest.approx(
            float(expected_avg_entry_price), rel=1e-6
        ), (
            f"Avg entry price mismatch. Expected: {expected_avg_entry_price}, "
            f"Got: {final_position.avg_entry_price}"
        )
    
    logger.info(
        f"✅ Position change verified: {final_position.side} {final_qty} "
        f"(change: {qty_change})"
    )
