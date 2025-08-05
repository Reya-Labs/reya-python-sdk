"""
Wallet resource for Reya Trading API.

This module provides wallet-related functionality.
"""
from typing import Dict, Any

from sdk.reya_rest_api.resources.base import BaseResource


class WalletResource(BaseResource):
    """Resource for wallet-related API endpoints."""
    
    def get_positions(self, wallet_address: str) -> Dict[str, Any]:
        """
        Get positions for a wallet address.
        
        Args:
            wallet_address: The wallet address
            
        Returns:
            Positions data
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/positions"
        response_data = self._get(endpoint)
        return response_data
    
    def get_conditional_orders(self, wallet_address: str) -> Dict[str, Any]:
        """
        Get conditional orders (limit, stop loss, take profit) for a wallet address.
        
        Args:
            wallet_address: The wallet address to get orders for
            
        Returns:
            List of conditional orders
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/conditionalOrders"
        response_data = self._get(endpoint)
        return response_data
    
    def get_balances(self, wallet_address: str) -> Dict[str, Any]:
        """
        Get account balance.
        
        Args:
            wallet_address: The wallet address
            
        Returns:
            Account balance information
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/accounts/balances"
        return self._get(endpoint)
    
    def get_configuration(self, wallet_address: str) -> Dict[str, Any]:
        """
        Get account configuration.
        
        Args:
            wallet_address: The wallet address
            
        Returns:
            Account configuration information
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/configuration"
        return self._get(endpoint)
    
    def get_orders(self, wallet_address: str) -> Dict[str, Any]:
        """
        Get filled orders for a wallet address.
        
        Args:
            wallet_address: The wallet address to get orders for
            
        Returns:
            List of filled orders
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"api/trading/wallet/{wallet_address}/orders"
        response_data = self._get(endpoint)
        return response_data
