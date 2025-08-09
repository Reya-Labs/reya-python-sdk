"""
Assets resource for Reya Trading API.

This module provides asset-related functionality.
"""

from typing import Any

from sdk.reya_rest_api.resources.base import BaseResource


class AssetsResource(BaseResource):
    """Resource for asset-related API endpoints."""

    async def get_assets(self) -> list[dict[str, Any]]:
        """
        Get all assets asynchronously.

        Returns:
            List of assets and their information

        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/assets"
        response_data: list[dict[str, Any]] = await self._get(endpoint)
        return response_data
