from typing import Optional

import asyncio
import json
import logging
import os
import time

import pytest

from sdk.async_api.order_change_update_payload import OrderChangeUpdatePayload
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.execution_type import ExecutionType
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.price import Price
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters
from sdk.reya_websocket import ReyaSocket
from tests.utils import match_order

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

logger = logging.getLogger("reya.integration_tests")


def limit_order_params_to_order(params: LimitOrderParameters, account_id: int) -> Order:
    """Convert LimitOrderParameters to Order object for testing"""
    return Order(
        exchange_id=5,  # REYA_DEX_ID
        symbol=params.symbol,
        account_id=account_id,
        order_id="",  # Will be set when order is created
        qty=params.qty,
        exec_qty="0",
        side=Side.B if params.is_buy else Side.A,
        limit_px=params.limit_px,
        order_type=OrderType.LIMIT,
        trigger_px=None,
        time_in_force=params.time_in_force,
        reduce_only=params.reduce_only or False,
        status=OrderStatus.OPEN,
        created_at=0,  # Will be set when order is created
        last_update_at=0,  # Will be set when order is created
    )


def trigger_order_params_to_order(params: TriggerOrderParameters, account_id: int) -> Order:
    """Convert TriggerOrderParameters to Order object for testing"""
    return Order(
        exchange_id=5,  # REYA_DEX_ID
        symbol=params.symbol,
        account_id=account_id,
        order_id="",  # Will be set when order is created
        qty=None,  # Trigger orders don't have qty until execution
        exec_qty="0",
        side=Side.B if params.is_buy else Side.A,
        limit_px=params.trigger_px,  # For trigger orders, limit_px contains the trigger price
        order_type=params.trigger_type,
        trigger_px=params.trigger_px,
        time_in_force=None,
        reduce_only=False,
        status=OrderStatus.OPEN,
        created_at=0,  # Will be set when order is created
        last_update_at=0,  # Will be set when order is created
    )


