"""
Configuration settings for the Reya Trading API.x
"""

from typing import Optional

import os
from dataclasses import dataclass

from dotenv import load_dotenv

MAINNET_CHAIN_ID = 1729

# Default exchange id resolved at import time. Set REYA_DEX_ID in the
# environment to override (e.g., devnet1 only registers exchange id 1).
# `TradingConfig.dex_id_override` still wins per-instance if set.
REYA_DEX_ID = int(os.environ.get("REYA_DEX_ID", "2"))


@dataclass
class TradingConfig:
    """Configuration for Reya Trading API"""

    api_url: str
    chain_id: int
    owner_wallet_address: str
    private_key: Optional[str] = None
    account_id: Optional[int] = None
    orders_gateway_address: Optional[str] = None
    dex_id_override: Optional[int] = None

    @property
    def is_mainnet(self) -> bool:
        """Determine if current chain ID is mainnet"""
        return self.chain_id == MAINNET_CHAIN_ID

    @property
    def dex_id(self) -> int:
        """Exchange id used as `OrderDetails.exchangeId` in signed orders.

        Resolves to the ``REYA_DEX_ID`` env var when set (via
        ``from_env``/``from_env_spot``), otherwise the canonical default.
        The override exists because non-mainnet deployments (devnet1,
        future testnets) may not have registered the canonical id-2
        exchange yet — using id 1 (passive pool) lets order-entry tests
        run end-to-end on those environments. Switch back to the
        default once the target deployment registers id 2.
        """
        if self.dex_id_override is not None:
            return self.dex_id_override
        return REYA_DEX_ID

    @property
    def default_orders_gateway_address(self) -> str:
        """OrdersGateway proxy contract address used as the EIP-712 verifyingContract.

        Resolution order: explicit ``orders_gateway_address`` (set via the
        ``REYA_ORDERS_GATEWAY`` env var in ``from_env``/``from_env_spot``) wins,
        otherwise fall back to the chain-id default. The override exists because
        non-mainnet deployments (devnet1, future testnets) redeploy the
        OrdersGateway proxy and a stale baked-in address makes the matching
        engine reject every signature.
        """
        if self.orders_gateway_address:
            return self.orders_gateway_address
        # OrdersGateway proxy = the EIP-712 verifyingContract, per deployment.
        # NOTE: devnet1 (the perpOB testnet) and the cronos testnet share chain
        # id 89346162 but use different proxy deployments, so they cannot be
        # distinguished by chain id alone. Non-mainnet defaults to devnet1 (the
        # current perpOB target); set REYA_ORDERS_GATEWAY to the cronos value to
        # target the cronos deployment instead.
        orders_gateway_by_env = {
            "mainnet": "0xfc8c96be87da63cecddbf54abfa7b13ee8044739",
            "cronos_testnet": "0x5a0ac2f89e0bdeafc5c549e354842210a3e87ca5",
            "devnet1": "0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D",
        }
        return orders_gateway_by_env["mainnet" if self.is_mainnet else "devnet1"]

    @classmethod
    def from_env(cls) -> "TradingConfig":
        """Create a config instance from environment variables."""
        load_dotenv()

        chain_id = int(os.environ.get("CHAIN_ID", MAINNET_CHAIN_ID))

        # Get API URL based on environment (mainnet or devnet1, the perpOB testnet)
        if chain_id == MAINNET_CHAIN_ID:
            default_api_url = "https://api.reya.xyz/v2"
        else:
            default_api_url = "https://api-devnet.reya-cronos.network/v2"

        # Require PERP_WALLET_ADDRESS_1
        owner_wallet_address = os.environ.get("PERP_WALLET_ADDRESS_1")
        if not owner_wallet_address:
            raise ValueError(
                "PERP_WALLET_ADDRESS_1 environment variable is required. "
                "This should be the wallet address whose data you want to query."
            )

        dex_id_env = os.environ.get("REYA_DEX_ID")
        return cls(
            api_url=os.environ.get("REYA_API_URL", default_api_url),
            chain_id=chain_id,
            owner_wallet_address=owner_wallet_address,
            private_key=os.environ.get("PERP_PRIVATE_KEY_1"),
            account_id=(int(os.environ["PERP_ACCOUNT_ID_1"]) if "PERP_ACCOUNT_ID_1" in os.environ else None),
            orders_gateway_address=os.environ.get("REYA_ORDERS_GATEWAY"),
            dex_id_override=int(dex_id_env) if dex_id_env else None,
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

        # Get API URL based on environment (mainnet or devnet1, the perpOB testnet)
        if chain_id == MAINNET_CHAIN_ID:
            default_api_url = "https://api.reya.xyz/v2"
        else:
            default_api_url = "https://api-devnet.reya-cronos.network/v2"

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

        dex_id_env = os.environ.get("REYA_DEX_ID")
        return cls(
            api_url=os.environ.get("REYA_API_URL", default_api_url),
            chain_id=chain_id,
            owner_wallet_address=owner_wallet_address,
            private_key=private_key,
            account_id=account_id,
            orders_gateway_address=os.environ.get("REYA_ORDERS_GATEWAY"),
            dex_id_override=int(dex_id_env) if dex_id_env else None,
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
