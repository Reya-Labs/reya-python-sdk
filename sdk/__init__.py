"""
Reya SDK - Python SDK for interacting with Reya services.

This package provides modules for interacting with different Reya services:
- reya_rest_api: For interacting with Reya's REST APIs
- reya_websocket: For consuming real-time data from Reya's WebSocket services
- reya_rpc: For making RPC calls to Reya's blockchain services
"""

SDK_VERSION = "1.0.1"  # Keep in sync with pyproject.toml

from sdk.reya_rest_api import ReyaTradingClient, TradingConfig
from sdk.reya_websocket import ReyaSocket
from sdk.reya_rpc import (
    get_config,
    create_account,
    deposit, 
    withdraw,
    bridge_in_from_arbitrum,
    bridge_out_to_arbitrum,
    stake,
    unstake,
    transfer,
    trade,
    update_oracle_prices,
)
