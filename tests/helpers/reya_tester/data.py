"""Data retrieval operations for ReyaTester."""

from typing import TYPE_CHECKING, Optional

import logging

from sdk.async_api.depth import Depth
from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.price import Price
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class DataOperations:
    """Data retrieval operations (get_* methods)."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

    async def current_price(self, symbol: str = "ETHRUSDPERP") -> str:
        """Fetch current market price for a symbol."""
        price_info: Price = await self._t.client.markets.get_price(symbol)
        logger.info(f"Price info: {price_info}")
        current_price = price_info.oracle_price

        if current_price:
            logger.info(f"💰 Current market price for {symbol}: ${float(current_price):.2f}")
            return current_price
        else:
            logger.info(f"❌ Current market price for {symbol} not found")
            raise RuntimeError("Current market price not found")

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

    async def last_perp_execution(self) -> PerpExecution:
        """Get the most recent perp execution for this wallet."""
        trades_list: PerpExecutionList = await self._t.client.wallet.get_wallet_perp_executions(
            address=self._t.owner_wallet_address
        )
        return trades_list.data[0]

    async def last_spot_execution(self) -> SpotExecution:
        """Get the most recent spot execution for this wallet."""
        executions_list: SpotExecutionList = await self._t.client.wallet.get_wallet_spot_executions(
            address=self._t.owner_wallet_address
        )
        return executions_list.data[0]

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
        return await self._t.client.get_market_depth(symbol)

    async def market_definition(self, symbol: str) -> MarketDefinition:
        """Get market configuration for a specific symbol."""
        markets_config: list[MarketDefinition] = await self._t.client.reference.get_market_definitions()
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
