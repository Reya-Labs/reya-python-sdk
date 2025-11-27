"""Main ReyaTester class with composition-based architecture."""

from typing import Optional
import asyncio
import logging
import os

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_websocket import ReyaSocket

from .data import DataOperations
from .orders import OrderOperations
from .positions import PositionOperations
from .waiters import Waiters
from .checks import Checks
from .websocket import WebSocketState

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("reya.integration_tests")


class ReyaTester:
    """
    Integration test helper with composition-based architecture.
    
    Usage:
        tester = ReyaTester()
        await tester.setup()
        
        # Data operations
        price = await tester.data.current_price("ETHRUSDPERP")
        positions = await tester.data.positions()
        
        # Order operations
        order_id = await tester.orders.create_limit(params)
        await tester.orders.close_all()
        
        # Position operations
        await tester.positions.setup(symbol="ETHRUSDPERP", is_buy=True)
        await tester.positions.close(symbol)
        
        # Wait operations
        await tester.wait.for_order_state(order_id, OrderStatus.FILLED)
        await tester.wait.for_spot_execution(expected_order)
        
        # Check/assertion operations
        await tester.check.no_open_orders()
        await tester.check.position(symbol, expected_side=Side.B, ...)
        tester.check.ws_order_change_received(order_id, ...)
        
        # WebSocket state
        tester.ws.order_changes  # dict of order changes
        tester.ws.last_spot_execution  # last spot execution
        tester.ws.get_balance_update_count()
    """

    def __init__(self):
        # Initialize the REST API client
        self.client = ReyaTradingClient()
        assert self.client.owner_wallet_address is not None
        assert self.client.config.account_id is not None
        assert self.client.config.chain_id is not None

        self.owner_wallet_address: str = self.client.owner_wallet_address
        self.account_id: int = self.client.config.account_id
        self.chain_id: int = self.client.config.chain_id

        # Internal WebSocket reference
        self._websocket: Optional[ReyaSocket] = None

        # Composed components
        self.ws = WebSocketState(self)
        self.data = DataOperations(self)
        self.orders = OrderOperations(self)
        self.positions = PositionOperations(self)
        self.wait = Waiters(self)
        self.check = Checks(self)

    async def setup(self) -> None:
        """Set up WebSocket connection for trade monitoring."""
        ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")
        await self.client.start()

        self._websocket = ReyaSocket(
            url=ws_url,
            on_open=self.ws.on_open,
            on_message=self.ws.on_message,
        )

        self._websocket.connect()
        logger.info("WebSocket connected for trade monitoring")

        await asyncio.sleep(0.05)

        await self.orders.close_all(fail_if_none=False)
        await self.positions.close_all(fail_if_none=False)

    async def close(self) -> None:
        """Close all connections."""
        if self._websocket:
            self._websocket.close()
        await self.client.close()

    # ==========================================================================
    # Backward compatibility properties/methods
    # These allow existing tests to work without changes during migration
    # ==========================================================================

    @property
    def websocket(self) -> Optional[ReyaSocket]:
        """Backward compatibility: access to raw websocket."""
        return self._websocket

    @property
    def ws_last_trade(self):
        """Backward compatibility."""
        return self.ws.last_trade

    @ws_last_trade.setter
    def ws_last_trade(self, value):
        """Backward compatibility."""
        self.ws.last_trade = value

    @property
    def ws_last_spot_execution(self):
        """Backward compatibility."""
        return self.ws.last_spot_execution

    @ws_last_spot_execution.setter
    def ws_last_spot_execution(self, value):
        """Backward compatibility."""
        self.ws.last_spot_execution = value

    @property
    def ws_order_changes(self):
        """Backward compatibility."""
        return self.ws.order_changes

    @property
    def ws_positions(self):
        """Backward compatibility."""
        return self.ws.positions

    @property
    def ws_balances(self):
        """Backward compatibility."""
        return self.ws.balances

    @property
    def ws_balance_updates(self):
        """Backward compatibility."""
        return self.ws.balance_updates

    @property
    def ws_current_prices(self):
        """Backward compatibility."""
        return self.ws.current_prices

    @property
    def ws_last_depth(self):
        """Backward compatibility."""
        return self.ws.last_depth

    # Backward compatibility methods - delegate to composed components
    async def get_current_price(self, symbol: str = "ETHRUSDPERP") -> str:
        return await self.data.current_price(symbol)

    async def get_positions(self):
        return await self.data.positions()

    async def get_position(self, symbol: str):
        return await self.data.position(symbol)

    async def get_last_wallet_perp_execution(self):
        return await self.data.last_perp_execution()

    async def get_last_wallet_spot_execution(self):
        return await self.data.last_spot_execution()

    async def get_balances(self):
        return await self.data.balances()

    async def get_balance(self, asset: str):
        return await self.data.balance(asset)

    def clear_balance_updates(self):
        self.ws.clear_balance_updates()

    def get_balance_updates_for_account(self, account_id: int):
        return self.ws.get_balance_updates_for_account(account_id)

    def verify_spot_trade_balance_changes(self, *args, **kwargs):
        return self.ws.verify_spot_trade_balance_changes(*args, **kwargs)

    async def get_market_depth(self, symbol: str):
        return await self.data.market_depth(symbol)

    def subscribe_to_market_depth(self, symbol: str):
        self.ws.subscribe_to_market_depth(symbol)

    async def get_market_definition(self, symbol: str):
        return await self.data.market_definition(symbol)

    async def get_open_order(self, order_id: str):
        return await self.data.open_order(order_id)

    async def close_exposures(self, fail_if_none: bool = True):
        return await self.positions.close_all(fail_if_none)

    async def close_active_orders(self, fail_if_none: bool = True):
        return await self.orders.close_all(fail_if_none)

    async def create_limit_order(self, params):
        return await self.orders.create_limit(params)

    async def create_trigger_order(self, params):
        return await self.orders.create_trigger(params)

    async def wait_for_order_execution(self, expected_order, timeout: int = 10):
        return await self.wait.for_order_execution(expected_order, timeout)

    async def wait_for_closing_order_execution(self, expected_order, expected_qty=None, timeout: int = 10):
        return await self.wait.for_closing_order_execution(expected_order, expected_qty, timeout)

    async def wait_for_spot_execution(self, expected_order, timeout: int = 10):
        return await self.wait.for_spot_execution(expected_order, timeout)

    async def wait_for_order_state(self, order_id: str, expected_status, timeout: int = 10):
        return await self.wait.for_order_state(order_id, expected_status, timeout)

    async def wait_for_order_creation(self, order_id: str, timeout: int = 10):
        return await self.wait.for_order_creation(order_id, timeout)

    async def check_open_order_created(self, order_id: str, expected_order):
        return await self.check.open_order_created(order_id, expected_order)

    async def check_no_open_orders(self):
        return await self.check.no_open_orders()

    async def check_position(self, symbol: str, **kwargs):
        return await self.check.position(symbol, **kwargs)

    async def check_position_not_open(self, symbol: str):
        return await self.check.position_not_open(symbol)

    async def check_order_execution(self, order_execution, expected_order, expected_qty=None):
        return await self.check.order_execution(order_execution, expected_order, expected_qty)

    async def check_no_order_execution_since(self, since_timestamp_ms: int):
        return await self.check.no_order_execution_since(since_timestamp_ms)

    async def check_spot_execution(self, spot_execution, expected_order, expected_qty=None):
        return await self.check.spot_execution(spot_execution, expected_order, expected_qty)

    async def check_balance(self, asset: str, expected_account_id: int, **kwargs):
        return await self.check.balance(asset, expected_account_id, **kwargs)

    async def setup_position(self, **kwargs):
        return await self.positions.setup(**kwargs)

    async def close_position(self, symbol: str, qty: str = "0.01"):
        return await self.positions.close(symbol, qty)

    async def flip_position(self, symbol: str, current_qty: str = "0.01", flip_qty: str = "0.02"):
        return await self.positions.flip(symbol, current_qty, flip_qty)

    def check_ws_order_change_received(self, order_id: str, **kwargs):
        return self.check.ws_order_change_received(order_id, **kwargs)

    def check_ws_spot_execution_received(self, **kwargs):
        return self.check.ws_spot_execution_received(**kwargs)

    def check_ws_balance_updates_received(self, initial_update_count: int, **kwargs):
        return self.check.ws_balance_updates_received(initial_update_count, **kwargs)

    def get_balance_update_count(self):
        return self.ws.get_balance_update_count()
