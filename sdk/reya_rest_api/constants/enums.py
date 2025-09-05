"""
Enumeration classes for Reya Trading API.
"""

from enum import Enum, IntEnum


class ConditionalOrderType(IntEnum):
    """Enum representing conditional order types"""

    STOP_LOSS = 0
    TAKE_PROFIT = 1
    LIMIT_ORDER = 2


class OrdersGatewayOrderType(IntEnum):
    """Enum representing orders gateway order types"""

    STOP_LOSS = 0
    TAKE_PROFIT = 1
    LIMIT_ORDER = 2
    MARKET_ORDER = 3
    REDUCE_ONLY_MARKET_ORDER = 4


# used for signatures
class ConditionalOrderStatus(str, Enum):
    """Order status values"""

    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
