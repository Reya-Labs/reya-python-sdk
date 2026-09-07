from enum import Enum

class OrderStatus(Enum): 
  OPEN = "OPEN"
  FILLED = "FILLED"
  CANCELLED = "CANCELLED"
  UNKNOWN = "UNKNOWN"

  @classmethod
  def _missing_(cls, value: object) -> "OrderStatus":
    """Resolve a member added by the server since this SDK was generated."""
    return cls.UNKNOWN
