from enum import Enum

class DisconnectNoticeReason(Enum): 
  SERVER_RESTART = "SERVER_RESTART"
  MAX_CONNECTION_AGE = "MAX_CONNECTION_AGE"