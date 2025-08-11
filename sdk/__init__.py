"""
Reya SDK - Python SDK for interacting with Reya services.

This package provides modules for interacting with different Reya services:
- reya_rest_api: For interacting with Reya's REST APIs
- reya_websocket: For consuming real-time data from Reya's WebSocket services
- reya_rpc: For making RPC calls to Reya's blockchain services
"""

from sdk._version import SDK_VERSION
from sdk.reya_rest_api import ReyaTradingClient, TradingConfig
from sdk.reya_rpc import (
    bridge_in_from_arbitrum,
    bridge_out_to_arbitrum,
    create_account,
    deposit,
    get_config,
    stake,
    trade,
    transfer,
    unstake,
    update_oracle_prices,
    withdraw,
)
from sdk.reya_websocket import ReyaSocket

__all__ = [
    "SDK_VERSION",
    "ReyaTradingClient",
    "TradingConfig",
    "ReyaSocket",
    "bridge_in_from_arbitrum",
    "bridge_out_to_arbitrum",
    "create_account",
    "deposit",
    "get_config",
    "stake",
    "trade",
    "transfer",
    "unstake",
    "update_oracle_prices",
    "withdraw",
]
