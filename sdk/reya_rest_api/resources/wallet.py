"""
Wallet resource for Reya Trading API.

This module provides wallet-related functionality.
"""

from typing import Any

from sdk.reya_rest_api.resources.base import BaseResource


class WalletResource(BaseResource):
    """Resource for wallet-related API endpoints."""

    async def get_positions(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get positions for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Positions data

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/positions"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_open_orders(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get open orders (limit, stop loss, take profit) for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address to get orders for

        Returns:
            List of open orders

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/openOrders"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_balances(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get account balance asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Account balance information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/accounts/balances"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_configuration(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get account configuration asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Account configuration information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/configuration"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_trades(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get trades for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address to get trades for

        Returns:
            List of trades

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/trades"
        response_data: dict[str, list[dict[str, Any]]] = await self._get(endpoint)
        return response_data.get("data", [])

    async def get_accounts(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get accounts for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Account information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/accounts"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_leverages(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get leverages for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Leverage information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/leverages"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_auto_exchange(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get auto exchange settings for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Auto exchange settings

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/autoExchange"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_stats(self, wallet_address: str) -> list[dict[str, Any]]:
        """
        Get stats for a wallet address asynchronously.

        Args:
            wallet_address: The wallet address

        Returns:
            Wallet stats information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/stats"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data
