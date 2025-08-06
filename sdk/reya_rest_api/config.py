"""
Configuration settings for the Reya Trading API.
"""
import os
from dataclasses import dataclass
from typing import Dict, Optional, Any
from dotenv import load_dotenv

MAINNET_CHAIN_ID = 1729
REYA_DEX_ID = 2

@dataclass
class TradingConfig:
    """Configuration for Reya Trading API"""
    api_url: str
    chain_id: int
    private_key: Optional[str] = None
    orders_gateway_address: Optional[str] = None
    account_id: Optional[int] = None
    wallet_address: Optional[str] = None
    
    @property
    def is_mainnet(self) -> bool:
        """Determine if current chain ID is mainnet"""
        return self.chain_id == MAINNET_CHAIN_ID

    @property
    def dex_id(self) -> int:
        """Get DEX ID based on chain ID"""
        return REYA_DEX_ID
    
    @property
    def default_orders_gateway_address(self) -> str:
        """Get default OrdersGateway proxy contract address based on chain ID"""
        if self.is_mainnet:
            return "0xfc8c96be87da63cecddbf54abfa7b13ee8044739"  # Mainnet address
        else:
            return "0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5"  # Testnet address
    
    @property
    def default_conditional_orders_address(self) -> str:
        """Get default ConditionalOrders proxy contract address based on chain ID"""
        if self.is_mainnet:
            return "0xfc8c96be87da63cecddbf54abfa7b13ee8044739"  # Mainnet address
        else:
            return "0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5"  # Testnet address
    
    @property
    def pool_account_id(self) -> int:
        """Get pool account ID based on chain ID"""
        return 2 if self.is_mainnet else 4
    
    @classmethod
    def from_env(cls) -> 'TradingConfig':
        """Create a config instance from environment variables."""
        load_dotenv()
        
        chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))
        
        # Get API URL based on environment (mainnet or testnet)
        if chain_id == MAINNET_CHAIN_ID:
            # TODO: change this !!!
            default_api_url = "http://localhost:8000"
        else:
            default_api_url = "https://api-cronos.reya.xyz/"
        
        return cls(
            api_url=os.environ.get("REYA_API_URL", default_api_url),
            chain_id=chain_id,
            private_key=os.environ.get("PRIVATE_KEY"),
            orders_gateway_address=None,  # Will use default based on chain_id
            account_id=int(os.environ.get("ACCOUNT_ID")) if "ACCOUNT_ID" in os.environ else None,
            wallet_address=os.environ.get("WALLET_ADDRESS")
        )

def get_config() -> TradingConfig:
    """Get configuration from environment."""
    return TradingConfig.from_env()
