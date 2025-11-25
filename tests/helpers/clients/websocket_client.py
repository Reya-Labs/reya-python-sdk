"""
WebSocket client wrapper for integration tests.

This module provides a wrapper around the SDK's ReyaSocket,
managing subscriptions and tracking received messages for test assertions.
"""

from typing import Optional, Callable
import asyncio
import json
import logging
import os

from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.price import Price
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.reya_websocket import ReyaSocket


logger = logging.getLogger("reya.test.websocket_client")


class WebSocketClient:
    """
    WebSocket client wrapper for integration tests.
    
    Manages WebSocket connection, subscriptions, and message tracking.
    Provides easy access to the latest received data for test assertions.
    
    Attributes:
        last_perp_execution: Most recent perpetual execution received
        last_spot_execution: Most recent spot execution received
        order_changes: Dict of order changes by order_id
        positions: Dict of positions by symbol
        balances: Dict of balances by asset
        balance_updates: List of all balance updates received
        depth: Dict of market depth by symbol
    """
    
    def __init__(self, ws_url: Optional[str] = None):
        """
        Initialize the WebSocket client.
        
        Args:
            ws_url: WebSocket URL. If not provided, uses REYA_WS_URL env var
                   or defaults to wss://ws.reya.xyz/
        """
        self._ws_url = ws_url or os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
        self._socket: Optional[ReyaSocket] = None
        self._connected = False
        
        # Message tracking
        self.last_perp_execution: Optional[PerpExecution] = None
        self.last_spot_execution: Optional[SpotExecution] = None
        self.order_changes: dict[str, Order] = {}
        self.positions: dict[str, Position] = {}
        self.balances: dict[str, AccountBalance] = {}
        self.balance_updates: list[AccountBalance] = []
        self.prices: dict[str, Price] = {}
        self.depth: dict[str, dict] = {}
        
        # Custom message handlers
        self._on_perp_execution: Optional[Callable[[PerpExecution], None]] = None
        self._on_spot_execution: Optional[Callable[[SpotExecution], None]] = None
        self._on_order_change: Optional[Callable[[Order], None]] = None
        self._on_position: Optional[Callable[[Position], None]] = None
        self._on_balance: Optional[Callable[[AccountBalance], None]] = None
    
    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected
    
    def connect(self) -> None:
        """
        Connect to the WebSocket server.
        
        Note: This is synchronous. The connection runs in a background thread.
        """
        if self._connected:
            logger.warning("WebSocket already connected")
            return
        
        self._socket = ReyaSocket(
            url=self._ws_url,
            on_open=self._handle_open,
            on_message=self._handle_message,
        )
        self._socket.connect()
        self._connected = True
        logger.info(f"WebSocket connected to {self._ws_url}")
    
    def disconnect(self) -> None:
        """Disconnect from the WebSocket server."""
        if self._socket:
            self._socket.close()
            self._socket = None
        self._connected = False
        logger.info("WebSocket disconnected")
    
    def _handle_open(self, ws) -> None:
        """Handle WebSocket connection open."""
        logger.info("WebSocket connection opened")
    
    def _handle_message(self, ws, message: dict) -> None:
        """Handle incoming WebSocket messages."""
        message_type = message.get("type")
        
        if message_type == "subscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"Subscribed to {channel}")
            
        elif message_type == "channel_data":
            self._process_channel_data(message)
            
        elif message_type == "ping":
            ws.send(json.dumps({"type": "pong"}))
            
        else:
            logger.debug(f"Received message type: {message_type}")
    
    def _process_channel_data(self, message: dict) -> None:
        """Process channel_data messages and update tracking state."""
        channel = message.get("channel", "")
        data = message.get("data", [])
        
        if "perpExecutions" in channel:
            for item in data:
                execution = PerpExecution.from_dict(item)
                if execution:
                    self.last_perp_execution = execution
                    if self._on_perp_execution:
                        self._on_perp_execution(execution)
                        
        elif "spotExecutions" in channel:
            for item in data:
                execution = SpotExecution.from_dict(item)
                if execution:
                    self.last_spot_execution = execution
                    if self._on_spot_execution:
                        self._on_spot_execution(execution)
                        
        elif "orderChanges" in channel:
            for item in data:
                order = Order.from_dict(item)
                if order and order.order_id:
                    self.order_changes[order.order_id] = order
                    if self._on_order_change:
                        self._on_order_change(order)
                        
        elif "positions" in channel:
            for item in data:
                position = Position.from_dict(item)
                if position and position.symbol:
                    self.positions[position.symbol] = position
                    if self._on_position:
                        self._on_position(position)
                        
        elif "balances" in channel or "accountBalances" in channel:
            for item in data:
                balance = AccountBalance.from_dict(item)
                if balance and balance.asset:
                    self.balances[balance.asset] = balance
                    self.balance_updates.append(balance)
                    if self._on_balance:
                        self._on_balance(balance)
                        
        elif "depth" in channel:
            # Extract symbol from channel path: /v2/market/{symbol}/depth
            parts = channel.split("/")
            if len(parts) >= 4:
                symbol = parts[3]
                self.depth[symbol] = data
    
    # ==================== Subscriptions ====================
    
    def subscribe_wallet(self, wallet_address: str) -> None:
        """
        Subscribe to all wallet-related channels.
        
        Args:
            wallet_address: The wallet address to subscribe to
        """
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        
        self._socket.wallet.perp_executions(wallet_address).subscribe()
        self._socket.wallet.spot_executions(wallet_address).subscribe()
        self._socket.wallet.order_changes(wallet_address).subscribe()
        self._socket.wallet.positions(wallet_address).subscribe()
        self._socket.wallet.balances(wallet_address).subscribe()
        logger.info(f"Subscribed to wallet channels for {wallet_address}")
    
    def subscribe_perp_executions(self, wallet_address: str) -> None:
        """Subscribe to perpetual executions for a wallet."""
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        self._socket.wallet.perp_executions(wallet_address).subscribe()
    
    def subscribe_spot_executions(self, wallet_address: str) -> None:
        """Subscribe to spot executions for a wallet."""
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        self._socket.wallet.spot_executions(wallet_address).subscribe()
    
    def subscribe_order_changes(self, wallet_address: str) -> None:
        """Subscribe to order changes for a wallet."""
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        self._socket.wallet.order_changes(wallet_address).subscribe()
    
    def subscribe_positions(self, wallet_address: str) -> None:
        """Subscribe to positions for a wallet."""
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        self._socket.wallet.positions(wallet_address).subscribe()
    
    def subscribe_balances(self, wallet_address: str) -> None:
        """Subscribe to balances for a wallet."""
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        self._socket.wallet.balances(wallet_address).subscribe()
    
    def subscribe_market_depth(self, symbol: str) -> None:
        """Subscribe to market depth for a symbol."""
        if not self._socket:
            raise RuntimeError("WebSocket not connected")
        self._socket.market.depth(symbol).subscribe()
        logger.info(f"Subscribed to market depth for {symbol}")
    
    # ==================== State Management ====================
    
    def clear_all(self) -> None:
        """Clear all tracked state."""
        self.last_perp_execution = None
        self.last_spot_execution = None
        self.order_changes.clear()
        self.positions.clear()
        self.balances.clear()
        self.balance_updates.clear()
        self.prices.clear()
        self.depth.clear()
        logger.debug("Cleared all WebSocket state")
    
    def clear_executions(self) -> None:
        """Clear execution tracking."""
        self.last_perp_execution = None
        self.last_spot_execution = None
    
    def clear_order_changes(self) -> None:
        """Clear order changes tracking."""
        self.order_changes.clear()
    
    def clear_balance_updates(self) -> None:
        """Clear balance updates list."""
        self.balance_updates.clear()
    
    # ==================== Getters ====================
    
    def get_order_change(self, order_id: str) -> Optional[Order]:
        """Get order change by order ID."""
        return self.order_changes.get(order_id)
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position by symbol."""
        return self.positions.get(symbol)
    
    def get_balance(self, asset: str) -> Optional[AccountBalance]:
        """Get balance by asset."""
        return self.balances.get(asset)
    
    def get_balance_updates_for_account(self, account_id: int) -> list[AccountBalance]:
        """Get all balance updates for a specific account."""
        return [b for b in self.balance_updates if b.account_id == account_id]
    
    def get_depth(self, symbol: str) -> Optional[dict]:
        """Get market depth for a symbol."""
        return self.depth.get(symbol)
    
    # ==================== Custom Handlers ====================
    
    def on_perp_execution(self, handler: Callable[[PerpExecution], None]) -> None:
        """Set custom handler for perpetual executions."""
        self._on_perp_execution = handler
    
    def on_spot_execution(self, handler: Callable[[SpotExecution], None]) -> None:
        """Set custom handler for spot executions."""
        self._on_spot_execution = handler
    
    def on_order_change(self, handler: Callable[[Order], None]) -> None:
        """Set custom handler for order changes."""
        self._on_order_change = handler
    
    def on_position(self, handler: Callable[[Position], None]) -> None:
        """Set custom handler for position updates."""
        self._on_position = handler
    
    def on_balance(self, handler: Callable[[AccountBalance], None]) -> None:
        """Set custom handler for balance updates."""
        self._on_balance = handler
