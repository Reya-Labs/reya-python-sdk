"""
Enumeration classes for Reya Trading API.
"""

from typing import Union

from dataclasses import dataclass
from enum import Enum, IntEnum


class TimeInForce(str, Enum):
    """Time in force for limit orders"""

    IOC = "IOC"  # Immediate or Cancel
    GTC = "GTC"  # Good Till Cancel


class TpslType(str, Enum):
    """TPSL (Take Profit/Stop Loss) order types"""

    TP = "TP"  # Take Profit
    SL = "SL"  # Stop Loss


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


class ConditionalOrderStatus(str, Enum):
    """Order status values"""

    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


@dataclass
class Limit:
    time_in_force: TimeInForce


@dataclass
class Trigger:
    trigger_px: str
    tpsl: TpslType


@dataclass
class LimitOrderType:
    limit: Limit

    def to_dict(self):
        return {"limit": {"timeInForce": self.limit.time_in_force.value}}


@dataclass
class TriggerOrderType:
    trigger: Trigger

    def to_dict(self):
        return {"trigger": {"triggerPx": self.trigger.trigger_px, "tpsl": self.trigger.tpsl}}


UnifiedOrderType = Union[LimitOrderType, TriggerOrderType]
