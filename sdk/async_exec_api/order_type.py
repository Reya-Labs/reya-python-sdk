from enum import Enum

class OrderType(Enum): 
  LIMIT = "LIMIT"
  TP = "TP"
  SL = "SL"