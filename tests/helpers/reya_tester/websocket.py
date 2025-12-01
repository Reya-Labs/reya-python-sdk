"""WebSocket state management for ReyaTester."""

from typing import TYPE_CHECKING, Optional
import json
import logging

from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.price import Price
from sdk.open_api.models.spot_execution import SpotExecution

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class WebSocketState:
    """WebSocket state tracking and management."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester
        
        # State tracking
        self.last_trade: Optional[PerpExecution] = None
        self.last_spot_execution: Optional[SpotExecution] = None
        self.spot_executions: list[SpotExecution] = []  # All spot executions received
        self.order_changes: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.balances: dict[str, AccountBalance] = {}
        self.balance_updates: list[AccountBalance] = []
        self.current_prices: dict[str, Price] = {}
        self.last_depth: dict[str, dict] = {}

    def clear(self) -> None:
        """Clear all WebSocket state."""
        self.last_trade = None
        self.last_spot_execution = None
        self.spot_executions.clear()
        self.order_changes.clear()
        self.positions.clear()
        self.balances.clear()
        self.balance_updates.clear()
        self.current_prices.clear()
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

    def get_balance_updates_for_account(self, account_id: int) -> list[AccountBalance]:
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

    def on_open(self, ws) -> None:
        """Handle WebSocket connection open."""
        logger.info("WebSocket opened, subscribing to trade feeds")

        ws.wallet.perp_executions(self._t.owner_wallet_address).subscribe()
        ws.wallet.spot_executions(self._t.owner_wallet_address).subscribe()
        ws.wallet.order_changes(self._t.owner_wallet_address).subscribe()
        ws.wallet.positions(self._t.owner_wallet_address).subscribe()
        ws.wallet.balances(self._t.owner_wallet_address).subscribe()

    def on_message(self, ws, message) -> None:
        """Handle WebSocket messages."""
        message_type = message.get("type")
        logger.info(f"Received message: {message}")
        
        if message_type == "subscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"✅ Subscribed to {channel}")
            
            # Handle initial snapshot for depth channel
            if "depth" in channel and "contents" in message:
                contents = message["contents"]
                symbol = contents.get("symbol") or channel.split("/")[3]
                # Normalize snapshot format (px/qty -> price/quantity)
                normalized = {
                    "type": contents.get("type", "SNAPSHOT"),
                    "bids": [{"price": b.get("px", b.get("price")), "quantity": b.get("qty", b.get("quantity"))} for b in contents.get("bids", [])],
                    "asks": [{"price": a.get("px", a.get("price")), "quantity": a.get("qty", a.get("quantity"))} for a in contents.get("asks", [])],
                    "updatedAt": contents.get("updatedAt")
                }
                self.last_depth[symbol] = normalized
                logger.info(f"Stored depth snapshot for {symbol}: {len(normalized['bids'])} bids, {len(normalized['asks'])} asks")
        elif message_type == "channel_data":
            channel = message.get("channel", "unknown")

            if "perpExecutions" in channel:
                for e in message["data"]:
                    trade = PerpExecution.from_dict(e)
                    assert trade is not None
                    self.last_trade = trade

            if "spotExecutions" in channel:
                for e in message["data"]:
                    execution = SpotExecution.from_dict(e)
                    assert execution is not None
                    self.last_spot_execution = execution
                    self.spot_executions.append(execution)

            if "orderChanges" in channel:
                for o in message["data"]:
                    order = Order.from_dict(o)
                    assert order is not None
                    self.order_changes[order.order_id] = order

            if "positions" in channel:
                for p in message["data"]:
                    position = Position.from_dict(p)
                    assert position is not None
                    self.positions[position.symbol] = position

            if "balances" in channel or "accountBalances" in channel:
                for b in message["data"]:
                    balance = AccountBalance.from_dict(b)
                    assert balance is not None
                    self.balances[balance.asset] = balance
                    self.balance_updates.append(balance)
                    logger.debug(f"Added balance update: account_id={balance.account_id}, asset={balance.asset}")

            if "depth" in channel:
                depth_data = message["data"]
                symbol = channel.split("/")[3]
                # Normalize update format and merge with existing depth
                existing = self.last_depth.get(symbol, {"bids": [], "asks": []})
                
                # Process bid updates
                for update in depth_data.get("bids", []):
                    price = update.get("price")
                    qty = update.get("quantity", "0")
                    # Remove existing entry at this price
                    existing["bids"] = [b for b in existing.get("bids", []) if b.get("price") != price]
                    # Add if quantity > 0
                    if float(qty) > 0:
                        existing["bids"].append({"price": price, "quantity": qty})
                
                # Process ask updates
                for update in depth_data.get("asks", []):
                    price = update.get("price")
                    qty = update.get("quantity", "0")
                    # Remove existing entry at this price
                    existing["asks"] = [a for a in existing.get("asks", []) if a.get("price") != price]
                    # Add if quantity > 0
                    if float(qty) > 0:
                        existing["asks"].append({"price": price, "quantity": qty})
                
                # Sort bids descending, asks ascending
                existing["bids"] = sorted(existing.get("bids", []), key=lambda x: float(x.get("price", 0)), reverse=True)
                existing["asks"] = sorted(existing.get("asks", []), key=lambda x: float(x.get("price", 0)))
                existing["updatedAt"] = depth_data.get("updatedAt")
                
                self.last_depth[symbol] = existing

        elif message_type == "ping":
            ws.send(json.dumps({"type": "pong"}))

    def verify_spot_trade_balance_changes(
        self,
        maker_account_id: int,
        taker_account_id: int,
        maker_initial_balances: dict[str, AccountBalance],
        maker_final_balances: dict[str, AccountBalance],
        taker_initial_balances: dict[str, AccountBalance],
        taker_final_balances: dict[str, AccountBalance],
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

        logger.info(f"Maker {base_asset} change: {maker_base_change} (expected: {'+' if is_maker_buyer else '-'}{qty_decimal})")
        logger.info(f"Maker {quote_asset} change: {maker_quote_change} (expected: {'-' if is_maker_buyer else '+'}{notional})")
        logger.info(f"Taker {base_asset} change: {taker_base_change} (expected: {'-' if is_maker_buyer else '+'}{qty_decimal})")
        logger.info(f"Taker {quote_asset} change: {taker_quote_change} (expected: {'+' if is_maker_buyer else '-'}{notional})")

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
        assert maker_base_change == expected_maker_base_change, (
            f"Maker {base_asset} change {maker_base_change} does not exactly match expected {expected_maker_base_change}"
        )

        assert maker_quote_change == expected_maker_quote_change, (
            f"Maker {quote_asset} change {maker_quote_change} does not exactly match expected {expected_maker_quote_change}"
        )

        assert taker_base_change == expected_taker_base_change, (
            f"Taker {base_asset} change {taker_base_change} does not exactly match expected {expected_taker_base_change}"
        )

        assert taker_quote_change == expected_taker_quote_change, (
            f"Taker {quote_asset} change {taker_quote_change} does not exactly match expected {expected_taker_quote_change}"
        )

        logger.info("✅ All balance changes verified EXACTLY (zero fees confirmed)!")
