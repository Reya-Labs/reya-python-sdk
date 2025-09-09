"""
Enumeration classes for Reya Trading API.
"""

from enum import IntEnum


class OrdersGatewayOrderType(IntEnum):
    """Enum representing orders gateway order types"""

    STOP_LOSS = 0
    TAKE_PROFIT = 1
    LIMIT_ORDER = 2
    MARKET_ORDER = 3
    REDUCE_ONLY_MARKET_ORDER = 4
