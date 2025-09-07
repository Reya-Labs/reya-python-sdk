from typing import Optional

import asyncio
import json
import logging
import os
import time

import pytest

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
from sdk.tests.models import OrderDetails
from sdk.tests.utils import match_order

# Set up logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

logger = logging.getLogger("reya.integration_tests")


class ReyaTester:
    """Helper class for integration tests with the Reya SDK"""

    def __init__(
        self,
        private_key: Optional[str] = None,
        api_url: Optional[str] = None,
        chain_id: Optional[int] = None,
        account_id: Optional[int] = None,
        wallet_address: Optional[str] = None,
    ):
        # Initialize the REST API client with provided or env-based config
        self.client = ReyaTradingClient(
            private_key=private_key,
            api_url=api_url,
            chain_id=chain_id,
            account_id=account_id,
            wallet_address=wallet_address,
        )

        self.wallet_address = self.client.wallet_address
        self.account_id = self.client.config.account_id
        self.chain_id = self.client.config.chain_id

        # For WebSocket integration
        self.websocket = None
        self.confirmed_trades = []
        self.orders = {}
        self.positions = {}
        self.current_prices = {}

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
        ws.wallet.trades(self.wallet_address).subscribe()
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
                self.confirmed_trades.append(message["contents"]["result"])

            if "openOrders" in channel:
                result = message["contents"]["result"]
                # Handle both list and dictionary formats
                if isinstance(result, list):
                    for order in result:
                        if "id" in order:
                            self.orders[order["id"]] = order
                elif isinstance(result, dict) and "id" in result:
                    self.orders[result["id"]] = result

            if "positions" in channel:
                result = message["contents"]["result"]
                # Handle both list and dictionary formats
                if isinstance(result, list):
                    for position in result:
                        if "market_id" in position:
                            self.positions[position["market_id"]] = position
                elif isinstance(result, dict) and "market_id" in result:
                    self.positions[result["market_id"]] = result

        elif message_type == "ping":
            ws.send(json.dumps({"type": "pong"}))

    def get_current_price(self, symbol: str = "ETHRUSDPERP") -> Optional[float]:
        """Fetch current market prices"""
        price_info: Price = self.client.markets.get_price(symbol)
        logger.info(f"Price info: {price_info}")
        self.current_price = float(price_info.oracle_price)

        if self.current_price:
            logger.info(f"💰 Current market price for {symbol}: ${self.current_price:.2f}")
            return self.current_price
        else:
            logger.info(f"❌ Current market price for {symbol} not found")
            return None

    def get_positions(self) -> dict[str, Position]:
        """Get current positions"""
        positions_list: list[Position] = self.client.get_positions()

        position_summary = {}
        for position in positions_list:
            symbol = position.symbol
            qty = position.qty
            if symbol and qty:
                position_summary[symbol] = position

        return position_summary

    def get_position(self, symbol: str) -> Optional[Position]:
        """Get current position for a specific market"""
        positions = self.get_positions()
        position = positions.get(symbol)
        if position is None:
            return None

        return position

    def get_wallet_perp_executions(self) -> dict[int, PerpExecution]:
        """Get past trades"""
        trades_list: PerpExecutionList = self.client.wallet.get_wallet_perp_executions(address=self.wallet_address)

        trade_sequence_number_dict = {}
        for trade in trades_list.data:
            trade_sequence_number_dict[trade.sequence_number] = trade

        logger.debug(f"📊 Current trades: {trade_sequence_number_dict}")
        return trade_sequence_number_dict

    def get_wallet_perp_execution(self, sequence_number: int) -> PerpExecution:
        """Get a past trade of a specific transaction hash"""
        trades = self.get_wallet_perp_executions()
        trade = trades.get(int(sequence_number))

        return trade

    def get_last_wallet_perp_execution(self) -> PerpExecution:
        """Get a past trade of a specific transaction hash"""
        trades = self.get_wallet_perp_executions()
        last_trade_sequence_number = max(trades.keys())

        return trades[last_trade_sequence_number]

    def get_market_definition(self, symbol: str) -> Optional[MarketDefinition]:
        """Get market configuration for a specific symbol"""
        markets_config: list[MarketDefinition] = self.client.reference.get_market_definitions()
        for config in markets_config:
            if config.symbol == symbol:
                return config
        return None

    def get_open_order(self, id: str) -> Optional[Order]:
        """Get open orders"""
        open_orders = self.client.get_open_orders()
        for order in open_orders:
            if order.order_id == id:
                return order
        return None

    async def close_exposure(self, symbol: str, fail_if_none: bool = True) -> Optional[str]:
        """Close exposure for a specific market"""
        position: Position = self.get_position(symbol)

        if position is None or position.qty == 0:
            logger.warning("No position to close")
            if fail_if_none:
                assert False
            return None

        market_price = self.get_current_price(symbol)
        price_with_offset = 0 if position.side == Side.B else market_price * 2

        order_details = OrderDetails(
            account_id=position.account_id,
            order_type=OrderType.LIMIT,
            symbol=symbol,
            is_buy=not (position.side == Side.B),  # short if position is long
            price=str(price_with_offset),
            qty=str(position.qty),
        )
        logger.debug(f"Order details: {order_details}")

        sequence_number = self.create_order(
            symbol=symbol,
            is_buy=order_details.is_buy,
            price=order_details.price,
            qty=order_details.qty,
            time_in_force=TimeInForce.IOC,
            reduce_only=True,
        )

        # Note: this confirms trade has been registered, not neccesarely position
        await self.wait_for_trade_confirmation_via_rest(order_details=order_details, sequence_number=sequence_number)

        position_after = self.get_position(symbol)
        if position_after is not None:
            logger.error(f"Failed to close position: {position_after}")
            assert False

    async def close_active_orders(self, fail_if_none: bool = True) -> Optional[str]:
        """Close exposure for a specific market"""
        active_orders: list[Order] = self.client.get_open_orders()

        if active_orders is None or len(active_orders) == 0:
            logger.warning("No active orders to close")
            if fail_if_none:
                assert False
            return None

        for order in active_orders:
            self.client.cancel_order(order_id=order.order_id)

            # Note: this confirms trade has been registered, not neccesarely position
            cancelled_order_id = await self.wait_for_order_cancellation_via_rest(order_id=order.order_id, timeout=10)
        assert cancelled_order_id is not None, "Failed to close position"

    def create_order(
        self,
        symbol: str,
        is_buy: bool,
        price: str,
        qty: str,
        time_in_force: TimeInForce = TimeInForce.IOC,
        reduce_only: Optional[bool] = None,
        expect_error: bool = False,
    ) -> Optional[str]:
        """Create an order with the specified parameters"""
        side_text = "BUY" if is_buy else "SELL"
        time_in_force_text = "IOC" if time_in_force == TimeInForce.IOC else "GTC"

        logger.info(f"📤 Creating {time_in_force_text} {side_text} order: symbol={symbol}, price=${price}, qty={qty}")

        # the market_id is only used for signatures
        market_id = (self.get_market_definition(symbol=symbol)).market_id

        params = LimitOrderParameters(
            symbol=symbol,
            market_id=market_id,
            is_buy=is_buy,
            price=price,
            qty=qty,
            reduce_only=reduce_only,
            time_in_force=time_in_force,
        )

        response = self.client.create_limit_order(params)

        # Check response format
        logger.info(f"Response: {response}")
        if response is not None:
            return "ioc" if response.order_id is None else response.order_id

        if not expect_error:
            logger.error(f"❌ Order creation failed: {response}")
        raise Exception(response)

    def create_tp_order(
        self, symbol: str, is_buy: bool, trigger_price: str, expect_error: bool = False
    ) -> Optional[CreateOrderResponse]:
        """Create an order with the specified parameters"""

        market_id = (self.get_market_definition(symbol=symbol)).market_id
        params = TriggerOrderParameters(
            market_id=market_id,
            symbol=symbol,
            is_buy=is_buy,
            trigger_price=trigger_price,
            trigger_type=OrderType.TP,
        )

        side_text = "BUY" if is_buy else "SELL"
        logger.info(f"📤 Creating TP {side_text} order: market_id={market_id}, trigger_price=${trigger_price}")

        response: CreateOrderResponse = self.client.create_trigger_order(params)

        # Check response format
        if response is not None:
            logger.info(f"✅ TP {side_text} order created with ID: {response.order_id}")
            return response

    def create_sl_order(
        self, symbol: str, is_buy: bool, trigger_price: str, expect_error: bool = False
    ) -> Optional[CreateOrderResponse]:
        """Create an order with the specified parameters"""
        side_text = "BUY" if is_buy else "SELL"

        market_id = (self.get_market_definition(symbol=symbol)).market_id
        logger.info(f"📤 Creating SL {side_text} order: market_id={market_id}, trigger_price=${trigger_price}")

        params = TriggerOrderParameters(
            symbol=symbol,
            market_id=market_id,
            is_buy=is_buy,
            trigger_price=trigger_price,
            trigger_type=OrderType.SL,
        )

        response = self.client.create_trigger_order(params)

        # Check response format
        if response is not None:
            logger.info(f"✅ SL {side_text} order created with ID: {response.order_id}")
            return response

    async def wait_for_trade_confirmation_via_rest(
        self, order_details: OrderDetails, sequence_number: int, timeout: int = 5
    ) -> Optional[PerpExecution]:
        """Query REST for trade confirmation until timeout"""
        logger.debug("⏳ Waiting for trade confirmation order...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            trades: dict[int, PerpExecution] = self.get_wallet_perp_executions()
            if sequence_number in trades.keys():
                latest_trade = trades[sequence_number]
                if match_order(order_details, latest_trade):
                    logger.info(f" ✅ Trade confirmed: {latest_trade.sequence_number}")
                    return latest_trade

            await asyncio.sleep(0.5)

        return None

    async def wait_for_order_cancellation_via_rest(self, order_id: str, timeout: int = 5) -> Optional[str]:
        """Query REST for order cancellation until timeout"""
        logger.debug("⏳ Waiting for order cancellation...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            orders: list[Order] = self.client.get_open_orders()
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
            orders = self.client.get_open_orders()
            logger.info(f"Orders: {orders}")
            for order in orders:
                logger.info(f"Order: {order.order_id} jdhfhfhhfhf {order_id}")
                if order.order_id == order_id:
                    logger.info(f" ✅ Order created: {order}")
                    return order

            await asyncio.sleep(0.5)

        return None

    # Note: the openOrders WS does not update on filled/cancelled/rejected
    async def wait_for_order_status_update_via_WS(
        self, order_id: str, expected_status: str = "pending", timeout: int = 5
    ) -> Optional[dict]:
        """Wait for order status update until timeout"""
        if not self.websocket:
            logger.warning("WebSocket not connected, can't listen to order updates")
            return None
        logger.debug("⏳ Waiting for order status update...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check if we have a new confirmed trade
            order = self.orders.get(order_id)
            logger.info(f"Order status: {self.orders}")
            if order:
                logger.info(f"Order status: {order}")
                if order["status"] == expected_status:
                    logger.info(f" ✅ Order status updated to {expected_status}: {order}")
                    return order

            await asyncio.sleep(0.5)

        return None

    # VALIDATION

    def check_open_order_created(self, order_id: str, order_details: OrderDetails):
        open_order = self.get_open_order(order_id)
        assert open_order is not None, "check_open_order_created: GTC order was not found"
        assert open_order.order_id == order_id, "check_open_order_created: Wrong order id"
        if order_details.order_type == OrderType.LIMIT:
            assert float(open_order.limit_px) == float(
                order_details.price
            ), "check_open_order_created: Wrong limit price"
            assert float(open_order.qty) == float(order_details.qty), "check_open_order_created: Wrong qty"
        else:
            assert float(open_order.trigger_px) == float(
                order_details.price
            ), "check_open_order_created: Wrong trigger price"
            assert open_order.qty is None, "check_open_order_created: Has qty"

        assert open_order.order_type == order_details.order_type, "check_open_order_created: Wrong order type"
        assert (open_order.side == Side.B) == order_details.is_buy, "check_open_order_created: Wrong order direction"

        assert open_order.status == OrderStatus.OPEN, "check_open_order_created: Wrong order status"
        return

    def check_no_open_orders(self):
        open_orders = self.client.get_open_orders()
        assert len(open_orders) == 0, "check_no_open_orders: Open orders should be empty"
        return

    def check_order_not_open(self, order_id: str):
        open_order = self.get_open_order(order_id)
        assert open_order is None, "check_order_not_open: Open order should be empty"
        return

    def check_position(
        self,
        symbol: str,
        expected_exchange_id: str | None = None,
        expected_account_id: str | None = None,
        expected_qty: str | None = None,
        expected_side: Side | None = None,
        expected_avg_entry_price: str | None = None,
        expected_last_trade_sequence_number: int | None = None,
    ):
        position = self.get_position(symbol)
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
        return

    def check_position_not_open(self, symbol: str):
        position = self.get_position(symbol)
        assert position is None, "check_position_not_open: Position should be empty"
        return

    def check_order_execution(self, order_details: OrderDetails) -> PerpExecution:
        order_execution = self.get_last_wallet_perp_execution()

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
            if order_details.is_buy:
                assert float(order_execution.price) <= float(
                    order_details.price
                ), "check_order_execution: Order execution price does not match"
            else:
                assert float(order_execution.price) >= float(
                    order_details.price
                ), "check_order_execution: Order execution price does not match"
        return order_execution

    def check_no_order_execution_since(self, since_timestamp_ms: int):
        order_execution = self.get_last_wallet_perp_execution()
        if order_execution is not None:
            assert (
                order_execution.timestamp < since_timestamp_ms
            ), "check_no_order_execution_since: Order execution should be empty"
        return
