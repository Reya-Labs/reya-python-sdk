from enum import Enum

class WsExecErrorCode(Enum): 
  MALFORMED_JSON = "MALFORMED_JSON"
  UNKNOWN_TYPE = "UNKNOWN_TYPE"
  DUPLICATE_REQUEST_ID = "DUPLICATE_REQUEST_ID"
  SERVER_SHUTTING_DOWN = "SERVER_SHUTTING_DOWN"
  TOO_MANY_INFLIGHT = "TOO_MANY_INFLIGHT"
  INTERNAL = "INTERNAL"
  UNKNOWN = "UNKNOWN"

  @classmethod
  def _missing_(cls, value: object) -> "WsExecErrorCode":
    """Resolve a member added by the server since this SDK was generated."""
    return cls.UNKNOWN
