"""
Configuration settings for the Reya Trading API.x
"""

from typing import Optional

import os
from dataclasses import dataclass

from dotenv import load_dotenv

MAINNET_CHAIN_ID = 1729
REYA_DEX_ID = 2


@dataclass
class TradingConfig:
    """Configuration for Reya Trading API"""

    api_url: str
    chain_id: int
    owner_wallet_address: str
    private_key: Optional[str] = None
    account_id: Optional[int] = None

    @property
    def is_mainnet(self) -> bool:
        """Determine if current chain ID is mainnet"""
        return self.chain_id == MAINNET_CHAIN_ID

    @property
    def dex_id(self) -> int:
        """Get DEX ID"""
        return REYA_DEX_ID

    @property
    def default_orders_gateway_address(self) -> str:
        """Get default OrdersGateway proxy contract address based on chain ID"""
        if self.is_mainnet:
            return "0xfc8c96be87da63cecddbf54abfa7b13ee8044739"  # Mainnet address
        else:
            return "0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5"  # Testnet address

    @property
    def pool_account_id(self) -> int:
        """Get pool account ID based on chain ID"""
        return 2 if self.is_mainnet else 4

    @classmethod
    def from_env(cls) -> "TradingConfig":
        """Create a config instance from environment variables."""
        load_dotenv()

        chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))

        # Get API URL based on environment (mainnet or testnet)
        if chain_id == MAINNET_CHAIN_ID:
            default_api_url = "https://api.reya.xyz/v2"
        else:
            default_api_url = "https://api-cronos.reya.xyz/v2"

        # Require PERP_WALLET_ADDRESS_1
        owner_wallet_address = os.environ.get("PERP_WALLET_ADDRESS_1")
        if not owner_wallet_address:
            raise ValueError(
                "PERP_WALLET_ADDRESS_1 environment variable is required. "
                "This should be the wallet address whose data you want to query."
            )

        return cls(
            api_url=os.environ.get("REYA_API_URL", default_api_url),
            chain_id=chain_id,
            owner_wallet_address=owner_wallet_address,
            private_key=os.environ.get("PERP_PRIVATE_KEY_1"),
            account_id=(int(os.environ["PERP_ACCOUNT_ID_1"]) if "PERP_ACCOUNT_ID_1" in os.environ else None),
        )

    @classmethod
    def from_env_spot(cls, account_number: int = 1) -> "TradingConfig":
        """Create a config instance from SPOT environment variables.
        
        Args:
            account_number: Which spot account to use (1 or 2)
        
        Returns:
            TradingConfig configured for the specified SPOT account
        
        Raises:
            ValueError: If required environment variables are not set
        """
        load_dotenv()

        if account_number not in (1, 2):
            raise ValueError(f"account_number must be 1 or 2, got {account_number}")

        chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))

        # Get API URL based on environment (mainnet or testnet)
        if chain_id == MAINNET_CHAIN_ID:
            default_api_url = "https://api.reya.xyz/v2"
        else:
            default_api_url = "https://api-cronos.reya.xyz/v2"

        # Get SPOT account credentials
        owner_wallet_address = os.environ.get(f"SPOT_WALLET_ADDRESS_{account_number}")
        if not owner_wallet_address:
            raise ValueError(
                f"SPOT_WALLET_ADDRESS_{account_number} environment variable is required. "
                "This should be the wallet address whose data you want to query."
            )

        private_key = os.environ.get(f"SPOT_PRIVATE_KEY_{account_number}")
        account_id_str = os.environ.get(f"SPOT_ACCOUNT_ID_{account_number}")
        account_id = int(account_id_str) if account_id_str else None

        return cls(
            api_url=os.environ.get("REYA_API_URL", default_api_url),
            chain_id=chain_id,
            owner_wallet_address=owner_wallet_address,
            private_key=private_key,
            account_id=account_id,
        )


def get_config() -> TradingConfig:
    """Get configuration from environment."""
    return TradingConfig.from_env()


def get_spot_config(account_number: int = 1) -> TradingConfig:
    """Get SPOT account configuration from environment.
    
    Args:
        account_number: Which spot account to use (1 or 2)
    
    Returns:
        TradingConfig configured for the specified SPOT account
    """
    return TradingConfig.from_env_spot(account_number)
