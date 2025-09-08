from enum import Enum

class OrderStatus(Enum): 
  OPEN = "OPEN"
  FILLED = "FILLED"
  CANCELLED = "CANCELLED"
  REJECTED = "REJECTED"