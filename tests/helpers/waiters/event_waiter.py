"""
Event waiting utilities for async test operations.

This module provides utilities for waiting on asynchronous events
with configurable timeouts and polling intervals.
"""

from typing import Optional, TypeVar, Callable, Awaitable
import asyncio
import logging
import time

from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.position import Position
from sdk.open_api.models.spot_execution import SpotExecution

from tests.helpers.clients.rest_client import RestClient
from tests.helpers.clients.websocket_client import WebSocketClient
from tests.helpers.utils import match_order, match_spot_order


logger = logging.getLogger("reya.test.waiter")

T = TypeVar("T")


class EventWaiter:
    """
    Utility class for waiting on async events with REST and WebSocket verification.
    
    Provides methods to wait for various trading events (executions, order state changes)
    with configurable timeouts and automatic REST/WS cross-verification.
    """
    
    def __init__(
        self,
        rest_client: RestClient,
        ws_client: WebSocketClient,
        default_timeout: float = 10.0,
        poll_interval: float = 0.1,
    ):
        """
        Initialize the event waiter.
        
        Args:
            rest_client: REST API client for polling
            ws_client: WebSocket client for real-time updates
            default_timeout: Default timeout in seconds
            poll_interval: Polling interval in seconds
        """
        self.rest = rest_client
        self.ws = ws_client
        self.default_timeout = default_timeout
        self.poll_interval = poll_interval
    
    async def wait_for_condition(
        self,
        condition: Callable[[], Awaitable[Optional[T]]],
        timeout: Optional[float] = None,
        description: str = "condition",
    ) -> T:
        """
        Wait for a condition to be met.
        
        Args:
            condition: Async callable that returns a value when condition is met, None otherwise
            timeout: Timeout in seconds (uses default if not specified)
            description: Description for logging
        
        Returns:
            The value returned by the condition when met
        
        Raises:
            RuntimeError: If timeout is reached
        """
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            result = await condition()
            if result is not None:
                elapsed = time.time() - start_time
                logger.info(f"✅ {description} met after {elapsed:.2f}s")
                return result
            await asyncio.sleep(self.poll_interval)
        
        raise RuntimeError(f"{description} not met after {timeout}s timeout")
    
    async def wait_for_perp_execution(
        self,
        expected_order: Order,
        expected_qty: Optional[str] = None,
        timeout: Optional[float] = None,
        verify_position: bool = True,
    ) -> PerpExecution:
        """
        Wait for perpetual execution confirmation via REST and WebSocket.
        
        Args:
            expected_order: Expected order details for matching
            expected_qty: Expected quantity (if different from order qty)
            timeout: Timeout in seconds
            verify_position: Whether to also verify position update
        
        Returns:
            The confirmed PerpExecution
        """
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        rest_trade = None
        ws_trade = None
        trade_seq_num = None
        ws_position = None
        rest_position = None
        
        logger.info("⏳ Waiting for perp execution...")
        
        while time.time() - start_time < timeout:
            # Check WebSocket for trade
            if ws_trade is None and self.ws.last_perp_execution is not None:
                if match_order(expected_order, self.ws.last_perp_execution, expected_qty):
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Trade confirmed via WS: {self.ws.last_perp_execution.sequence_number} ({elapsed:.2f}s)")
                    ws_trade = self.ws.last_perp_execution
                    trade_seq_num = ws_trade.sequence_number
            
            # Check REST for trade
            if rest_trade is None:
                last_trade = await self.rest.get_last_perp_execution()
                if last_trade and match_order(expected_order, last_trade, expected_qty):
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Trade confirmed via REST: {last_trade.sequence_number} ({elapsed:.2f}s)")
                    rest_trade = last_trade
                    trade_seq_num = trade_seq_num or last_trade.sequence_number
            
            if verify_position:
                # Check WebSocket for position
                if ws_position is None and expected_order.symbol in self.ws.positions:
                    ws_pos = self.ws.positions[expected_order.symbol]
                    if trade_seq_num and ws_pos.last_trade_sequence_number == trade_seq_num:
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Position confirmed via WS ({elapsed:.2f}s)")
                        ws_position = ws_pos
                
                # Check REST for position
                if rest_position is None and trade_seq_num:
                    position = await self.rest.get_position(expected_order.symbol)
                    if position and position.last_trade_sequence_number == trade_seq_num:
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Position confirmed via REST ({elapsed:.2f}s)")
                        rest_position = position
                
                # All confirmations received
                if rest_trade and ws_trade and rest_position and ws_position:
                    return rest_trade
            else:
                # Only trade confirmation needed
                if rest_trade and ws_trade:
                    return rest_trade
            
            await asyncio.sleep(self.poll_interval)
        
        raise RuntimeError(
            f"Perp execution not confirmed after {timeout}s. "
            f"REST: {rest_trade is not None}, WS: {ws_trade is not None}, "
            f"REST pos: {rest_position is not None}, WS pos: {ws_position is not None}"
        )
    
    async def wait_for_spot_execution(
        self,
        expected_order: Order,
        expected_qty: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> SpotExecution:
        """
        Wait for spot execution confirmation via REST and WebSocket.
        
        Args:
            expected_order: Expected order details for matching
            expected_qty: Expected quantity (if different from order qty)
            timeout: Timeout in seconds
        
        Returns:
            The confirmed SpotExecution
        """
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        rest_execution = None
        ws_execution = None
        
        logger.info("⏳ Waiting for spot execution...")
        
        while time.time() - start_time < timeout:
            # Check WebSocket for execution
            if ws_execution is None and self.ws.last_spot_execution is not None:
                if match_spot_order(expected_order, self.ws.last_spot_execution, expected_qty):
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Spot execution confirmed via WS: {self.ws.last_spot_execution.order_id} ({elapsed:.2f}s)")
                    ws_execution = self.ws.last_spot_execution
            
            # Check REST for execution (once we have WS confirmation)
            if rest_execution is None and ws_execution is not None:
                executions = await self.rest.get_wallet_spot_executions()
                for execution in executions.data:
                    if execution.order_id == ws_execution.order_id:
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Spot execution confirmed via REST: {execution.order_id} ({elapsed:.2f}s)")
                        rest_execution = execution
                        break
            
            if rest_execution and ws_execution:
                return rest_execution
            
            await asyncio.sleep(self.poll_interval)
        
        raise RuntimeError(
            f"Spot execution not confirmed after {timeout}s. "
            f"REST: {rest_execution is not None}, WS: {ws_execution is not None}"
        )
    
    async def wait_for_order_state(
        self,
        order_id: str,
        expected_status: OrderStatus,
        timeout: Optional[float] = None,
    ) -> str:
        """
        Wait for order to reach a specific state.
        
        Args:
            order_id: Order ID to monitor
            expected_status: Expected order status
            timeout: Timeout in seconds
        
        Returns:
            The order ID when state is reached
        """
        if expected_status == OrderStatus.OPEN:
            raise ValueError("Use wait_for_order_creation for OPEN status")
        
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        rest_match = False
        ws_match = False
        
        logger.debug(f"⏳ Waiting for order {order_id} to reach {expected_status.value}...")
        
        while time.time() - start_time < timeout:
            # Check REST - order should not be in open orders
            if not rest_match:
                orders = await self.rest.get_open_orders()
                order_ids = [o.order_id for o in orders]
                if order_id not in order_ids:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Order {expected_status.value} via REST ({elapsed:.2f}s)")
                    rest_match = True
            
            # Check WebSocket
            if not ws_match:
                ws_order = self.ws.get_order_change(order_id)
                if ws_order and ws_order.status == expected_status:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Order {expected_status.value} via WS ({elapsed:.2f}s)")
                    ws_match = True
                elif ws_order and ws_order.status not in [OrderStatus.OPEN, expected_status]:
                    raise RuntimeError(
                        f"Order {order_id} reached unexpected state {ws_order.status.value}, "
                        f"expected {expected_status.value}"
                    )
            
            if rest_match and ws_match:
                return order_id
            
            await asyncio.sleep(self.poll_interval)
        
        raise RuntimeError(f"Order {order_id} did not reach {expected_status.value} after {timeout}s")
    
    async def wait_for_order_creation(
        self,
        order_id: str,
        timeout: Optional[float] = None,
    ) -> Order:
        """
        Wait for order creation confirmation.
        
        Args:
            order_id: Order ID to monitor
            timeout: Timeout in seconds
        
        Returns:
            The created Order
        """
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        rest_order = None
        ws_order = None
        
        logger.debug(f"⏳ Waiting for order {order_id} creation...")
        
        while time.time() - start_time < timeout:
            # Check REST
            if rest_order is None:
                orders = await self.rest.get_open_orders()
                for order in orders:
                    if order.order_id == order_id:
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Order created via REST ({elapsed:.2f}s)")
                        rest_order = order
                        break
            
            # Check WebSocket
            if ws_order is None:
                ws_order = self.ws.get_order_change(order_id)
                if ws_order and ws_order.status in [OrderStatus.OPEN, "PARTIALLY_FILLED"]:
                    elapsed = time.time() - start_time
                    logger.info(f"✅ Order created via WS ({elapsed:.2f}s)")
            
            if rest_order and ws_order:
                return rest_order
            elif ws_order:
                return ws_order
            elif rest_order:
                return rest_order
            
            await asyncio.sleep(self.poll_interval)
        
        raise RuntimeError(f"Order {order_id} not created after {timeout}s")
    
    async def wait_for_position_closed(
        self,
        symbol: str,
        timeout: Optional[float] = None,
    ) -> None:
        """
        Wait for a position to be closed.
        
        Args:
            symbol: Symbol to monitor
            timeout: Timeout in seconds
        """
        timeout = timeout or self.default_timeout
        start_time = time.time()
        
        logger.debug(f"⏳ Waiting for {symbol} position to close...")
        
        while time.time() - start_time < timeout:
            position = await self.rest.get_position(symbol)
            if position is None:
                elapsed = time.time() - start_time
                logger.info(f"✅ Position closed ({elapsed:.2f}s)")
                return
            
            await asyncio.sleep(self.poll_interval)
        
        raise RuntimeError(f"Position {symbol} not closed after {timeout}s")


# ==================== Convenience Functions ====================

async def wait_for_order_execution(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    expected_order: Order,
    timeout: float = 10.0,
) -> PerpExecution:
    """Convenience function to wait for perp execution."""
    waiter = EventWaiter(rest_client, ws_client, default_timeout=timeout)
    return await waiter.wait_for_perp_execution(expected_order)


async def wait_for_spot_execution(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    expected_order: Order,
    timeout: float = 10.0,
) -> SpotExecution:
    """Convenience function to wait for spot execution."""
    waiter = EventWaiter(rest_client, ws_client, default_timeout=timeout)
    return await waiter.wait_for_spot_execution(expected_order)


async def wait_for_order_state(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    order_id: str,
    expected_status: OrderStatus,
    timeout: float = 10.0,
) -> str:
    """Convenience function to wait for order state."""
    waiter = EventWaiter(rest_client, ws_client, default_timeout=timeout)
    return await waiter.wait_for_order_state(order_id, expected_status)


async def wait_for_order_creation(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    order_id: str,
    timeout: float = 10.0,
) -> Order:
    """Convenience function to wait for order creation."""
    waiter = EventWaiter(rest_client, ws_client, default_timeout=timeout)
    return await waiter.wait_for_order_creation(order_id)


async def wait_for_position_closed(
    rest_client: RestClient,
    ws_client: WebSocketClient,
    symbol: str,
    timeout: float = 10.0,
) -> None:
    """Convenience function to wait for position to close."""
    waiter = EventWaiter(rest_client, ws_client, default_timeout=timeout)
    await waiter.wait_for_position_closed(symbol)
