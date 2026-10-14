from enum import Enum

class MarketStatus(Enum): 
  PREOPEN = "PREOPEN"
  TRADING = "TRADING"
  UNKNOWN = "UNKNOWN"

  @classmethod
  def _missing_(cls, value: object) -> "MarketStatus":
    """Resolve a member added by the server since this SDK was generated."""
    return cls.UNKNOWN
