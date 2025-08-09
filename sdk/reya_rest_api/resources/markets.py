"""
Markets resource for Reya Trading API.

This module provides market-related functionality.
"""

from typing import Any

from sdk.reya_rest_api.resources.base import BaseResource


class MarketsResource(BaseResource):
    """Resource for market-related API endpoints."""

    async def get_markets(self) -> list[dict[str, Any]]:
        """
        Get all markets asynchronously.

        Returns:
            List of markets

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/markets"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_market(self, market_id: str) -> list[dict[str, Any]]:
        """
        Get market by ID asynchronously.

        Args:
            market_id: The market ID

        Returns:
            Market information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/market/{market_id}"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_markets_configuration(self) -> list[dict[str, Any]]:
        """
        Get markets configuration asynchronously.

        Returns:
            Markets configuration information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/markets/configuration"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_markets_storage(self) -> list[dict[str, Any]]:
        """
        Get markets storage information asynchronously.

        Returns:
            Markets storage data

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/markets/storage"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_markets_trackers(self) -> list[dict[str, Any]]:
        """
        Get all market trackers asynchronously.

        Returns:
            Markets trackers information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/markets/trackers"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_market_trackers(self, market_id: str) -> list[dict[str, Any]]:
        """
        Get trackers for a specific market asynchronously.

        Args:
            market_id: The market ID

        Returns:
            Market trackers information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/market/{market_id}/trackers"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_market_trades(self, market_id: str) -> list[dict[str, Any]]:
        """
        Get trades for a specific market asynchronously.

        Args:
            market_id: The market ID

        Returns:
            Market trades information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/market/{market_id}/trades"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_market_data(self, market_id: str) -> list[dict[str, Any]]:
        """
        Get data for a specific market asynchronously.

        Args:
            market_id: The market ID

        Returns:
            Market data

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/market/{market_id}/data"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data

    async def get_markets_data(self) -> list[dict[str, Any]]:
        """
        Get data for all markets asynchronously.

        Returns:
            All markets data

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/markets/data"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data
