from enum import Enum

class ExecutionType(Enum): 
  ORDER_MATCH = "ORDER_MATCH"
  LIQUIDATION = "LIQUIDATION"
  ADL = "ADL"
  MARKET_CLOSE = "MARKET_CLOSE"
  UNKNOWN = "UNKNOWN"

  @classmethod
  def _missing_(cls, value: object) -> "ExecutionType":
    """Resolve a member added by the server since this SDK was generated."""
    return cls.UNKNOWN
