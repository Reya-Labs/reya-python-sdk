"""
REST API client wrapper for integration tests.

This module provides a thin wrapper around the SDK's ReyaTradingClient,
exposing commonly used methods for tests with consistent error handling
and logging.
"""

from typing import Optional
import logging

from sdk.open_api.models.account_balance import AccountBalance
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.perp_execution_list import PerpExecutionList
from sdk.open_api.models.position import Position
from sdk.open_api.models.price import Price
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models import LimitOrderParameters, TriggerOrderParameters


logger = logging.getLogger("reya.test.rest_client")


class RestClient:
    """
    REST API client wrapper for integration tests.
    
    Provides a simplified interface to the SDK's ReyaTradingClient with:
    - Consistent logging
    - Error handling
    - Type hints for all return values
    
    The client is initialized from environment variables via ReyaTradingClient.
    """
    
    def __init__(self):
        """Initialize the REST client from environment variables."""
        self._client = ReyaTradingClient()
        
        # Validate required configuration
        assert self._client.owner_wallet_address is not None, "OWNER_WALLET_ADDRESS is required"
        assert self._client.config.account_id is not None, "ACCOUNT_ID is required"
        assert self._client.config.chain_id is not None, "CHAIN_ID is required"
    
    @property
    def owner_wallet_address(self) -> str:
        """Get the owner wallet address."""
        return self._client.owner_wallet_address
    
    @property
    def account_id(self) -> int:
        """Get the default account ID."""
        return self._client.config.account_id
    
    @property
    def chain_id(self) -> int:
        """Get the chain ID."""
        return self._client.config.chain_id
    
    @property
    def dex_id(self) -> int:
        """Get the DEX/exchange ID."""
        return self._client.config.dex_id
    
    async def start(self) -> None:
        """Start the client (initialize async resources)."""
        await self._client.start()
    
    async def stop(self) -> None:
        """Stop the client (cleanup async resources)."""
        await self._client.stop()
    
    # ==================== Market Data ====================
    
    async def get_price(self, symbol: str) -> Price:
        """Get current price for a symbol."""
        return await self._client.markets.get_price(symbol)
    
    async def get_current_price(self, symbol: str) -> str:
        """Get current oracle price as string."""
        price_info = await self.get_price(symbol)
        if price_info.oracle_price:
            logger.debug(f"Current price for {symbol}: ${float(price_info.oracle_price):.2f}")
            return price_info.oracle_price
        raise RuntimeError(f"Current market price not found for {symbol}")
    
    async def get_market_definitions(self) -> list[MarketDefinition]:
        """Get all market definitions."""
        return await self._client.reference.get_market_definitions()
    
    async def get_market_definition(self, symbol: str) -> MarketDefinition:
        """Get market definition for a specific symbol."""
        definitions = await self.get_market_definitions()
        for definition in definitions:
            if definition.symbol == symbol:
                return definition
        raise RuntimeError(f"Market definition not found for symbol: {symbol}")
    
    async def get_market_depth(self, symbol: str) -> dict:
        """Get L2 market depth (orderbook) for a symbol."""
        return await self._client.get_market_depth(symbol)
    
    # ==================== Orders ====================
    
    async def create_limit_order(self, params: LimitOrderParameters) -> CreateOrderResponse:
        """Create a limit order (IOC or GTC)."""
        side_text = "BUY" if params.is_buy else "SELL"
        tif_text = params.time_in_force.value if params.time_in_force else "GTC"
        logger.info(f"Creating {tif_text} {side_text} order: {params.symbol} @ {params.limit_px} x {params.qty}")
        
        response = await self._client.create_limit_order(params)
        logger.debug(f"Order response: {response}")
        return response
    
    async def create_trigger_order(self, params: TriggerOrderParameters) -> CreateOrderResponse:
        """Create a trigger order (TP/SL)."""
        side_text = "BUY" if params.is_buy else "SELL"
        trigger_text = params.trigger_type.value if params.trigger_type else "TRIGGER"
        logger.info(f"Creating {trigger_text} {side_text} order: {params.symbol} @ trigger {params.trigger_px}")
        
        response = await self._client.create_trigger_order(params)
        logger.info(f"Trigger order created: {response.order_id}")
        return response
    
    async def cancel_order(self, order_id: str, symbol: str, account_id: int) -> None:
        """Cancel an order by ID."""
        logger.info(f"Cancelling order {order_id} for {symbol}")
        await self._client.cancel_order(order_id=order_id, symbol=symbol, account_id=account_id)
    
    async def get_open_orders(self) -> list[Order]:
        """Get all open orders for the account."""
        return await self._client.get_open_orders()
    
    async def get_open_order(self, order_id: str) -> Optional[Order]:
        """Get a specific open order by ID."""
        orders = await self.get_open_orders()
        for order in orders:
            if order.order_id == order_id:
                return order
        return None
    
    # ==================== Positions ====================
    
    async def get_positions(self) -> list[Position]:
        """Get all positions for the account."""
        return await self._client.get_positions()
    
    async def get_positions_by_symbol(self) -> dict[str, Position]:
        """Get positions as a dict keyed by symbol."""
        positions = await self.get_positions()
        return {p.symbol: p for p in positions if p.symbol}
    
    async def get_position(self, symbol: str) -> Optional[Position]:
        """Get position for a specific symbol."""
        positions = await self.get_positions_by_symbol()
        return positions.get(symbol)
    
    # ==================== Balances ====================
    
    async def get_account_balances(self) -> list[AccountBalance]:
        """Get all account balances."""
        return await self._client.get_account_balances()
    
    async def get_balances(self, account_id: Optional[int] = None) -> dict[str, AccountBalance]:
        """Get balances as a dict keyed by asset, optionally filtered by account."""
        target_account = account_id or self.account_id
        balances = await self.get_account_balances()
        return {
            b.asset: b 
            for b in balances 
            if b.asset and b.account_id == target_account
        }
    
    async def get_balance(self, asset: str, account_id: Optional[int] = None) -> Optional[AccountBalance]:
        """Get balance for a specific asset."""
        balances = await self.get_balances(account_id)
        return balances.get(asset)
    
    # ==================== Executions ====================
    
    async def get_wallet_perp_executions(self, address: Optional[str] = None) -> PerpExecutionList:
        """Get perpetual executions for a wallet."""
        target_address = address or self.owner_wallet_address
        return await self._client.wallet.get_wallet_perp_executions(address=target_address)
    
    async def get_last_perp_execution(self, address: Optional[str] = None) -> Optional[PerpExecution]:
        """Get the most recent perpetual execution."""
        executions = await self.get_wallet_perp_executions(address)
        return executions.data[0] if executions.data else None
    
    async def get_wallet_spot_executions(self, address: Optional[str] = None) -> SpotExecutionList:
        """Get spot executions for a wallet."""
        target_address = address or self.owner_wallet_address
        return await self._client.wallet.get_wallet_spot_executions(address=target_address)
    
    async def get_last_spot_execution(self, address: Optional[str] = None) -> Optional[SpotExecution]:
        """Get the most recent spot execution."""
        executions = await self.get_wallet_spot_executions(address)
        return executions.data[0] if executions.data else None
