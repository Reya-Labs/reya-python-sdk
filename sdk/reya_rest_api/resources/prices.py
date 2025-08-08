"""
Prices resource for Reya Trading API.

This module provides price-related functionality.
"""
from typing import Dict, Any

from sdk.reya_rest_api.resources.base import BaseResource


class PricesResource(BaseResource):
    """Resource for price-related API endpoints."""
    
    async def get_prices(self) -> Dict[str, Any]:
        """
        Get all prices asynchronously.
        
        Returns:
            Price information for all assets
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = "api/trading/prices"
        response_data = await self._get(endpoint)
        return response_data
        
    async def get_price(self, asset_pair_id: str) -> Dict[str, Any]:
        """
        Get price for a specific asset pair asynchronously.
        
        Args:
            asset_pair_id: The asset pair ID
            
        Returns:
            Price information for the specified asset pair
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/prices/{asset_pair_id}"
        response_data = await self._get(endpoint)
        return response_data
