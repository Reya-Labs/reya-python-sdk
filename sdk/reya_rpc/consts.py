from sdk.reya_rpc.types import MarketPriceStreams

COLLATERAL_PRICE_STREAMS = [
    "USDCUSD",
    "DEUSDUSD",
    "SDEUSDDEUSD",
    "USDEUSD",
    "SUSDEUSD",
    "ETHUSD",
]
ALL_PRICE_STREAMS = COLLATERAL_PRICE_STREAMS + [o.value for o in MarketPriceStreams]
