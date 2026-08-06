"""
Trading operations wrapper around the Reya Python SDK.

Provides a high-level async interface for all trading operations used by the bot.
"""

import logging
from typing import Optional

from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters

logger = logging.getLogger("telegram_bot.trading")


class TradingService:
    """
    Wrapper around ReyaTradingClient providing trading operations for the Telegram bot.

    This class manages the client lifecycle and exposes simple async methods
    for each bot command that involves trading.
    """

    def __init__(self, config: TradingConfig):
        self._config = config
        self._client: Optional[ReyaTradingClient] = None

    async def start(self) -> None:
        """Initialize the trading client and load market definitions."""
        self._client = ReyaTradingClient(config=self._config)
        await self._client.start()
        logger.info("Trading service started")

    async def stop(self) -> None:
        """Close the trading client session."""
        if self._client:
            await self._client.close()
            self._client = None
        logger.info("Trading service stopped")

    def _require_client(self) -> ReyaTradingClient:
        if self._client is None:
            raise RuntimeError("Trading service is not started. Call start() first.")
        return self._client

    # -------------------------------------------------------------------------
    # Market data
    # -------------------------------------------------------------------------

    async def get_prices(self) -> list:
        """Return all market prices."""
        client = self._require_client()
        return await client.markets.get_prices()

    async def get_price(self, symbol: str) -> Optional[object]:
        """Return the price for a specific symbol, or None if not found."""
        prices = await self.get_prices()
        for price in prices:
            if price.symbol == symbol:
                return price
        return None

    async def get_market_definitions(self) -> list:
        """Return all perp market definitions."""
        client = self._require_client()
        return await client.reference.get_market_definitions()

    async def get_spot_market_definitions(self) -> list:
        """Return all spot market definitions."""
        client = self._require_client()
        return await client.reference.get_spot_market_definitions()

    async def get_market_summaries(self) -> list:
        """Return all market summaries."""
        client = self._require_client()
        return await client.markets.get_markets()

    # -------------------------------------------------------------------------
    # Account / wallet data
    # -------------------------------------------------------------------------

    async def get_accounts(self) -> list:
        """Return accounts for the configured wallet."""
        client = self._require_client()
        return await client.get_accounts()

    async def get_account_balances(self) -> list:
        """Return account balances for the configured wallet."""
        client = self._require_client()
        return await client.get_account_balances()

    async def get_positions(self) -> list:
        """Return open positions for the configured wallet."""
        client = self._require_client()
        return await client.get_positions()

    async def get_open_orders(self) -> list:
        """Return open orders for the configured wallet."""
        client = self._require_client()
        return await client.get_open_orders()

    async def get_perp_executions(self) -> object:
        """Return perp trade history for the configured wallet."""
        client = self._require_client()
        return await client.get_perp_executions()

    # -------------------------------------------------------------------------
    # Order management
    # -------------------------------------------------------------------------

    async def buy_ioc(self, symbol: str, qty: str, limit_px: str) -> object:
        """Submit an IOC limit buy order."""
        client = self._require_client()
        return await client.create_limit_order(
            LimitOrderParameters(
                symbol=symbol,
                is_buy=True,
                limit_px=limit_px,
                qty=qty,
                time_in_force=TimeInForce.IOC,
                reduce_only=False,
            )
        )

    async def sell_ioc(self, symbol: str, qty: str, limit_px: str) -> object:
        """Submit an IOC limit sell order."""
        client = self._require_client()
        return await client.create_limit_order(
            LimitOrderParameters(
                symbol=symbol,
                is_buy=False,
                limit_px=limit_px,
                qty=qty,
                time_in_force=TimeInForce.IOC,
                reduce_only=False,
            )
        )

    async def buy_gtc(self, symbol: str, qty: str, limit_px: str) -> object:
        """Submit a GTC limit buy order."""
        client = self._require_client()
        return await client.create_limit_order(
            LimitOrderParameters(
                symbol=symbol,
                is_buy=True,
                limit_px=limit_px,
                qty=qty,
                time_in_force=TimeInForce.GTC,
            )
        )

    async def sell_gtc(self, symbol: str, qty: str, limit_px: str) -> object:
        """Submit a GTC limit sell order."""
        client = self._require_client()
        return await client.create_limit_order(
            LimitOrderParameters(
                symbol=symbol,
                is_buy=False,
                limit_px=limit_px,
                qty=qty,
                time_in_force=TimeInForce.GTC,
            )
        )

    async def stop_loss(self, symbol: str, is_buy: bool, trigger_px: str) -> object:
        """Submit a stop-loss trigger order."""
        client = self._require_client()
        return await client.create_trigger_order(
            TriggerOrderParameters(
                symbol=symbol,
                is_buy=is_buy,
                trigger_px=trigger_px,
                trigger_type=OrderType.SL,
            )
        )

    async def take_profit(self, symbol: str, is_buy: bool, trigger_px: str) -> object:
        """Submit a take-profit trigger order."""
        client = self._require_client()
        return await client.create_trigger_order(
            TriggerOrderParameters(
                symbol=symbol,
                is_buy=is_buy,
                trigger_px=trigger_px,
                trigger_type=OrderType.TP,
            )
        )

    async def cancel_order(self, order_id: str) -> object:
        """Cancel an order by its ID."""
        client = self._require_client()
        return await client.cancel_order(order_id=order_id)
