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
from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.price import Price
from sdk.open_api.models.side import Side
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters
from sdk.reya_websocket import ReyaSocket
from tests.helpers.utils import match_order, match_spot_order

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
        self.ws_last_spot_execution: Optional[SpotExecution] = None
        self.ws_order_changes: dict[str, Order] = {}
        self.ws_positions: dict[str, Position] = {}
        self.ws_balances: dict[str, AccountBalance] = {}  # key: asset
        self.ws_balance_updates: list[AccountBalance] = []  # Track all balance updates
        self.ws_current_prices: dict[str, Price] = {}
        self.ws_last_depth: dict[str, dict] = {}  # key: symbol, value: depth snapshot

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
        await asyncio.sleep(0.3)

        await self.close_active_orders(fail_if_none=False)
        await self.close_exposures(fail_if_none=False)

    def _on_websocket_open(self, ws):
        """Handle WebSocket connection open"""
        logger.info("WebSocket opened, subscribing to trade feeds")

        # Subscribe to trades for our wallet (both perp and spot)
        ws.wallet.perp_executions(self.owner_wallet_address).subscribe()
        ws.wallet.spot_executions(self.owner_wallet_address).subscribe()
        ws.wallet.order_changes(self.owner_wallet_address).subscribe()
        ws.wallet.positions(self.owner_wallet_address).subscribe()
        ws.wallet.balances(self.owner_wallet_address).subscribe()

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

            if "spotExecutions" in channel:
                for e in message["data"]:
                    execution = SpotExecution.from_dict(e)
                    assert execution is not None
                    self.ws_last_spot_execution = execution

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

            if "balances" in channel or "accountBalances" in channel:
                for b in message["data"]:
                    balance = AccountBalance.from_dict(b)
                    assert balance is not None
                    self.ws_balances[balance.asset] = balance
                    self.ws_balance_updates.append(balance)  # Track all updates
                    logger.debug(f"Added balance update: account_id={balance.account_id}, asset={balance.asset}")

            if "depth" in channel:
                # Market depth updates - format: /v2/market/{symbol}/depth
                depth_data = message["data"]
                # Extract symbol from channel path
                symbol = channel.split("/")[3]  # e.g., "/v2/market/WETHRUSD/depth" -> "WETHRUSD"
                self.ws_last_depth[symbol] = depth_data

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

    async def get_last_wallet_spot_execution(self) -> SpotExecution:
        """Get the most recent spot execution for this wallet"""
        executions_list: SpotExecutionList = await self.client.wallet.get_wallet_spot_executions(
            address=self.owner_wallet_address
        )

        return executions_list.data[0]

    async def get_balances(self) -> dict[str, AccountBalance]:
        """Get current account balances for this tester's account only"""
        balances_list: list[AccountBalance] = await self.client.get_account_balances()

        balance_dict = {}
        for balance in balances_list:
            # Only include balances for this tester's account_id
            if balance.account_id == self.account_id:
                asset = balance.asset
                if asset:
                    balance_dict[asset] = balance

        return balance_dict

    async def get_balance(self, asset: str) -> Optional[AccountBalance]:
        """Get balance for a specific asset"""
        balances = await self.get_balances()
        return balances.get(asset)

    def clear_balance_updates(self) -> None:
        """Clear the list of balance updates received via WebSocket"""
        self.ws_balance_updates.clear()
        logger.debug("Cleared WebSocket balance updates")

    def get_balance_updates_for_account(self, account_id: int) -> list[AccountBalance]:
        """Get all balance updates for a specific account"""
        return [b for b in self.ws_balance_updates if b.account_id == account_id]

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
        Verify that balance changes for a spot trade match expected amounts.

        Args:
            maker_account_id: Maker account ID
            taker_account_id: Taker account ID
            maker_initial_balances: Maker balances before trade
            maker_final_balances: Maker balances after trade
            taker_initial_balances: Taker balances before trade
            taker_final_balances: Taker balances after trade
            base_asset: Base asset symbol (e.g., "ETH")
            quote_asset: Quote asset symbol (e.g., "RUSD")
            qty: Trade quantity as string
            price: Trade price as string
            is_maker_buyer: True if maker is buying (taker is selling), False otherwise
        """
        qty_float = float(qty)
        price_float = float(price)
        notional = qty_float * price_float

        logger.info("\n💰 Verifying spot trade balance changes...")
        logger.info(f"Trade: qty={qty} {base_asset} at price={price} {quote_asset}")
        logger.info(f"Notional: {notional} {quote_asset}")
        logger.info(f"Maker is {'BUYER' if is_maker_buyer else 'SELLER'}")

        # Get balances (dict keys are assets, but values might be for different accounts if wallet is shared)
        # For shared wallets, get_balances returns dict with latest balance for each asset
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

        logger.info(f"Maker {base_asset} change: {maker_base_change:.6f} (expected: {'+' if is_maker_buyer else '-'}{qty_float:.6f})")
        logger.info(f"Maker {quote_asset} change: {maker_quote_change:.6f} (expected: {'-' if is_maker_buyer else '+'}{notional:.6f})")
        logger.info(f"Taker {base_asset} change: {taker_base_change:.6f} (expected: {'-' if is_maker_buyer else '+'}{qty_float:.6f})")
        logger.info(f"Taker {quote_asset} change: {taker_quote_change:.6f} (expected: {'+' if is_maker_buyer else '-'}{notional:.6f})")

        # Allow 0.1% tolerance for fees and precision
        tolerance = 0.001

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

    async def get_market_depth(self, symbol: str) -> dict:
        """Get L2 market depth (orderbook) for a given symbol via REST API"""
        return await self.client.get_market_depth(symbol)

    def subscribe_to_market_depth(self, symbol: str) -> None:
        """Subscribe to L2 market depth updates for a specific symbol via WebSocket"""
        if self.websocket is None:
            raise RuntimeError("WebSocket not connected - call setup() first")
        self.websocket.market.depth(symbol).subscribe()
        logger.info(f"Subscribed to market depth for {symbol}")

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
        try:
            positions = await self.get_positions()
        except Exception as e:
            logger.warning(f"Failed to get positions (API may not have market trackers in Redis): {e}")
            if fail_if_none:
                logger.warning("Ignoring positions error since fail_if_none=True means we don't require positions to exist")
            return None

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
            await asyncio.sleep(0.2)

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
            try:
                await self.client.cancel_order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    account_id=order.account_id
                )

                # Note: this confirms trade has been registered, not neccesarely position
                cancelled_order_id = await self.wait_for_order_state(
                    order_id=order.order_id, expected_status=OrderStatus.CANCELLED, timeout=10
                )
            except Exception as e:
                logger.warning(f"Failed to cancel order {order.order_id}: {e}")
                # If the order doesn't exist, that's fine for cleanup purposes
                continue

        if fail_if_none and cancelled_order_id is None:
            assert False, "Failed to close position"

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

            await asyncio.sleep(0.1)

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

            await asyncio.sleep(0.1)

        raise RuntimeError(f"Order not executed after {timeout} seconds, rest_trade: {rest_trade is not None}, ws_trade: {ws_trade is not None}, rest_closed: {rest_closed}, ws_position: {ws_position is not None}")

    async def wait_for_spot_execution(self, expected_order: Order, timeout: int = 10) -> SpotExecution:
        """Wait for spot execution confirmation via both REST and WebSocket"""
        logger.info("⏳ Waiting for spot execution confirmation...")

        start_time = time.time()
        rest_execution = None
        ws_execution = None

        while time.time() - start_time < timeout:
            # Check WebSocket for execution
            if ws_execution is None and self.ws_last_spot_execution is not None:
                if match_spot_order(expected_order, self.ws_last_spot_execution):
                    elapsed_time = time.time() - start_time
                    logger.info(
                        f" ✅ Spot execution confirmed via WS: {self.ws_last_spot_execution.order_id} (took {elapsed_time:.2f}s)"
                    )
                    ws_execution = self.ws_last_spot_execution

            # Check REST API for execution matching the WS execution
            if rest_execution is None and ws_execution is not None:
                # Get all recent executions and search for one matching the WS execution
                executions_list: SpotExecutionList = await self.client.wallet.get_wallet_spot_executions(
                    address=self.owner_wallet_address
                )
                for execution in executions_list.data:
                    if execution.order_id == ws_execution.order_id:
                        elapsed_time = time.time() - start_time
                        logger.info(f" ✅ Spot execution confirmed via REST: {execution.order_id} (took {elapsed_time:.2f}s)")
                        rest_execution = execution
                        break

            if rest_execution and ws_execution:
                assert (
                    rest_execution.to_str() == ws_execution.to_str()
                ), f"expected {rest_execution.to_str()} to equal {ws_execution.to_str()}"
                return rest_execution

            await asyncio.sleep(0.1)

        raise RuntimeError(
            f"Spot execution not confirmed after {timeout} seconds, rest: {rest_execution is not None}, ws: {ws_execution is not None}"
        )

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

            await asyncio.sleep(0.1)

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

            await asyncio.sleep(0.1)

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
            # Compare prices as floats to handle "3996.0" vs "3996" formatting differences
            assert float(open_order.limit_px) == float(expected_order.limit_px), f"check_open_order_created: Wrong limit price. Expected: {expected_order.limit_px}, Got: {open_order.limit_px}"
            assert float(open_order.qty) == float(expected_order.qty), f"check_open_order_created: Wrong qty. Expected: {expected_order.qty}, Got: {open_order.qty}"
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
        if len(open_orders) == 0:
            return

        logger.warning(f"check_no_open_orders: Found {len(open_orders)} open orders from database, checking if they're stale:")

        # Filter out stale orders (orders that don't exist in matching engine)
        legitimate_orders = []
        for order in open_orders:
            logger.warning(f"  - Checking order ID: {order.order_id}, Symbol: {order.symbol}, Status: {order.status}")
            try:
                await self.client.cancel_order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    account_id=order.account_id
                )
                # If cancel succeeded, this is a legitimate order that we need to wait for
                logger.warning(f"Order {order.order_id} exists in matching engine, waiting for cancellation...")
                legitimate_orders.append(order)
            except Exception as e:
                # If cancel fails because order doesn't exist in matching engine, it's a stale DB record
                if "Missing order" in str(e):
                    logger.info(f"Order {order.order_id} is stale (doesn't exist in matching engine), ignoring")
                else:
                    logger.warning(f"Unexpected error cancelling order {order.order_id}: {e}")
                    legitimate_orders.append(order)

        if len(legitimate_orders) == 0:
            logger.info("check_no_open_orders: All orders are stale, test can proceed")
            return

        # Wait for legitimate orders to be cancelled
        logger.warning(f"Waiting for {len(legitimate_orders)} legitimate orders to be cancelled...")
        await asyncio.sleep(0.3)

        # Check again
        remaining_orders = await self.client.get_open_orders()
        # Filter out stale orders again
        remaining_legitimate = []
        for order in remaining_orders:
            try:
                await self.client.cancel_order(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    account_id=order.account_id
                )
            except Exception as e:
                if "Missing order" not in str(e):
                    remaining_legitimate.append(order)

        if len(remaining_legitimate) > 0:
            logger.error(f"check_no_open_orders: Still found {len(remaining_legitimate)} legitimate open orders:")
            for order in remaining_legitimate:
                logger.error(f"  - Order ID: {order.order_id}, Symbol: {order.symbol}, Status: {order.status}")
            assert False, "check_no_open_orders: Open orders should be empty"
        else:
            logger.info("check_no_open_orders: All legitimate orders cleaned up successfully")

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

    async def check_spot_execution(
        self, spot_execution: SpotExecution, expected_order: Order, expected_qty: Optional[str] = None
    ) -> SpotExecution:
        """Validate spot execution details"""
        assert spot_execution is not None, "check_spot_execution: No spot execution found"
        assert (
            spot_execution.exchange_id == self.client.config.dex_id
        ), "check_spot_execution: Exchange ID does not match"
        assert (
            spot_execution.symbol == expected_order.symbol
        ), "check_spot_execution: Symbol does not match"
        assert (
            spot_execution.account_id == expected_order.account_id
        ), "check_spot_execution: Account ID does not match"
        assert (
            spot_execution.qty == (expected_order.qty if expected_qty is None else expected_qty)
        ), "check_spot_execution: Quantity does not match"
        assert spot_execution.side == expected_order.side, "check_spot_execution: Side does not match"
        assert (
            spot_execution.type == ExecutionType.ORDER_MATCH
        ), "check_spot_execution: Execution type does not match"
        if expected_order.order_type == OrderType.LIMIT and expected_order.limit_px is not None:
            if expected_order.side == Side.B:  # Buy
                assert float(spot_execution.price) <= float(
                    expected_order.limit_px
                ), "check_spot_execution: Execution price should be <= limit price for buy"
            else:  # Sell
                assert float(spot_execution.price) >= float(
                    expected_order.limit_px
                ), "check_spot_execution: Execution price should be >= limit price for sell"
        return spot_execution

    async def check_balance(
        self,
        asset: str,
        expected_account_id: int,
        expected_min_balance: Optional[str] = None,
        expected_max_balance: Optional[str] = None,
    ):
        """Check account balance for a specific asset"""
        balance = await self.get_balance(asset)
        if balance is None:
            raise RuntimeError(f"check_balance: Balance not found for asset {asset}")

        if expected_account_id is not None:
            assert balance.account_id == expected_account_id, "check_balance: Account ID does not match"

        if expected_min_balance is not None:
            assert float(balance.real_balance) >= float(
                expected_min_balance
            ), f"check_balance: Balance {balance.real_balance} should be >= {expected_min_balance}"

        if expected_max_balance is not None:
            assert float(balance.real_balance) <= float(
                expected_max_balance
            ), f"check_balance: Balance {balance.real_balance} should be <= {expected_max_balance}"

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
        await asyncio.sleep(0.3)

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
        await asyncio.sleep(0.5)

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

