"""Data retrieval operations for ReyaTester."""

from typing import TYPE_CHECKING, Optional

import asyncio
import logging

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.depth import Depth
from sdk.open_api.models.execution_bust import ExecutionBust
from sdk.open_api.models.execution_bust_list import ExecutionBustList
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class DataOperations:
    """Data retrieval operations (get_* methods)."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

    async def current_price(self, symbol: str = "ETHRUSDPERP", max_attempts: int = 5) -> str:
        """Fetch current perp mark price for a symbol, retrying through transient market-data gaps."""
        last_exc: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                current_price = await self._t.client.get_market_mark_price(symbol)
                logger.info(f"💰 Current mark price for {symbol}: ${float(current_price):.2f}")
                return current_price
            except (ApiException, RuntimeError) as e:
                # Only transient market-data gaps are retryable; anything else is a real error.
                error_text = str(e)
                if (
                    "NO_PRICES_FOUND_FOR_SYMBOL_ERROR" not in error_text
                    and "Price not found" not in error_text
                    and "market data not found" not in error_text
                    and "did not include markPrice" not in error_text
                ):
                    raise
                last_exc = e
                logger.warning(
                    f"⚠️ market summary gap for {symbol} (attempt {attempt + 1}/{max_attempts}); " "retrying in 0.3s"
                )
            await asyncio.sleep(0.3)

        raise RuntimeError(f"Current mark price for {symbol} unavailable after {max_attempts} attempts") from last_exc

    async def asset_oracle_price(self, asset: str, max_attempts: int = 5) -> str:
        """Fetch current asset oracle price, retrying through transient market-data gaps."""
        last_exc: Optional[Exception] = None
        for attempt in range(max_attempts):
            try:
                current_price = (await self._t.client.get_asset_oracle_price(asset)).oracle_price
                logger.info(f"💰 Current asset oracle price for {asset}: ${float(current_price):.2f}")
                return current_price
            except (ApiException, RuntimeError) as e:
                error_text = str(e)
                if (
                    "Price not found" not in error_text
                    and "market data not found" not in error_text
                    and "Asset oracle price not found" not in error_text
                ):
                    raise
                last_exc = e
                logger.warning(
                    f"⚠️ asset oracle price gap for {asset} (attempt {attempt + 1}/{max_attempts}); " "retrying in 0.3s"
                )
            await asyncio.sleep(0.3)

        raise RuntimeError(
            f"Current asset oracle price for {asset} unavailable after {max_attempts} attempts"
        ) from last_exc

    async def positions(self) -> dict[str, Position]:
        """Get all current positions."""
        positions_list: list[Position] = await self._t.client.get_positions()

        position_summary = {}
        for position in positions_list:
            symbol = position.symbol
            qty = position.qty
            if symbol and qty:
                position_summary[symbol] = position

        return position_summary

    async def position(self, symbol: str) -> Optional[Position]:
        """Get current position for a specific market."""
        positions = await self.positions()
        return positions.get(symbol)

    async def last_perp_execution(self) -> Optional[PerpExecution]:
        """Get the most recent perp execution for this wallet.

        Returns None if no executions exist.
        """
        wallet_address = self._t.owner_wallet_address
        if wallet_address is None:
            raise ValueError("owner_wallet_address is required for perp execution lookup")
        trades_list: PerpExecutionList = await self._t.client.wallet.get_wallet_perp_executions(address=wallet_address)
        if trades_list.data and len(trades_list.data) > 0:
            return trades_list.data[0]
        return None

    async def last_spot_execution(self) -> Optional[SpotExecution]:
        """Get the most recent spot execution for this wallet.

        Returns None if no executions exist.
        """
        wallet_address = self._t.owner_wallet_address
        if wallet_address is None:
            raise ValueError("owner_wallet_address is required for spot execution lookup")
        executions_list: SpotExecutionList = await self._t.client.wallet.get_wallet_spot_executions(
            address=wallet_address
        )
        if executions_list.data and len(executions_list.data) > 0:
            return executions_list.data[0]
        return None

    async def balances(self) -> dict[str, AccountBalance]:
        """Get current account balances for this tester's account only."""
        balances_list: list[AccountBalance] = await self._t.client.get_account_balances()

        balance_dict = {}
        for balance in balances_list:
            if balance.account_id == self._t.account_id:
                asset = balance.asset
                if asset:
                    balance_dict[asset] = balance

        return balance_dict

    async def balance(self, asset: str) -> Optional[AccountBalance]:
        """Get balance for a specific asset."""
        balances = await self.balances()
        return balances.get(asset)

    async def market_depth(self, symbol: str) -> Depth:
        """Get L2 market depth (orderbook) for a given symbol via REST API."""
        return await self._t.client.markets.get_market_depth(symbol=symbol)

    async def market_definition(self, symbol: str) -> MarketDefinition:
        """Get market configuration for a specific symbol."""
        markets_config: list[MarketDefinition] = await self._t.client.reference.get_perp_market_definitions()
        for config in markets_config:
            if config.symbol == symbol:
                return config
        raise RuntimeError(f"Market definition not found for symbol: {symbol}")

    async def open_order(self, order_id: str) -> Optional[Order]:
        """Get a specific open order by ID."""
        open_orders = await self._t.client.get_open_orders()
        for order in open_orders:
            if order.order_id == order_id:
                return order
        return None

    async def open_orders(self) -> list[Order]:
        """Get all open orders."""
        return await self._t.client.get_open_orders()

    async def execution_busts(self) -> list[ExecutionBust]:
        """Get execution busts (failed fills) for this wallet, across spot and perp.

        Returns list of ExecutionBust objects (may be empty).
        """
        bust_list: ExecutionBustList = await self._t.client.get_execution_busts()
        return bust_list.data if bust_list.data else []

    async def market_execution_busts(self, symbol: str) -> list[ExecutionBust]:
        """Get execution busts for a specific market (spot or perp).

        Args:
            symbol: Trading symbol (e.g., ``WETHRUSD`` or ``ETHRUSDPERP``).
        """
        bust_list: ExecutionBustList = await self._t.client.markets.get_market_execution_busts(symbol=symbol)
        return bust_list.data if bust_list.data else []
