"""WebSocket state management for ReyaTester.

This module uses async_api types directly from the WebSocket SDK.
These types match the AsyncAPI spec and are auto-generated.
"""

from typing import TYPE_CHECKING, Optional

import json
import logging

# Import async_api types (auto-generated from AsyncAPI spec)
from sdk.async_api.account_balance import AccountBalance as AsyncAccountBalance
from sdk.async_api.account_balance_update_payload import AccountBalanceUpdatePayload
from sdk.async_api.depth import Depth
from sdk.async_api.market_depth_update_payload import MarketDepthUpdatePayload
from sdk.async_api.market_spot_execution_update_payload import MarketSpotExecutionUpdatePayload
from sdk.async_api.order import Order as AsyncOrder
from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.async_api.perp_execution import PerpExecution as AsyncPerpExecution
from sdk.async_api.position import Position as AsyncPosition
from sdk.async_api.position_update_payload import PositionUpdatePayload
from sdk.async_api.spot_execution import SpotExecution as AsyncSpotExecution
from sdk.async_api.subscribed_message_payload import SubscribedMessagePayload
from sdk.async_api.wallet_perp_execution_update_payload import WalletPerpExecutionUpdatePayload
from sdk.async_api.wallet_spot_execution_update_payload import WalletSpotExecutionUpdatePayload
from sdk.reya_websocket import WebSocketMessage

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class WebSocketState:
    """WebSocket state tracking and management.

    Uses async_api types directly from WebSocket messages.
    These types are auto-generated from the AsyncAPI spec.
    """

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

        # State tracking - using async_api types directly
        self.last_trade: Optional[AsyncPerpExecution] = None
        self.last_spot_execution: Optional[AsyncSpotExecution] = None
        self.spot_executions: list[AsyncSpotExecution] = []  # All spot executions received (wallet-level)
        self.market_spot_executions: dict[str, list[AsyncSpotExecution]] = {}  # Market-level spot executions by symbol
        self.order_changes: dict[str, AsyncOrder] = {}
        self.positions: dict[str, AsyncPosition] = {}
        self.balances: dict[str, AsyncAccountBalance] = {}  # async_api AccountBalance
        self.balance_updates: list[AsyncAccountBalance] = []
        self.last_depth: dict[str, Depth] = {}

    def clear(self) -> None:
        """Clear all WebSocket state."""
        self.last_trade = None
        self.last_spot_execution = None
        self.spot_executions.clear()
        self.market_spot_executions.clear()
        self.order_changes.clear()
        self.positions.clear()
        self.balances.clear()
        self.balance_updates.clear()
        self.last_depth.clear()

    def clear_balance_updates(self) -> None:
        """Clear the list of balance updates."""
        self.balance_updates.clear()
        logger.debug("Cleared WebSocket balance updates")

    def clear_spot_executions(self) -> None:
        """Clear the list of spot executions."""
        self.spot_executions.clear()
        self.last_spot_execution = None
        logger.debug("Cleared WebSocket spot executions")

    def get_balance_updates_for_account(self, account_id: int) -> list[AsyncAccountBalance]:
        """Get all balance updates for a specific account."""
        return [b for b in self.balance_updates if b.account_id == account_id]

    def get_balance_update_count(self) -> int:
        """Get the current count of balance updates."""
        return len(self.balance_updates)

    def subscribe_to_market_depth(self, symbol: str) -> None:
        """Subscribe to L2 market depth updates for a specific symbol."""
        if self._t._websocket is None:
            raise RuntimeError("WebSocket not connected - call setup() first")
        self._t._websocket.market.depth(symbol).subscribe()
        logger.info(f"Subscribed to market depth for {symbol}")

    def subscribe_to_market_spot_executions(self, symbol: str) -> None:
        """Subscribe to market-level spot executions for a specific symbol."""
        if self._t._websocket is None:
            raise RuntimeError("WebSocket not connected - call setup() first")
        self._t._websocket.market.spot_executions(symbol).subscribe()
        logger.info(f"Subscribed to market spot executions for {symbol}")

    def clear_market_spot_executions(self, symbol: Optional[str] = None) -> None:
        """Clear market spot executions. If symbol provided, clear only that symbol."""
        if symbol:
            self.market_spot_executions.pop(symbol, None)
            logger.debug(f"Cleared market spot executions for {symbol}")
        else:
            self.market_spot_executions.clear()
            logger.debug("Cleared all market spot executions")

    def on_open(self, ws) -> None:
        """Handle WebSocket connection open."""
        logger.info("WebSocket opened, subscribing to trade feeds")

        ws.wallet.perp_executions(self._t.owner_wallet_address).subscribe()
        ws.wallet.spot_executions(self._t.owner_wallet_address).subscribe()
        ws.wallet.order_changes(self._t.owner_wallet_address).subscribe()
        ws.wallet.positions(self._t.owner_wallet_address).subscribe()
        ws.wallet.balances(self._t.owner_wallet_address).subscribe()

    def on_message(self, ws, message: WebSocketMessage) -> None:
        """Handle WebSocket messages using typed payloads from SDK.

        The SDK parses all messages into typed Pydantic models automatically.
        This follows the same pattern as the REST API - no raw dict access.
        """
        logger.info(f"Received message: {type(message).__name__}")

        # Handle subscribed messages with initial snapshots
        if isinstance(message, SubscribedMessagePayload):
            logger.info(f"✅ Subscribed to {message.channel}")

            # Handle initial snapshot for depth channel
            if "depth" in message.channel and message.contents:
                depth = Depth.model_validate(message.contents)
                self.last_depth[depth.symbol] = depth
                logger.info(f"Stored depth snapshot for {depth.symbol}: {len(depth.bids)} bids, {len(depth.asks)} asks")

            # Handle initial snapshot for market spot executions channel
            if "/market/" in message.channel and "spotExecutions" in message.channel and message.contents:
                symbol = message.channel.split("/")[3]  # /v2/market/{symbol}/spotExecutions
                data = message.contents.get("data", [])

                if symbol not in self.market_spot_executions:
                    self.market_spot_executions[symbol] = []

                for e in data:
                    execution = AsyncSpotExecution.model_validate(e)
                    self.market_spot_executions[symbol].append(execution)

                logger.info(f"Stored market spot executions snapshot for {symbol}: {len(data)} execution(s)")

        # Handle perp executions - store async_api type directly
        elif isinstance(message, WalletPerpExecutionUpdatePayload):
            for trade in message.data:
                self.last_trade = trade

        # Handle spot executions (market or wallet level) - store async_api type directly
        elif isinstance(message, (MarketSpotExecutionUpdatePayload, WalletSpotExecutionUpdatePayload)):
            is_market_channel = "/market/" in message.channel
            for exec_data in message.data:
                self.last_spot_execution = exec_data

                if is_market_channel:
                    symbol = message.channel.split("/")[3]
                    if symbol not in self.market_spot_executions:
                        self.market_spot_executions[symbol] = []
                    self.market_spot_executions[symbol].append(exec_data)
                    logger.debug(f"Added market spot execution for {symbol}: {exec_data.order_id}")
                else:
                    self.spot_executions.append(exec_data)

        # Handle order changes - store async_api type directly
        elif isinstance(message, OrderChangeUpdatePayload):
            for order_data in message.data:
                self.order_changes[order_data.order_id] = order_data

        # Handle position updates - store async_api type directly
        elif isinstance(message, PositionUpdatePayload):
            for pos_data in message.data:
                self.positions[pos_data.symbol] = pos_data

        # Handle depth updates
        elif isinstance(message, MarketDepthUpdatePayload):
            depth = message.data
            symbol = depth.symbol
            existing = self.last_depth.get(symbol)

            if existing is None:
                self.last_depth[symbol] = depth
            else:
                # Merge updates into existing depth
                existing_bids = list(existing.bids) if existing.bids else []
                existing_asks = list(existing.asks) if existing.asks else []

                # Process bid updates
                for level in depth.bids:
                    existing_bids = [b for b in existing_bids if b.px != level.px]
                    if float(level.qty) > 0:
                        existing_bids.append(level)

                # Process ask updates
                for level in depth.asks:
                    existing_asks = [a for a in existing_asks if a.px != level.px]
                    if float(level.qty) > 0:
                        existing_asks.append(level)

                # Sort bids descending, asks ascending
                existing_bids = sorted(existing_bids, key=lambda x: float(x.px), reverse=True)
                existing_asks = sorted(existing_asks, key=lambda x: float(x.px))

                self.last_depth[symbol] = Depth(
                    symbol=symbol,
                    type=existing.type,
                    bids=existing_bids,
                    asks=existing_asks,
                    updatedAt=depth.updated_at,  # Use alias for input, access via snake_case
                )

        # Handle account balance updates - store async_api type directly
        elif isinstance(message, AccountBalanceUpdatePayload):
            for balance_data in message.data:
                self.balances[balance_data.asset] = balance_data
                self.balance_updates.append(balance_data)
                logger.debug(f"Added balance update: account_id={balance_data.account_id}, asset={balance_data.asset}")

    def verify_spot_trade_balance_changes(
        self,
        maker_initial_balances: dict[str, AsyncAccountBalance],
        maker_final_balances: dict[str, AsyncAccountBalance],
        taker_initial_balances: dict[str, AsyncAccountBalance],
        taker_final_balances: dict[str, AsyncAccountBalance],
        base_asset: str,
        quote_asset: str,
        qty: str,
        price: str,
        is_maker_buyer: bool,
    ) -> None:
        """
        Verify that balance changes for a spot trade match expected amounts EXACTLY.

        Note: Spot trading has ZERO fees, so we verify exact balance changes:
        - Base asset change = qty (exactly)
        - Quote asset change = qty * price (exactly)
        """
        from decimal import Decimal

        qty_decimal = Decimal(qty)
        price_decimal = Decimal(price)
        notional = qty_decimal * price_decimal

        logger.info("\n💰 Verifying EXACT spot trade balance changes (zero fees)...")
        logger.info(f"Trade: qty={qty} {base_asset} at price={price} {quote_asset}")
        logger.info(f"Notional: {notional} {quote_asset}")
        logger.info(f"Maker is {'BUYER' if is_maker_buyer else 'SELLER'}")

        maker_initial_base = maker_initial_balances.get(base_asset)
        maker_final_base = maker_final_balances.get(base_asset)
        maker_initial_quote = maker_initial_balances.get(quote_asset)
        maker_final_quote = maker_final_balances.get(quote_asset)

        taker_initial_base = taker_initial_balances.get(base_asset)
        taker_final_base = taker_final_balances.get(base_asset)
        taker_initial_quote = taker_initial_balances.get(quote_asset)
        taker_final_quote = taker_final_balances.get(quote_asset)

        logger.info(f"Maker balances - initial: base={maker_initial_base}, quote={maker_initial_quote}")
        logger.info(f"Maker balances - final: base={maker_final_base}, quote={maker_final_quote}")
        logger.info(f"Taker balances - initial: base={taker_initial_base}, quote={taker_initial_quote}")
        logger.info(f"Taker balances - final: base={taker_final_base}, quote={taker_final_quote}")

        assert maker_initial_base and maker_final_base, f"Maker {base_asset} balance not found"
        assert maker_initial_quote and maker_final_quote, f"Maker {quote_asset} balance not found"
        assert taker_initial_base and taker_final_base, f"Taker {base_asset} balance not found"
        assert taker_initial_quote and taker_final_quote, f"Taker {quote_asset} balance not found"

        # Use Decimal for exact comparison (spot has zero fees)
        maker_base_change = Decimal(maker_final_base.real_balance) - Decimal(maker_initial_base.real_balance)
        maker_quote_change = Decimal(maker_final_quote.real_balance) - Decimal(maker_initial_quote.real_balance)
        taker_base_change = Decimal(taker_final_base.real_balance) - Decimal(taker_initial_base.real_balance)
        taker_quote_change = Decimal(taker_final_quote.real_balance) - Decimal(taker_initial_quote.real_balance)

        logger.info(
            f"Maker {base_asset} change: {maker_base_change} (expected: {'+' if is_maker_buyer else '-'}{qty_decimal})"
        )
        logger.info(
            f"Maker {quote_asset} change: {maker_quote_change} (expected: {'-' if is_maker_buyer else '+'}{notional})"
        )
        logger.info(
            f"Taker {base_asset} change: {taker_base_change} (expected: {'-' if is_maker_buyer else '+'}{qty_decimal})"
        )
        logger.info(
            f"Taker {quote_asset} change: {taker_quote_change} (expected: {'+' if is_maker_buyer else '-'}{notional})"
        )

        # Calculate expected EXACT changes (zero fees)
        if is_maker_buyer:
            expected_maker_base_change = qty_decimal
            expected_maker_quote_change = -notional
            expected_taker_base_change = -qty_decimal
            expected_taker_quote_change = notional
        else:
            expected_maker_base_change = -qty_decimal
            expected_maker_quote_change = notional
            expected_taker_base_change = qty_decimal
            expected_taker_quote_change = -notional

        # Verify EXACT match (spot has zero fees)
        assert (
            maker_base_change == expected_maker_base_change
        ), f"Maker {base_asset} change {maker_base_change} does not exactly match expected {expected_maker_base_change}"

        assert (
            maker_quote_change == expected_maker_quote_change
        ), f"Maker {quote_asset} change {maker_quote_change} does not exactly match expected {expected_maker_quote_change}"

        assert (
            taker_base_change == expected_taker_base_change
        ), f"Taker {base_asset} change {taker_base_change} does not exactly match expected {expected_taker_base_change}"

        assert (
            taker_quote_change == expected_taker_quote_change
        ), f"Taker {quote_asset} change {taker_quote_change} does not exactly match expected {expected_taker_quote_change}"

        logger.info("✅ All balance changes verified EXACTLY (zero fees confirmed)!")
