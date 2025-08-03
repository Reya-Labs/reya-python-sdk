"""
Enumeration classes for Reya Trading API.
"""
from enum import Enum, IntEnum
from typing import Dict, List, Any, Union
from dataclasses import dataclass


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
    timeInForce: TimeInForce

@dataclass
class Trigger:
    triggerPx: str
    tpsl: TpslType

@dataclass
class LimitOrderType:
    limit: Limit
    
    def to_dict(self):
        return {
            "limit": {
                "timeInForce": self.limit.timeInForce.value
            }
        }

@dataclass
class TriggerOrderType:
    trigger: Trigger
    
    def to_dict(self):
        return {
            "trigger": {
                "triggerPx": self.trigger.triggerPx,
                "tpsl": self.trigger.tpsl.value
            }
        }

UnifiedOrderType = Union[LimitOrderType, TriggerOrderType]
