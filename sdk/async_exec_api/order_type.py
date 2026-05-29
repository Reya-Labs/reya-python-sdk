from enum import Enum

class OrderType(Enum): 
  LIMIT = "LIMIT"
  STOP_LOSS = "STOP_LOSS"
  TAKE_PROFIT = "TAKE_PROFIT"