"""
Configuration settings for the Reya Trading API.
"""

from typing import Optional

import os
from dataclasses import dataclass

from dotenv import load_dotenv

MAINNET_CHAIN_ID = 1729
REYA_DEX_ID = 5


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

        # Require OWNER_WALLET_ADDRESS
        owner_wallet_address = os.environ.get("OWNER_WALLET_ADDRESS")
        if not owner_wallet_address:
            raise ValueError(
                "OWNER_WALLET_ADDRESS environment variable is required. "
                "This should be the wallet address whose data you want to query."
            )

        return cls(
            api_url=os.environ.get("REYA_API_URL", default_api_url),
            chain_id=chain_id,
            owner_wallet_address=owner_wallet_address,
            private_key=os.environ.get("PRIVATE_KEY"),
            account_id=(int(os.environ["ACCOUNT_ID"]) if "ACCOUNT_ID" in os.environ else None),
        )


def get_config() -> TradingConfig:
    """Get configuration from environment."""
    return TradingConfig.from_env()
