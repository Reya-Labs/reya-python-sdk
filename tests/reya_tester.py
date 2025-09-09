from typing import Optional, Union

import asyncio
import json
import logging
import os
import time

import pytest

from typing import Dict

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
from tests.models import OrderDetails
from tests.utils import match_order

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

logger = logging.getLogger("reya.integration_tests")


class ReyaTester:
    """Helper class for integration tests with the Reya SDK"""

    def __init__(self):
        # Initialize the REST API client with provided or env-based config
        self.client = ReyaTradingClient()

        assert self.client.wallet_address is not None
        assert self.client.config.account_id is not None
        assert self.client.config.chain_id is not None

        self.wallet_address: str = self.client.wallet_address
        self.account_id: int = self.client.config.account_id
        self.chain_id: int = self.client.config.chain_id

        # For WebSocket integration
        self.websocket: Optional[ReyaSocket] = None
        self.ws_confirmed_trades: Dict[int, PerpExecution] = {}
        self.ws_order_changes: Dict[str, Order] = {}
        self.ws_positions : Dict[str, Position] = {}
        self.ws_current_prices: Dict[str, Price] = {}

    async def setup_websocket(self):
        """Set up WebSocket connection for trade monitoring"""
        ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

        self.websocket = ReyaSocket(
            url=ws_url,
            on_open=self._on_websocket_open,
            on_message=self._on_websocket_message,
        )

        self.websocket.connect()
        logger.info("WebSocket connected for trade monitoring")

        # Wait a moment for subscriptions to complete
        await asyncio.sleep(1)

    def _on_websocket_open(self, ws):
        """Handle WebSocket connection open"""
        logger.info("WebSocket opened, subscribing to trade feeds")

        # Subscribe to trades for our wallet
        ws.wallet.perp_executions(self.wallet_address).subscribe()
        ws.wallet.open_orders(self.wallet_address).subscribe()
        ws.wallet.positions(self.wallet_address).subscribe()

    def _on_websocket_message(self, ws, message):
        """Handle WebSocket messages for trade confirmations"""
        message_type = message.get("type")

        if message_type == "subscribed":
            channel = message.get("channel", "unknown")
            logger.info(f"✅ Subscribed to {channel}")

        elif message_type == "channel_data":
            channel = message.get("channel", "unknown")

            if "trades" in channel:
                self.ws_confirmed_trades[message["contents"]["result"].sequence_number] = message["contents"]["result"]

            if "openOrders" in channel:
                result = message["contents"]["result"]
                # Handle both list and dictionary formats
                if isinstance(result, list):
                    for order_dict in result:
                        order = Order.from_dict(order_dict)
                        assert order is not None
                        self.ws_order_changes[order.order_id] = order

            if "positions" in channel:
                result = message["contents"]["result"]
                # Handle both list and dictionary formats
                if isinstance(result, list):
                    for position in result:
                        if "symbol" in position:
                            self.ws_positions[position["symbol"]] = position
                elif isinstance(result, dict) and "symbol" in result:
                    position = Position.from_dict(result)
                    assert position is not None
                    self.ws_positions[result["symbol"]] = position

        elif message_type == "ping":
            ws.send(json.dumps({"type": "pong"}))

    async def get_current_price(self, symbol: str = "ETHRUSDPERP") -> str:
        """Fetch current market prices"""
        price_info: Price = await self.client.markets.get_price(symbol)
        logger.info(f"Price info: {price_info}")
        current_price = price_info.oracle_price

        if current_price:
            logger.info(f"💰 Current market price for {symbol}: ${current_price:.2f}")
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

    async def get_wallet_perp_executions(self) -> dict[int, PerpExecution]:
        """Get past trades"""
        trades_list: PerpExecutionList = await self.client.wallet.get_wallet_perp_executions(
            address=self.wallet_address
        )

        trade_sequence_number_dict = {}
        for trade in trades_list.data:
            trade_sequence_number_dict[trade.sequence_number] = trade

        logger.debug(f"📊 Current trades: {trade_sequence_number_dict}")
        return trade_sequence_number_dict

    async def get_wallet_perp_execution(self, sequence_number: int) -> PerpExecution:
        """Get a past trade of a specific transaction hash"""
        trades = await self.get_wallet_perp_executions()
        trade = trades.get(int(sequence_number))

        return trade

    async def get_last_wallet_perp_execution(self) -> PerpExecution:
        """Get a past trade of a specific transaction hash"""
        trades = await self.get_wallet_perp_executions()
        last_trade_sequence_number = max(trades.keys())

        return trades[last_trade_sequence_number]

    async def get_market_definition(self, symbol: str) -> Optional[MarketDefinition]:
        """Get market configuration for a specific symbol"""
        markets_config: list[MarketDefinition] = await self.client.reference.get_market_definitions()
        for config in markets_config:
            if config.symbol == symbol:
                return config
        return None

    async def get_open_order(self, id: str) -> Optional[Order]:
        """Get open orders"""
        open_orders = await self.client.get_open_orders()
        for order in open_orders:
            if order.order_id == id:
                return order
        return None

    async def close_exposure(self, symbol: str, fail_if_none: bool = True):
        """Close exposure for a specific market"""
        position: Position = await self.get_position(symbol)

        if position is None or position.qty == 0:
            logger.warning("No position to close")
            if fail_if_none:
                assert False
            return None

        market_price = await self.get_current_price(symbol)
        price_with_offset = 0 if position.side == Side.B else market_price * 2

        order_details = OrderDetails(
            account_id=position.account_id,
            order_type=OrderType.LIMIT,
            symbol=symbol,
            is_buy=not (position.side == Side.B),  # short if position is long
            limit_px=str(price_with_offset),
            qty=str(position.qty),
        )
        logger.debug(f"Order details: {order_details}")

        order_id = await self.create_limit_order(
            symbol=symbol,
            is_buy=order_details.is_buy,
            limit_px=order_details.limit_px,
            qty=order_details.qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )

        # If order_id is None, the IOC order was filled immediately, no need to wait
        # Note: this confirms trade has been registered, not neccesarely position
        if order_id is not None:
            await self.wait_for_trade_confirmation(order_details=order_details, sequence_number=order_id)

        position_after = await self.get_position(symbol)
        if position_after is not None:
            logger.error(f"Failed to close position: {position_after}")
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
            cancelled_order_id = await self.wait_for_order_cancellation_via_rest(order_id=order.order_id, timeout=10)

        assert cancelled_order_id is not None, "Failed to close position"

    async def create_limit_order(
        self,
        symbol: str,
        is_buy: bool,
        limit_px: str,
        qty: str,
        time_in_force: TimeInForce = TimeInForce.IOC,
        reduce_only: Optional[bool] = None,
        expect_error: bool = False,
    ) -> Optional[str]:
        """Create an order with the specified parameters"""
        side_text = "BUY" if is_buy else "SELL"
        time_in_force_text = "IOC" if time_in_force == TimeInForce.IOC else "GTC"

        logger.info(
            f"📤 Creating {time_in_force_text} {side_text} order: symbol={symbol}, price=${limit_px}, qty={qty}"
        )

        # the market_id is only used for signatures
        market_id = (await self.get_market_definition(symbol=symbol)).market_id

        params = LimitOrderParameters(
            symbol=symbol,
            market_id=market_id,
            is_buy=is_buy,
            limit_px=limit_px,
            qty=qty,
            reduce_only=reduce_only,
            time_in_force=time_in_force,
        )

        response = await self.client.create_limit_order(params)

        # Check response format
        logger.info(f"Response: {response}")
        if response is not None:
            return response.order_id

        if expect_error:
            return None

        logger.error(f"❌ Order creation failed: {response}")
        raise RuntimeError(response)

    async def create_tp_order(
        self, symbol: str, is_buy: bool, trigger_px: str, expect_error: bool = False
    ) -> Optional[CreateOrderResponse]:
        """Create an order with the specified parameters"""

        market_definition = await self.get_market_definition(symbol=symbol)
        if not market_definition and expect_error:
            return None
        elif not market_definition:
            raise RuntimeError("Market definition not found for symbol: " + symbol)

        params = TriggerOrderParameters(
            market_id=market_definition.market_id,
            symbol=symbol,
            is_buy=is_buy,
            trigger_px=trigger_px,
            trigger_type=OrderType.TP,
        )

        side_text = "BUY" if is_buy else "SELL"
        logger.info(
            f"📤 Creating TP {side_text} order: market_id={market_definition.market_id}, trigger_px=${trigger_px}"
        )

        response: CreateOrderResponse = await self.client.create_trigger_order(params)

        # Check response format
        if response is not None:
            logger.info(f"✅ TP {side_text} order created with ID: {response.order_id}")
            return response

        if expect_error:
            return None

        logger.error(f"❌ Order creation failed: {response}")
        raise RuntimeError(response)

    async def create_sl_order(
        self, symbol: str, is_buy: bool, trigger_px: str, expect_error: bool = False
    ) -> Optional[CreateOrderResponse]:
        """Create an order with the specified parameters"""
        side_text = "BUY" if is_buy else "SELL"

        market_id = (await self.get_market_definition(symbol=symbol)).market_id
        logger.info(f"📤 Creating SL {side_text} order: market_id={market_id}, trigger_px=${trigger_px}")

        params = TriggerOrderParameters(
            symbol=symbol,
            market_id=market_id,
            is_buy=is_buy,
            trigger_px=trigger_px,
            trigger_type=OrderType.SL,
        )

        response = await self.client.create_trigger_order(params)

        # Check response format
        if response is not None:
            logger.info(f"✅ SL {side_text} order created with ID: {response.order_id}")
            return response

        if expect_error:
            return None

        logger.error(f"❌ Order creation failed: {response}")
        raise RuntimeError(response)

    async def wait_for_trade_confirmation(
        self, order_details: OrderDetails, sequence_number: int, timeout: int = 5
    ) -> Optional[PerpExecution]:
        """Query REST for trade confirmation until timeout"""
        logger.debug("⏳ Waiting for trade confirmation order...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            rest_trade = None
            ws_trade = None
            trades: dict[int, PerpExecution] = await self.get_wallet_perp_executions()
            if sequence_number in trades:
                latest_trade = trades[sequence_number]
                if match_order(order_details, latest_trade):
                    logger.info(f" ✅ Trade confirmed via REST: {latest_trade.sequence_number}")
                    rest_trade = latest_trade

            if sequence_number in self.ws_confirmed_trades:
                latest_trade = self.ws_confirmed_trades[sequence_number]
                if match_order(order_details, latest_trade):
                    logger.info(f" ✅ Trade confirmed via WS: {latest_trade.sequence_number}")
                    ws_trade = latest_trade

            if rest_trade and ws_trade:
                assert rest_trade.to_str() == ws_trade.to_str()
                return rest_trade

            await asyncio.sleep(0.5)

        return None

    async def wait_for_order_cancellation_via_rest(self, order_id: str, timeout: int = 5) -> Optional[str]:
        """Query REST for order cancellation until timeout"""
        logger.debug("⏳ Waiting for order cancellation...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            orders: list[Order] = await self.client.get_open_orders()
            orders_ids = [order.order_id for order in orders]
            if order_id not in orders_ids:
                logger.info(f" ✅ Order cancelled: {order_id}")
                return order_id

            await asyncio.sleep(0.5)

        return None

    async def wait_for_order_creation_via_rest(self, order_id: str, timeout: int = 5) -> Optional[Order]:
        """Query REST for order creation until timeout"""
        logger.debug("⏳ Waiting for order creation...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            orders = await self.client.get_open_orders()
            logger.info(f"Orders: {orders}")
            for order in orders:
                if order.order_id == order_id:
                    logger.info(f" ✅ Order created: {order}")
                    return order

            await asyncio.sleep(0.5)

        return None

    """async def wait_for_order_status_update_via_ws(
        self, order_id: str, expected_status: OrderStatus, timeout: int = 5
    ) -> Optional[dict]:
        if not self.websocket:
            logger.warning("WebSocket not connected, can't listen to order updates")
            return None
        logger.debug("⏳ Waiting for order status update...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            order = self.ws_order_changes.get(order_id)
            logger.info(f"Order status: {self.ws_order_changes}")
            if order:
                logger.info(f"Order status: {order}")
                if order["status"] == expected_status:
                    logger.info(f" ✅ Order status updated to {expected_status}: {order}")
                    return order

            await asyncio.sleep(0.5)

        return None

    async def wait_for_execution_via_ws(
        self, order_id: str, expected_status: OrderStatus, timeout: int = 5
    ) -> Optional[dict]:
        if self.websocket is None:
            logger.warning("WebSocket not connected, can't listen to execution updates")
            return None

        logger.debug("⏳ Waiting for execution...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # TODO: Implement actual WebSocket message handling
            # This should listen for order execution updates from the WebSocket
            # and check if the order_id matches and status equals expected_status
            await asyncio.sleep(0.5)

        logger.debug(f"⏱️ Timeout reached after {timeout}s waiting for execution")
        return None"""

    # VALIDATION

    async def check_open_order_created(self, order_id: str, order_details: OrderDetails):
        open_order = await self.get_open_order(order_id)
        assert open_order is not None, "check_open_order_created: GTC order was not found"
        assert open_order.order_id == order_id, "check_open_order_created: Wrong order id"
        if order_details.order_type == OrderType.LIMIT:
            assert open_order.limit_px == order_details.limit_px, "check_open_order_created: Wrong limit price"
            assert open_order.qty == order_details.qty, "check_open_order_created: Wrong qty"
        else:
            assert open_order.trigger_px == order_details.limit_px, "check_open_order_created: Wrong trigger price"
            assert open_order.qty is None, "check_open_order_created: Has qty"

        assert open_order.order_type == order_details.order_type, "check_open_order_created: Wrong order type"
        assert (open_order.side == Side.B) == order_details.is_buy, "check_open_order_created: Wrong order direction"

        assert open_order.status == OrderStatus.OPEN, "check_open_order_created: Wrong order status"

    async def check_no_open_orders(self):
        open_orders = await self.client.get_open_orders()
        assert len(open_orders) == 0, "check_no_open_orders: Open orders should be empty"

    async def check_order_not_open(self, order_id: str):
        open_order = await self.get_open_order(order_id)
        assert open_order is None, "check_order_not_open: Open order should be empty"

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

        ws_position = self.ws_positions[position.symbol]
        assert position.to_str() == ws_position.to_str()

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
        assert ws_position is None, "check_position_not_open: WebSocket position should be empty"

    async def check_order_execution(self, order_details: OrderDetails) -> PerpExecution:
        order_execution = await self.get_last_wallet_perp_execution()

        assert order_execution is not None, "check_order_execution: No order execution found"
        assert (
            order_execution.exchange_id == self.client.config.dex_id
        ), "check_order_execution: Exchange ID does not match"
        assert (
            order_execution.symbol == order_details.symbol
        ), "check_order_execution: Order execution symbol does not match"
        assert (
            order_execution.account_id == order_details.account_id
        ), "check_order_execution: Order execution account ID does not match"
        assert order_execution.qty == order_details.qty, "check_order_execution: Order execution qty does not match"
        assert (
            order_execution.side == Side.B if order_details.is_buy else Side.A
        ), "check_order_execution: Order execution side does not match"
        assert (
            order_execution.type == ExecutionType.ORDER_MATCH
        ), "check_order_execution: Order execution type does not match"
        if order_details.order_type == OrderType.LIMIT:
            assert order_details.limit_px is not None
            if order_details.is_buy:
                assert float(order_execution.price) <= float(
                    order_details.limit_px
                ), "check_order_execution: Order execution price does not match"
            else:
                assert float(order_execution.price) >= float(
                    order_details.limit_px
                ), "check_order_execution: Order execution price does not match"
        return order_execution

    async def check_no_order_execution_since(self, since_timestamp_ms: int):
        order_execution = await self.get_last_wallet_perp_execution()
        if order_execution is not None:
            assert (
                order_execution.timestamp < since_timestamp_ms
            ), "check_no_order_execution_since: Order execution should be empty"
