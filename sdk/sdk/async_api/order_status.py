from enum import Enum

class OrderStatus(Enum): 
  PENDING = "PENDING"
  FILLED = "FILLED"
  CANCELLED = "CANCELLED"
  REJECTED = "REJECTED"