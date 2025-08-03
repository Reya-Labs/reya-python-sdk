"""
Account resource for Reya Trading API.

This module provides account-related functionality.
"""
from typing import Dict, Any, Optional, List

from .base import BaseResource


class AccountResource(BaseResource):
    """Resource for account-related API endpoints."""
    
    def get_account(self, account_id: int) -> Dict[str, Any]:
        """
        Get account information.
        
        Args:
            account_id: The Reya account ID
            
        Returns:
            Account information
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"trading/account/{account_id}"
        return self._get(endpoint)
    
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
    
    def get_balance(self, account_id: int) -> Dict[str, Any]:
        """
        Get account balance.
        
        Args:
            account_id: The Reya account ID
            
        Returns:
            Account balance information
            
        Raises:
            ValueError: If the API returns an error
        """
        endpoint = f"trading/account/{account_id}/balance"
        return self._get(endpoint)
