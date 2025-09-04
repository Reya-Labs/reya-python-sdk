from enum import Enum

class ExecutionType(Enum): 
  ORDER_MATCH = "ORDER_MATCH"
  LIQUIDATION = "LIQUIDATION"
  ADL = "ADL"