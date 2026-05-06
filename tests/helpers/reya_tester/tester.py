"""Main ReyaTester class with composition-based architecture."""

from typing import Optional

import asyncio
import logging
import os

from dotenv import load_dotenv

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

    def __init__(
        self,
        spot_account_number: Optional[int] = None,
        perp_account_number: Optional[int] = None,
    ):
        """
        Initialize ReyaTester with specified account configuration.

        Args:
            spot_account_number: Optional spot account (1 or 2) for spot tests.
                If set, uses SPOT_ACCOUNT_ID_<N>, SPOT_PRIVATE_KEY_<N>, SPOT_WALLET_ADDRESS_<N>.
            perp_account_number: Optional perp account (1 or 2) for perp orderbook
                maker/taker tests. If set, uses PERP_ACCOUNT_ID_<N>, PERP_PRIVATE_KEY_<N>,
                PERP_WALLET_ADDRESS_<N>. ``perp_account_number=1`` is also the default
                when both args are None (backwards compat).

        Exactly one of spot_account_number / perp_account_number may be set; if both
        are None, the legacy default is PERP_ACCOUNT_ID_1.
        """
        load_dotenv()

        if spot_account_number is not None and perp_account_number is not None:
            raise ValueError("spot_account_number and perp_account_number are mutually exclusive")

        # Track if this is a spot account (cannot trade perps)
        self._is_spot_account = spot_account_number is not None
        self._spot_account_number = spot_account_number
        self._perp_account_number = perp_account_number

        if spot_account_number is None and perp_account_number in (None, 1):
            # Default — PERP_ACCOUNT_ID_1 via TradingConfig.from_env()
            self.client = ReyaTradingClient()
        elif spot_account_number in (1, 2):
            self.client = self._create_client_for_spot_account(spot_account_number)
        elif perp_account_number == 2:
            self.client = self._create_client_for_perp_account(perp_account_number)
        else:
            raise ValueError(f"Invalid account selection: spot={spot_account_number} perp={perp_account_number}")

        # Store account properties - these must be set for tests to work
        assert self.client is not None, "Client must be initialized"
        assert self.client.config.account_id is not None, "account_id must be configured"
        assert self.client.config.chain_id is not None, "chain_id must be configured"

        self.owner_wallet_address: Optional[str] = self.client.owner_wallet_address
        self.account_id: int = self.client.config.account_id
        self.chain_id: int = self.client.config.chain_id

        # Internal WebSocket reference
        self._websocket: Optional[ReyaSocket] = None

        # Nonce tracking for order operations
        self._nonce_counter: int = 0

        # Composed components
        self.ws = WebSocketState(self)
        self.data = DataOperations(self)
        self.orders = OrderOperations(self)
        self.positions = PositionOperations(self)
        self.wait = Waiters(self)
        self.check = Checks(self)

    def _create_client_for_spot_account(self, spot_account_number: int) -> ReyaTradingClient:
        """Create a ReyaTradingClient configured for a spot account."""
        return self._create_client_for_account("SPOT", spot_account_number)

    def _create_client_for_perp_account(self, perp_account_number: int) -> ReyaTradingClient:
        """Create a ReyaTradingClient configured for the secondary perp account.

        PERP_ACCOUNT_ID_2 / PERP_PRIVATE_KEY_2 / PERP_WALLET_ADDRESS_2 unlock perp
        orderbook maker/taker tests; under perpOB every fill needs a counterparty
        on the opposite side of the book.
        """
        return self._create_client_for_account("PERP", perp_account_number)

    def _create_client_for_account(self, prefix: str, account_number: int) -> ReyaTradingClient:
        """Build a client from {PREFIX}_ACCOUNT_ID_N env vars (PREFIX in {SPOT, PERP})."""
        account_id = os.environ.get(f"{prefix}_ACCOUNT_ID_{account_number}")
        private_key = os.environ.get(f"{prefix}_PRIVATE_KEY_{account_number}")
        wallet_address = os.environ.get(f"{prefix}_WALLET_ADDRESS_{account_number}")

        if not all([account_id, private_key, wallet_address]):
            logger.warning(
                f"{prefix} Account {account_number} not fully configured. Missing one of: "
                f"{prefix}_ACCOUNT_ID_{account_number}, {prefix}_PRIVATE_KEY_{account_number}, "
                f"{prefix}_WALLET_ADDRESS_{account_number}"
            )
            return ReyaTradingClient()

        base_client = ReyaTradingClient()
        base_config = base_client.config

        if wallet_address is None:
            raise ValueError(f"wallet_address is required for {prefix} account")
        if account_id is None:
            raise ValueError(f"account_id is required for {prefix} account")
        config = TradingConfig(
            api_url=base_config.api_url,
            chain_id=base_config.chain_id,
            owner_wallet_address=wallet_address,
            private_key=private_key,
            account_id=int(account_id),
            # Carry env-var-driven overrides from the base config so secondary
            # accounts sign with the same OrdersGateway domain and exchange id
            # as account 1 — otherwise they fall back to chain-id defaults that
            # may not match the deployment (e.g. devnet1).
            orders_gateway_address=base_config.orders_gateway_address,
            dex_id_override=base_config.dex_id_override,
        )
        return ReyaTradingClient(config=config)

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

    @property
    def websocket(self) -> Optional[ReyaSocket]:
        """Get the WebSocket connection (for cleanup/testing purposes)."""
        return self._websocket

    @websocket.setter
    def websocket(self, value: Optional[ReyaSocket]) -> None:
        """Set the WebSocket connection (for reconnection in tests)."""
        self._websocket = value

    @property
    def spot_account_number(self) -> Optional[int]:
        """Get the spot account number (1 or 2) if this is a spot account, None otherwise."""
        return self._spot_account_number

    @property
    def perp_account_number(self) -> Optional[int]:
        """Get the perp account number (1 or 2) if explicitly selected."""
        return self._perp_account_number

    @property
    def is_spot_account(self) -> bool:
        """Check if this tester is configured for a spot account."""
        return self._is_spot_account

    def perp_trade(self) -> PerpTradeContext:
        """Create a context for bulletproof PERP trade verification.

        This context manager captures the baseline sequence number BEFORE
        any order is placed, enabling reliable trade verification across
        both REST and WebSocket channels.

        Usage:
            async with reya_tester.perp_trade() as ctx:
                await reya_tester.orders.create_limit(params)
                result = await ctx.wait_for_execution(expected_order)
                # result.rest_execution and result.ws_execution are guaranteed
                # to be the SAME trade (same sequence_number)

        Returns:
            PerpTradeContext: Context manager for trade verification.
        """
        return PerpTradeContext(tester=self)

    def get_next_nonce(self) -> int:
        """
        Get the next nonce from the SDK's nonce tracking mechanism.

        This is useful for validation tests that need to manually construct
        API requests while keeping the nonce counter in sync.

        Returns:
            The next nonce value to use for API requests.
        """
        return self.client.get_next_nonce()

    async def get_last_perp_execution_sequence_number(self) -> int:
        """Get the sequence number of the last perp execution.

        Returns 0 if no executions exist.
        """
        execution = await self.data.last_perp_execution()
        return execution.sequence_number if execution and execution.sequence_number else 0

    async def get_last_wallet_perp_execution(self):
        """Get the most recent perp execution for this wallet.

        Returns None if no executions exist.
        """
        return await self.data.last_perp_execution()

    async def get_market_definition(self, symbol: str):
        """Get market configuration for a specific symbol."""
        return await self.data.market_definition(symbol)

    async def wait_for_closing_order_execution(self, expected_order, expected_qty: Optional[str] = None):
        """Wait for position-closing trade confirmation via both REST and WebSocket."""
        return await self.wait.for_closing_order_execution(expected_order, expected_qty)

    async def check_no_order_execution_since(self, since_sequence_number: int) -> None:
        """Assert no order execution occurred since the given sequence number."""
        await self.check.no_order_execution_since(since_sequence_number)