class ReyaTester:
    """Helper class for integration tests with the Reya SDK"""

    def __init__(self):
        # Initialize the REST API client with provided or env-based config
        self.client = ReyaTradingClient()
        assert self.client.owner_wallet_address is not None
        assert self.client.config.account_id is not None
        assert self.client.config.chain_id is not None

        self.owner_wallet_address: str = self.client.owner_wallet_address
        self.account_id: int = self.client.config.account_id
        self.chain_id: int = self.client.config.chain_id

        # For WebSocket integration
        self.websocket: Optional[ReyaSocket] = None
        self.ws_last_trade: Optional[PerpExecution] = None
        self.ws_order_changes: dict[str, Order] = {}
        self.ws_positions: dict[str, Position] = {}
        self.ws_current_prices: dict[str, Price] = {}

    async def setup(self):
        """Set up WebSocket connection for trade monitoring"""
        ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
        await self.client.start()

        self.websocket = ReyaSocket(
            url=ws_url,
            on_open=self._on_websocket_open,
            on_message=self._on_websocket_message,
        )

        self.websocket.connect()
        logger.info("WebSocket connected for trade monitoring")

        # Wait a moment for subscriptions to complete
        await asyncio.sleep(1)

        await self.close_active_orders(fail_if_none=False)
        await self.close_exposures(fail_if_none=False)

    def _on_websocket_open(self, ws):
        """Handle WebSocket connection open"""
        logger.info("WebSocket opened, subscribing to trade feeds")

        # Subscribe to trades for our wallet
        ws.wallet.perp_executions(self.owner_wallet_address).subscribe()
        ws.wallet.order_changes(self.owner_wallet_address).subscribe()
        ws.wallet.positions(self.owner_wallet_address).subscribe()

    def _on_websocket_message(self, ws, message):
        """Handle WebSocket messages for trade confirmations"""
        message_type = message.get("type")
        logger.info(f"Received message: {message}")
        if message_type == "subscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"✅ Subscribed to {channel}")
        elif message_type == "channel_data":
            channel = message.get("channel", "unknown")

            if "perpExecutions" in channel:
                for e in message["data"]:
                    trade = PerpExecution.from_dict(e)
                    assert trade is not None
                    self.ws_last_trade = trade

            if "orderChanges" in channel:
                for o in message["data"]:
                    order = Order.from_dict(o)
                    assert order is not None
                    self.ws_order_changes[order.order_id] = order

            if "positions" in channel:
                for p in message["data"]:
                    position = Position.from_dict(p)
                    assert position is not None
                    self.ws_positions[position.symbol] = position

        elif message_type == "ping":
            ws.send(json.dumps({"type": "pong"}))

    async def get_current_price(self, symbol: str = "ETHRUSDPERP") -> str:
        """Fetch current market prices"""
        price_info: Price = await self.client.markets.get_price(symbol)
        logger.info(f"Price info: {price_info}")
        current_price = price_info.oracle_price

        if current_price:
            logger.info(f"💰 Current market price for {symbol}: ${float(current_price):.2f}")
            return current_price
        else:
            logger.info(f"❌ Current market price for {symbol} not found")
            raise RuntimeError("Current market price not found")

    async def get_positions(self) -> dict[str, Position]:
        """Get current positions"""
        positions_list: list[Position] = await self.client.get_positions()

        position_summary = {}
        for position in positions_list:
            symbol = position.symbol
            qty = position.qty
            if symbol and qty:
                position_summary[symbol] = position

        return position_summary

    async def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for a specific market"""
        positions = await self.get_positions()
        position = positions.get(symbol)
        if position is None:
            return None

        return position

    async def get_last_wallet_perp_execution(self) -> PerpExecution:
        """Get a past trade of a specific transaction hash"""
        trades_list: PerpExecutionList = await self.client.wallet.get_wallet_perp_executions(
            address=self.owner_wallet_address
        )

        return trades_list.data[0]

    async def get_market_definition(self, symbol: str) -> MarketDefinition:
        """Get market configuration for a specific symbol"""
        markets_config: list[MarketDefinition] = await self.client.reference.get_market_definitions()
        for config in markets_config:
            if config.symbol == symbol:
                return config
        raise RuntimeError(f"Market definition not found for symbol: {symbol}")

    async def get_open_order(self, id: str) -> Optional[Order]:
        """Get open orders"""
        open_orders = await self.client.get_open_orders()
        for order in open_orders:
            if order.order_id == id:
                return order
        return None

    async def close_exposures(self, fail_if_none: bool = True):
        """Close exposure for a specific market"""
        positions = await self.get_positions()

        if len(positions) == 0:
            logger.warning("No position to close")
            if fail_if_none:
                assert False
            return None

        for symbol, position in positions.items():
            price_with_offset = 0 if position.side == Side.B else 1000000000000

            limit_order_params = LimitOrderParameters(
                symbol=symbol,
                is_buy=not (position.side == Side.B),  # short if position is long
                limit_px=str(price_with_offset),
                qty=str(position.qty),
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )
            logger.debug(f"Order params: {limit_order_params}")

            order_id = await self.create_limit_order(limit_order_params)

            assert order_id is None

        # Wait for positions to be actually closed with 5 second timeout
        start_time = time.time()
        timeout = 10

        while time.time() - start_time < timeout:
            position_after = await self.get_positions()
            if len(position_after) == 0:
                elapsed_time = time.time() - start_time
                logger.info(f"✅ All positions closed successfully (took {elapsed_time:.2f}s)")
                return

            logger.debug(f"Still have {len(position_after)} positions, waiting...")
            await asyncio.sleep(0.5)

        # Timeout reached, positions still exist
        position_after = await self.get_positions()
        if len(position_after) > 0:
            logger.error(f"Failed to close positions after {timeout}s timeout: {position_after}")
            assert False

    async def close_active_orders(self, fail_if_none: bool = True):
        """Close exposure for a specific market"""
        active_orders: list[Order] = await self.client.get_open_orders()

        if active_orders is None or len(active_orders) == 0:
            logger.warning("No active orders to close")
            if fail_if_none:
                assert False
            return None

        cancelled_order_id = None
        for order in active_orders:
            await self.client.cancel_order(order_id=order.order_id)

            # Note: this confirms trade has been registered, not neccesarely position
            cancelled_order_id = await self.wait_for_order_state(
                order_id=order.order_id, expected_status=OrderStatus.CANCELLED, timeout=10
            )

        assert cancelled_order_id is not None, "Failed to close position"

    async def create_limit_order(self, params: LimitOrderParameters) -> Optional[str]:
        """Create a limit order with the specified parameters"""
        side_text = "BUY" if params.is_buy else "SELL"
        time_in_force_text = "IOC" if params.time_in_force == TimeInForce.IOC else "GTC"

        logger.info(
            f"📤 Creating {time_in_force_text} {side_text} order: symbol={params.symbol}, price=${params.limit_px}, qty={params.qty}"
        )

        response = await self.client.create_limit_order(params)

        # Check response format
        logger.info(f"Response: {response}")

        return response.order_id

    async def create_trigger_order(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """Create a trigger order (TP/SL) with the specified parameters"""
        side_text = "BUY" if params.is_buy else "SELL"
        trigger_type_text = params.trigger_type.value

        logger.info(
            f"📤 Creating {trigger_type_text} {side_text} order: symbol={params.symbol}, trigger_px=${params.trigger_px}"
        )

        response = await self.client.create_trigger_order(params)

        logger.info(f"✅ {trigger_type_text} {side_text} order created with ID: {response.order_id}")
        return response

    async def wait_for_order_execution(self, expected_order: Order, timeout: int = 10) -> PerpExecution:
        logger.info("⏳ Waiting for trade confirmation order...")

        start_time = time.time()
        rest_trade = None
        ws_trade = None
        trade_seq_num = None
        ws_position = None
        rest_position = None

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade

            last_trade = await self.get_last_wallet_perp_execution()

            if ws_trade is None and self.ws_last_trade is not None:
                if match_order(expected_order, self.ws_last_trade):
                    elapsed_time = time.time() - start_time
                    logger.info(
                        f" ✅ Trade confirmed via WS: {self.ws_last_trade.sequence_number} (took {elapsed_time:.2f}s)"
                    )
                    ws_trade = self.ws_last_trade
                    trade_seq_num = self.ws_last_trade.sequence_number
                    assert ws_trade is not None

            # Check WebSocket for position
            if (
                ws_position is None
                and expected_order.symbol in self.ws_positions
                and trade_seq_num == self.ws_positions[expected_order.symbol].last_trade_sequence_number
            ):
                ws_position = self.ws_positions[expected_order.symbol]
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via WS: {expected_order.symbol} (took {elapsed_time:.2f}s)")

            # Check REST API for trade
            if rest_trade is None and match_order(expected_order, last_trade):
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Trade confirmed via REST: {last_trade.sequence_number} (took {elapsed_time:.2f}s)")
                rest_trade = last_trade
                trade_seq_num = last_trade.sequence_number

            # Check REST API for position
            position = await self.get_position(expected_order.symbol)
            if rest_position is None and position is not None and trade_seq_num == position.last_trade_sequence_number:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via REST: {expected_order.symbol} (took {elapsed_time:.2f}s)")
                rest_position = position

            if rest_trade and ws_trade and rest_position and ws_position:
                assert (
                    rest_trade.to_str() == ws_trade.to_str()
                ), f"expected {rest_trade.to_str()} to equal {ws_trade.to_str()}"
                assert (
                    rest_position.to_str() == ws_position.to_str()
                ), f"expected {rest_position.to_str()} to equal {ws_position.to_str()}"
                return rest_trade

            await asyncio.sleep(0.5)

        raise RuntimeError(f"Order not executed after {timeout} seconds, rest_trade: {rest_trade is not None}, ws_trade: {ws_trade is not None}, rest_position: {rest_position is not None}, ws_position: {ws_position is not None}")

    async def wait_for_closing_order_execution(
        self, expected_order: Order, expected_qty: Optional[str] = None, timeout: int = 10
    ) -> PerpExecution:
        logger.info("⏳ Waiting for position-closing trade confirmation...")

        start_time = time.time()
        rest_trade = None
        ws_trade = None
        trade_seq_num = None
        ws_position = None
        rest_closed = False

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade

            last_trade = await self.get_last_wallet_perp_execution()

            if ws_trade is None and self.ws_last_trade is not None:
                if match_order(expected_order, self.ws_last_trade, expected_qty):
                    elapsed_time = time.time() - start_time
                    logger.info(
                        f" ✅ Trade confirmed via WS: {self.ws_last_trade.sequence_number} (took {elapsed_time:.2f}s)"
                    )
                    ws_trade = self.ws_last_trade
                    trade_seq_num = self.ws_last_trade.sequence_number
                    assert ws_trade is not None

            # Check WebSocket for position
            if (
                ws_position is None
                and expected_order.symbol in self.ws_positions
                and trade_seq_num == self.ws_positions[expected_order.symbol].last_trade_sequence_number
            ):
                ws_position = self.ws_positions[expected_order.symbol]
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via WS: {expected_order.symbol} (took {elapsed_time:.2f}s)")

            # Check REST API for trade
            if rest_trade is None and match_order(expected_order, last_trade, expected_qty):
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Trade confirmed via REST: {last_trade.sequence_number} (took {elapsed_time:.2f}s)")
                rest_trade = last_trade
                trade_seq_num = last_trade.sequence_number

            # Check REST API for position
            position = await self.get_position(expected_order.symbol)
            if not rest_closed and position is None:
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Position confirmed via REST: {expected_order.symbol} (took {elapsed_time:.2f}s)")
                rest_closed = True

            if rest_trade and ws_trade and rest_closed and ws_position:
                assert (
                    rest_trade.to_str() == ws_trade.to_str()
                ), f"expected {rest_trade.to_str()} to equal {ws_trade.to_str()}"
                return rest_trade

            await asyncio.sleep(0.5)

        raise RuntimeError(f"Order not executed after {timeout} seconds, rest_trade: {rest_trade is not None}, ws_trade: {ws_trade is not None}, rest_closed: {rest_closed}, ws_position: {ws_position is not None}")

    async def wait_for_order_state(self, order_id: str, expected_status: OrderStatus, timeout: int = 10) -> str:
        """Query both REST and WebSocket for order status change confirmation until timeout"""
        logger.debug(f"⏳ Waiting for order to reach state: {expected_status.value}...")
        assert expected_status != OrderStatus.OPEN, "use wait_for order_creation instead"

        start_time = time.time()
        rest_match = False
        ws_match = False

        while time.time() - start_time < timeout:
            # Check REST API for order status
            orders: list[Order] = await self.client.get_open_orders()
            orders_ids = [order.order_id for order in orders]

            # Order should not be in open orders
            if rest_match == False and order_id not in orders_ids:
                elapsed_time = time.time() - start_time
                logger.info(
                    f" ✅ Order reached {expected_status.value} state via REST: {order_id} (took {elapsed_time:.2f}s)"
                )
                rest_match = True

            # Check WebSocket for order status
            ws_order = self.ws_order_changes.get(order_id)
            if not ws_match and ws_order and ws_order.status == expected_status:
                elapsed_time = time.time() - start_time
                logger.info(
                    f" ✅ Order reached {expected_status.value} state via WS: {order_id} (took {elapsed_time:.2f}s)"
                )
                ws_match = True
            if ws_order and ws_order.status != OrderStatus.OPEN and ws_order.status != expected_status:
                raise RuntimeError(
                    f"Order {order_id} reached {ws_order.status.value} state via WS, but expected {expected_status} (took {elapsed_time:.2f}s)"
                )

            if rest_match and ws_match:
                return order_id

            await asyncio.sleep(0.5)

        raise RuntimeError(f"Order {order_id} did not reach {expected_status.value} state after {timeout} seconds")

    async def wait_for_order_creation(self, order_id: str, timeout: int = 10) -> Order:
        """Query both REST and WebSocket for order creation confirmation until timeout"""
        logger.debug("⏳ Waiting for order creation...")

        start_time = time.time()
        rest_order = None
        ws_order = None

        while time.time() - start_time < timeout:
            # Check REST API for order creation

            orders = await self.client.get_open_orders()
            for order in orders:
                if rest_order is None and order.order_id == order_id:
                    elapsed_time = time.time() - start_time
                    logger.info(f" ✅ Order created via REST: {order} (took {elapsed_time:.2f}s)")
                    rest_order = order
                    break

            # Check WebSocket for order creation

            if ws_order is None and order_id in self.ws_order_changes:
                ws_order = self.ws_order_changes[order_id]
                if ws_order.status in ["OPEN", "PARTIALLY_FILLED"]:
                    elapsed_time = time.time() - start_time
                    logger.info(f" ✅ Order created via WS: {ws_order} (took {elapsed_time:.2f}s)")

            if rest_order and ws_order:
                assert rest_order.order_id == ws_order.order_id
                return rest_order
            elif ws_order:
                # If WebSocket has the order but REST doesn't, trust WebSocket
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Order created via WS only: {ws_order} (took {elapsed_time:.2f}s)")
                return ws_order
            elif rest_order:
                # If REST has the order but WebSocket doesn't, trust REST
                elapsed_time = time.time() - start_time
                logger.info(f" ✅ Order created via REST only: {rest_order} (took {elapsed_time:.2f}s)")
                return rest_order

            await asyncio.sleep(0.5)

        raise RuntimeError(f"Order {order_id} not created after {timeout} seconds")

    async def check_open_order_created(self, order_id: str, expected_order: Order):
        open_order = await self.get_open_order(order_id)

        # For trigger orders (SL/TP), if not found in open orders, check WebSocket
        if open_order is None and expected_order.order_type in [OrderType.SL, OrderType.TP]:
            # Check WebSocket for trigger orders
            if order_id in self.ws_order_changes:
                open_order = self.ws_order_changes[order_id]
                logger.info(f"✅ Trigger order found via WebSocket: {open_order}")

        assert (
            open_order is not None
        ), f"check_open_order_created: Order {order_id} was not found in open orders or WebSocket"
        assert open_order.order_id == order_id, "check_open_order_created: Wrong order id"

        if expected_order.order_type == OrderType.LIMIT:
            assert open_order.limit_px == expected_order.limit_px, "check_open_order_created: Wrong limit price"
            assert open_order.qty == expected_order.qty, "check_open_order_created: Wrong qty"
        else:
            # For trigger orders, use trigger_px field and allow for slight precision differences
            expected_trigger_px = expected_order.trigger_px if expected_order.trigger_px else expected_order.limit_px
            assert float(open_order.trigger_px) == pytest.approx(
                float(expected_trigger_px), rel=1e-6
            ), f"check_open_order_created: Wrong trigger price. Expected: {expected_trigger_px}, Got: {open_order.trigger_px}"
            assert open_order.qty is None, "check_open_order_created: Has qty"

        assert open_order.order_type == expected_order.order_type, "check_open_order_created: Wrong order type"
        assert open_order.side == expected_order.side, "check_open_order_created: Wrong order direction"

        assert open_order.status == OrderStatus.OPEN, "check_open_order_created: Wrong order status"

    async def check_no_open_orders(self):
        open_orders = await self.client.get_open_orders()
        assert len(open_orders) == 0, "check_no_open_orders: Open orders should be empty"

    async def check_position(
        self,
        symbol: str,
        expected_exchange_id: int,
        expected_account_id: int,
        expected_qty: str,
        expected_side: Side,
        expected_avg_entry_price: str | None = None,
        expected_last_trade_sequence_number: int | None = None,
    ):
        position = await self.get_position(symbol)
        if position is None:
            raise RuntimeError("check_position: Position not found")

        if expected_exchange_id is not None:
            assert position.exchange_id == expected_exchange_id, "check_position: Exchange ID does not match"
        if expected_account_id is not None:
            assert position.account_id == expected_account_id, "check_position: Account ID does not match"
        if expected_qty is not None:
            assert position.qty == expected_qty, "check_position: Qty does not match"
        if expected_side is not None:
            assert position.side == expected_side, "check_position: Side does not match"
        if expected_avg_entry_price is not None:
            assert float(position.avg_entry_price) == pytest.approx(
                float(expected_avg_entry_price), rel=1e-6
            ), "check_position: Average entry price does not match"
        if expected_last_trade_sequence_number is not None:
            assert (
                position.last_trade_sequence_number == expected_last_trade_sequence_number
            ), "check_position: Last trade sequence number does not match"

    async def check_position_not_open(self, symbol: str):
        position = await self.get_position(symbol)
        assert position is None, "check_position_not_open: Position should be empty"

        ws_position = self.ws_positions.get(symbol)
        assert (
            ws_position is None or ws_position.qty == "0"
        ), "check_position_not_open: WebSocket position should be empty"

    async def check_order_execution(
        self, order_execution: PerpExecution, expected_order: Order, expected_qty: Optional[str] = None
    ) -> PerpExecution:
        assert order_execution is not None, "check_order_execution: No order execution found"
        assert (
            order_execution.exchange_id == self.client.config.dex_id
        ), "check_order_execution: Exchange ID does not match"
        assert (
            order_execution.symbol == expected_order.symbol
        ), "check_order_execution: Order execution symbol does not match"
        assert (
            order_execution.account_id == expected_order.account_id
        ), "check_order_execution: Order execution account ID does not match"
        assert (
            order_execution.qty == expected_order.qty if expected_qty is None else expected_qty
        ), "check_order_execution: Order execution qty does not match"
        assert order_execution.side == expected_order.side, "check_order_execution: Order execution side does not match"
        assert (
            order_execution.type == ExecutionType.ORDER_MATCH
        ), "check_order_execution: Order execution type does not match"
        if expected_order.order_type == OrderType.LIMIT:
            assert expected_order.limit_px is not None
            if expected_order.side == Side.B:  # Buy
                assert float(order_execution.price) <= float(
                    expected_order.limit_px
                ), "check_order_execution: Order execution price does not match"
            else:  # Sell
                assert float(order_execution.price) >= float(
                    expected_order.limit_px
                ), "check_order_execution: Order execution price does not match"
        return order_execution

    async def check_no_order_execution_since(self, since_timestamp_ms: int):
        order_execution = await self.get_last_wallet_perp_execution()
        if order_execution is not None:
            assert (
                order_execution.timestamp < since_timestamp_ms
            ), "check_no_order_execution_since: Order execution should be empty"

    # Reusable helper functions for common test operations

    async def setup_position(
        self,
        symbol: str = "ETHRUSDPERP",
        is_buy: bool = True,
        qty: str = "0.01",
        price_multiplier: float = 1.01,
        reduce_only: bool = False
    ) -> tuple[str, str]:
        """
        Set up a position by creating and executing a limit order.

        Returns:
            tuple[str, str]: (market_price, position_side) where position_side is 'B' for long, 'A' for short
        """
        # Get current market price
        market_price = await self.get_current_price(symbol)
        logger.info(f"Setting up {'long' if is_buy else 'short'} position at market price: ${market_price}")

        # Create limit order to establish position
        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * price_multiplier),
            is_buy=is_buy,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=reduce_only,
        )
        await self.create_limit_order(limit_order_params)

        # Wait for execution and verify position
        expected_order = limit_order_params_to_order(limit_order_params, self.account_id)
        await self.wait_for_order_execution(expected_order)
        await self.check_no_open_orders()

        # Add small delay for position data to be available
        import asyncio
        await asyncio.sleep(1)

        # Verify position was created
        from sdk.reya_rest_api.config import REYA_DEX_ID
        from sdk.open_api.models.side import Side

        expected_side = Side.B if is_buy else Side.A
        await self.check_position(
            symbol=symbol,
            expected_exchange_id=REYA_DEX_ID,
            expected_account_id=self.account_id,
            expected_qty=qty,
            expected_side=expected_side,
        )

        return market_price, expected_side.value

    async def close_position(
        self,
        symbol: str,
        qty: str = "0.01"
    ) -> None:
        """
        Manually close a position using a market order.

        Args:
            symbol: Trading symbol
            qty: Quantity to close
        """
        # Get current position to determine direction
        position = await self.get_position(symbol)
        if position is None:
            raise RuntimeError("No position found to close")

        from sdk.open_api.models.side import Side

        # Close position with opposite direction
        is_buy = position.side == Side.A  # Buy to close short, sell to close long

        close_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px="0",  # Market order (very low price for sell, will be filled at market)
            is_buy=is_buy,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=True,
        )
        await self.create_limit_order(close_order_params)

        # Wait for closing execution
        expected_order = limit_order_params_to_order(close_order_params, self.account_id)
        execution = await self.wait_for_closing_order_execution(expected_order)
        await self.check_order_execution(execution, expected_order, qty)
        await self.check_position_not_open(symbol)

    async def flip_position(
        self,
        symbol: str,
        current_qty: str = "0.01",
        flip_qty: str = "0.02"
    ) -> None:
        """
        Flip a position from long to short or vice versa.

        Args:
            symbol: Trading symbol
            current_qty: Current position quantity
            flip_qty: Total quantity to trade (should be > current_qty to flip)
        """
        # Get current position to determine direction
        position = await self.get_position(symbol)
        if position is None:
            raise RuntimeError("No position found to flip")

        from sdk.open_api.models.side import Side

        # Trade in opposite direction with larger quantity
        is_buy = position.side == Side.A  # Buy to flip short to long, sell to flip long to short

        flip_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px="0",  # Market order
            is_buy=is_buy,
            time_in_force=TimeInForce.IOC,
            qty=flip_qty,
            reduce_only=False,
        )
        await self.create_limit_order(flip_order_params)

        # Wait for flip execution
        expected_order = limit_order_params_to_order(flip_order_params, self.account_id)
        execution = await self.wait_for_order_execution(expected_order)
        await self.check_order_execution(execution, expected_order, flip_qty)

        # Give some time for position to be updated after flip
        import asyncio
        await asyncio.sleep(2)

        # Verify flipped position
        from sdk.reya_rest_api.config import REYA_DEX_ID
        expected_side = Side.B if is_buy else Side.A
        remaining_qty = str(float(flip_qty) - float(current_qty))

        await self.check_position(
            symbol=symbol,
            expected_exchange_id=REYA_DEX_ID,
            expected_account_id=self.account_id,
            expected_qty=remaining_qty,
            expected_side=expected_side,
        )

