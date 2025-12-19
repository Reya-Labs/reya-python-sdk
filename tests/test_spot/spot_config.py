"""
Centralized SPOT test configuration.

This module provides a SpotTestConfig dataclass that holds all test configuration
including dynamically fetched oracle prices. The config is injected via pytest
fixtures - no global state is used.

Usage in test files:
    @pytest.mark.asyncio
    async def test_something(spot_config: SpotTestConfig, maker_tester: ReyaTester):
        maker_price = spot_config.price(0.99)  # 99% of oracle price
        ...
"""

from dataclasses import dataclass


@dataclass
class SpotTestConfig:
    """
    Centralized configuration for SPOT tests.
    
    This dataclass is populated by a pytest fixture at session start,
    ensuring all tests use consistent, dynamically-fetched values.
    
    Attributes:
        symbol: The spot market symbol (e.g., "WETHRUSD")
        min_qty: Minimum order quantity as string (e.g., "0.001")
        oracle_price: Current oracle price for the underlying asset
    """
    symbol: str
    min_qty: str
    oracle_price: float
    
    def price(self, multiplier: float = 1.0) -> float:
        """
        Get a price based on oracle price with optional multiplier.
        
        Args:
            multiplier: Price multiplier (e.g., 0.99 for 99% of oracle)
            
        Returns:
            Rounded price to 2 decimal places
        """
        return round(self.oracle_price * multiplier, 2)
    
    def buy_price(self, multiplier: float = 0.99) -> float:
        """Get a buy price (default 99% of oracle - within deviation limit)."""
        return self.price(multiplier)
    
    def sell_price(self, multiplier: float = 1.01) -> float:
        """Get a sell price (default 101% of oracle - within deviation limit)."""
        return self.price(multiplier)
