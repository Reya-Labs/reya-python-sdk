"""Centralized test configuration for Reya Python SDK tests."""

from dataclasses import dataclass, field
from typing import Optional
import os

from dotenv import load_dotenv


@dataclass
class TestConfig:
    """
    Centralized test configuration.
    
    All test parameters and environment-specific settings are managed here,
    making it easy to adjust test behavior without modifying test code.
    """
    
    # API Configuration
    api_base_url: str = ""
    ws_base_url: str = ""
    
    # Account Configuration
    owner_wallet_address: str = ""
    default_account_id: Optional[int] = None
    maker_account_id: Optional[int] = None
    taker_account_id: Optional[int] = None
    chain_id: Optional[int] = None
    
    # Test Defaults - Spot Markets
    default_spot_symbol: str = "ETHRUSD"
    default_spot_qty: str = "0.01"
    
    # Test Defaults - Perp Markets
    default_perp_symbol: str = "ETHRUSDPERP"
    default_perp_qty: str = "0.01"
    
    # Timeouts (in seconds)
    default_timeout: int = 10
    ws_connection_timeout: int = 5
    order_execution_timeout: int = 10
    
    # Tolerances for assertions
    balance_tolerance: float = 0.001  # 0.1% tolerance for balance checks
    price_tolerance: float = 0.01     # 1% tolerance for price checks
    qty_tolerance: float = 0.0001     # Small tolerance for quantity comparisons
    
    # Polling intervals (in seconds)
    poll_interval: float = 0.1
    
    @classmethod
    def from_env(cls, env_file: Optional[str] = None) -> "TestConfig":
        """
        Load configuration from environment variables.
        
        Args:
            env_file: Optional path to .env file. If not provided,
                     will look for .env in the current directory.
        
        Returns:
            TestConfig instance populated from environment.
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()
        
        def get_optional_int(key: str) -> Optional[int]:
            value = os.getenv(key)
            if value is None or value == "":
                return None
            try:
                return int(value)
            except ValueError:
                return None
        
        return cls(
            api_base_url=os.getenv("API_BASE_URL", "https://api.reya.network"),
            ws_base_url=os.getenv("WS_BASE_URL", "wss://ws.reya.network"),
            owner_wallet_address=os.getenv("OWNER_WALLET_ADDRESS", ""),
            default_account_id=get_optional_int("ACCOUNT_ID"),
            maker_account_id=get_optional_int("MAKER_ACCOUNT_ID"),
            taker_account_id=get_optional_int("TAKER_ACCOUNT_ID"),
            chain_id=get_optional_int("CHAIN_ID"),
        )
    
    def get_account_id(self, account_type: str = "default") -> Optional[int]:
        """
        Get account ID by type.
        
        Args:
            account_type: One of "default", "maker", or "taker"
        
        Returns:
            The account ID or None if not configured.
        """
        account_map = {
            "default": self.default_account_id,
            "maker": self.maker_account_id,
            "taker": self.taker_account_id,
        }
        return account_map.get(account_type)
    
    def is_valid(self) -> bool:
        """Check if minimum required configuration is present."""
        return bool(
            self.owner_wallet_address
            and self.default_account_id is not None
        )
    
    def is_maker_taker_configured(self) -> bool:
        """Check if maker/taker accounts are configured for multi-account tests."""
        return bool(
            self.maker_account_id is not None
            and self.taker_account_id is not None
        )


# Global config instance (lazy-loaded)
_config: Optional[TestConfig] = None


def get_test_config() -> TestConfig:
    """Get the global test configuration instance."""
    global _config
    if _config is None:
        _config = TestConfig.from_env()
    return _config


def reset_test_config() -> None:
    """Reset the global config (useful for testing the config itself)."""
    global _config
    _config = None
