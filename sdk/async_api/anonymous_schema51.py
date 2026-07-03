from enum import Enum

class AnonymousSchema51(Enum): 
  INVALID_SIGNATURE = "InvalidSignature"
  SIGNATURE_INVALID = "SignatureInvalid"
  SIGNATURE_EXPIRED = "SignatureExpired"
  SMALL_ORDER_SIZE = "SmallOrderSize"
  DUSTY_ORDER_SIZE = "DustyOrderSize"
  INVALID_FILLED_EXPOSURES = "InvalidFilledExposures"
  STORK_PAYLOAD_OLDER_THAN_LATEST = "StorkPayloadOlderThanLatest"
  ZERO_SL_TP_ORDER_SIZE = "ZeroSlTpOrderSize"