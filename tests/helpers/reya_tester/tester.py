"""Main ReyaTester class with composition-based architecture."""

from typing import Optional

import asyncio
import logging
import os

from dotenv import load_dotenv

from sdk.open_api.models.depth import Depth
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_websocket import ReyaSocket

from .checks import Checks
from .data import DataOperations
from .orders import OrderOperations
from .perp_trade_context import PerpTradeContext
from .positions import PositionOperations
from .waiters import Waiters
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

    def __init__(self, spot_account_number: Optional[int] = None):
        """
        Initialize ReyaTester with specified account configuration.

        Args:
            spot_account_number: Optional spot account to use (1 or 2) for spot tests.
                                If None, uses default PERP_ACCOUNT_ID_1, PERP_PRIVATE_KEY_1, PERP_WALLET_ADDRESS_1.
                                If 1, uses SPOT_ACCOUNT_ID_1, SPOT_PRIVATE_KEY_1, SPOT_WALLET_ADDRESS_1.
                                If 2, uses SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_2, SPOT_WALLET_ADDRESS_2.
        """
        load_dotenv()

        # Track if this is a spot account (cannot trade perps)
        self._is_spot_account = spot_account_number is not None

        if spot_account_number is None:
            # Default - use standard config (PERP_ACCOUNT_ID_1, PERP_PRIVATE_KEY_1, PERP_WALLET_ADDRESS_1)
            self.client = ReyaTradingClient()
        elif spot_account_number in (1, 2):
            # Spot account - create client with explicit spot account config
            self.client = self._create_client_for_spot_account(spot_account_number)
        else:
            raise ValueError(f"Invalid spot_account_number: {spot_account_number}. Must be None, 1, or 2.")

        # Store account properties - these must be set for tests to work
        assert self.client is not None, "Client must be initialized"
        assert self.client.config.account_id is not None, "account_id must be configured"
        assert self.client.config.chain_id is not None, "chain_id must be configured"

        self.owner_wallet_address: Optional[str] = self.client.owner_wallet_address
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

    def _create_client_for_spot_account(self, spot_account_number: int) -> ReyaTradingClient:
        """Create a ReyaTradingClient configured for a spot account."""
        account_id = os.environ.get(f"SPOT_ACCOUNT_ID_{spot_account_number}")
        private_key = os.environ.get(f"SPOT_PRIVATE_KEY_{spot_account_number}")
        wallet_address = os.environ.get(f"SPOT_WALLET_ADDRESS_{spot_account_number}")

        if not all([account_id, private_key, wallet_address]):
            logger.warning(
                f"Spot Account {spot_account_number} not fully configured. Missing one of: SPOT_ACCOUNT_ID_{spot_account_number}, SPOT_PRIVATE_KEY_{spot_account_number}, SPOT_WALLET_ADDRESS_{spot_account_number}"
            )
            # Return a client with None values - tests will skip if needed
            return ReyaTradingClient()

        # Get base config to inherit api_url and chain_id
        base_client = ReyaTradingClient()
        base_config = base_client.config

        # Create new config with spot account values
        if wallet_address is None:
            raise ValueError("wallet_address is required for spot account")
        if account_id is None:
            raise ValueError("account_id is required for spot account")
        spot_config = TradingConfig(
            api_url=base_config.api_url,
            chain_id=base_config.chain_id,
            owner_wallet_address=wallet_address,
            private_key=private_key,
            account_id=int(account_id),
        )

        # Create client with the spot config directly
        return ReyaTradingClient(config=spot_config)

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

        # Only close perp orders/positions for perp accounts
        # Spot accounts (account_id >= 10 billion) cannot trade perps
        if not self._is_spot_account:
            await self.orders.close_all(fail_if_none=False)
            await self.positions.close_all(fail_if_none=False)
        else:
            logger.info("Skipping perp cleanup for spot account")

    async def close(self) -> None:
        """Close all connections."""
        if self._websocket:
            self._websocket.close()
        await self.client.close()

    def perp_trade(self) -> PerpTradeContext:
        """Create a context for bulletproof PERP trade verification.

        This context manager captures the baseline sequence number BEFORE
        any order is placed, enabling reliable trade verification across
        both REST and WebSocket channels.

        Usage:
            async with reya_tester.perp_trade() as ctx:
                await reya_tester.create_limit_order(params)
                result = await ctx.wait_for_execution(expected_order)
                # result.rest_execution and result.ws_execution are guaranteed
                # to be the SAME trade (same sequence_number)

        Returns:
            PerpTradeContext: Context manager for trade verification.
        """
        return PerpTradeContext(tester=self)

    # ==========================================================================
    # Backward compatibility properties/methods
    # These allow existing tests to work without changes during migration
    # ==========================================================================

    @property
    def websocket(self) -> Optional[ReyaSocket]:
        """Backward compatibility: access to raw websocket."""
        return self._websocket

    @websocket.setter
    def websocket(self, value: Optional[ReyaSocket]) -> None:
        """Set the websocket connection."""
        self._websocket = value

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
    def ws_spot_executions(self):
        """Backward compatibility."""
        return self.ws.spot_executions

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

    async def get_last_perp_execution_sequence_number(self) -> int:
        """Get the sequence number of the last perp execution, or 0 if none exists."""
        execution = await self.data.last_perp_execution()
        return execution.sequence_number if execution else 0

    async def get_last_wallet_spot_execution(self):
        return await self.data.last_spot_execution()

    async def get_balances(self):
        return await self.data.balances()

    async def get_balance(self, asset: str):
        return await self.data.balance(asset)

    def clear_balance_updates(self):
        self.ws.clear_balance_updates()

    def clear_spot_executions(self):
        self.ws.clear_spot_executions()

    def get_balance_updates_for_account(self, account_id: int):
        return self.ws.get_balance_updates_for_account(account_id)

    def verify_spot_trade_balance_changes(self, *args, **kwargs):
        return self.ws.verify_spot_trade_balance_changes(*args, **kwargs)

    async def get_market_depth(self, symbol: str) -> Depth:
        return await self.data.market_depth(symbol)

    def subscribe_to_market_depth(self, symbol: str):
        self.ws.subscribe_to_market_depth(symbol)

    def subscribe_to_market_spot_executions(self, symbol: str):
        self.ws.subscribe_to_market_spot_executions(symbol)

    @property
    def ws_market_spot_executions(self):
        """Backward compatibility."""
        return self.ws.market_spot_executions

    def clear_market_spot_executions(self, symbol: Optional[str] = None):
        self.ws.clear_market_spot_executions(symbol)

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

    async def wait_for_order_execution(self, expected_order, timeout: int = 10, baseline_seq: Optional[int] = None):
        return await self.wait.for_order_execution(expected_order, timeout, baseline_seq)

    async def wait_for_closing_order_execution(
        self, expected_order, expected_qty=None, timeout: int = 10, baseline_seq: Optional[int] = None
    ):
        return await self.wait.for_closing_order_execution(expected_order, expected_qty, timeout, baseline_seq)

    async def wait_for_spot_execution(self, order_id: str, expected_order, timeout: int = 10):
        return await self.wait.for_spot_execution(order_id, expected_order, timeout)

    async def wait_for_order_state(self, order_id: str, expected_status, timeout: int = 10):
        return await self.wait.for_order_state(order_id, expected_status, timeout)

    async def wait_for_order_creation(self, order_id: str, expected_order=None, timeout: int = 10):
        return await self.wait.for_order_creation(order_id, expected_order, timeout)

    async def wait_for_balance_updates(self, initial_count: int, min_updates: int = 1, timeout: float = 5.0):
        return await self.wait.for_balance_updates(initial_count, min_updates, timeout)

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

    async def check_no_order_execution_since(self, since_sequence_number: int):
        return await self.check.no_order_execution_since(since_sequence_number)

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

    def get_next_nonce(self) -> int:
        """
        Get the next nonce from the SDK's nonce tracking mechanism.

        This is useful for validation tests that need to manually construct
        API requests while keeping the nonce counter in sync.

        Returns:
            The next nonce value to use for API requests.
        """
        return self.client.get_next_nonce()
