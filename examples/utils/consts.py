from enum import Enum


class CommandType(Enum):
    Deposit                       = 0
    Withdraw                      = 1
    DutchLiquidation              = 2
    MatchOrder                    = 3
    TransferBetweenMarginAccounts = 4

# Note: the list of markets keeps updating, please check with the team for the updated ids
class MarketIds(Enum):
    ETH    = 1
    BTC    = 2
    SOL    = 3
    ARB    = 4
    OP     = 5
    AVAX   = 6
    MKR    = 7
    LINK   = 8
    AAVE   = 9
    CRV    = 10
    UNI    = 11
    SUI    = 12
    TIA    = 13
    SEI    = 14
    ZRO    = 15
    XRP    = 16
    WIF    = 17
    kPEPE  = 18
    POPCAT = 19
    DOGE   = 20
    kSHIB  = 21
    kBONK  = 22
    APT    = 23
    BNB    = 24
    JTO    = 25
    ADA    = 26
    LDO    = 27
    POL    = 28
    NEAR   = 29
    FTM    = 30
    ENA    = 31
    EIGEN  = 32
    PENDLE = 33
    GOAT   = 34
    GRASS  = 35
    kNEIRO = 36
    DOT    = 37
    LTC    = 38
    PYTH   = 39
    JUP    = 40
    PENGU  = 41
    TRUMP  = 42
    HYPE   = 43
    VIRTUAL= 44
    AI16Z  = 45
    AIXBT  = 46
    S      = 47
    FARTCOIN = 48



class MarketTickers(Enum):
    ETH    = "ETH-rUSD"
    BTC    = "BTC-rUSD"
    SOL    = "SOL-rUSD"
    ARB    = "ARB-rUSD"
    OP     = "OP-rUSD"
    AVAX   = "AVAX-rUSD"
    MKR    = "MKR-rUSD"
    LINK   = "LINK-rUSD"
    AAVE   = "AAVE-rUSD"
    CRV    = "CRV-rUSD"
    UNI    = "UNI-rUSD"
    SUI    = "SUI-rUSD"
    TIA    = "TIA-rUSD"
    SEI    = "SEI-rUSD"
    ZRO    = "ZRO-rUSD"
    XRP    = "XRP-rUSD"
    WIF    = "WIF-rUSD"
    kPEPE  = "kPEPE-rUSD"
    POPCAT = "POPCAT-rUSD"
    DOGE   = "DOGE-rUSD"
    kSHIB  = "kSHIB-rUSD"
    kBONK  = "kBONK-rUSD"
    APT    = "APT-rUSD"
    BNB    = "BNB-rUSD"
    JTO    = "JTO-rUSD"
    ADA    = "ADA-rUSD"
    LDO    = "LDO-rUSD"
    POL    = "POL-rUSD"
    NEAR   = "NEAR-rUSD"
    FTM    = "FTM-rUSD"
    ENA    = "ENA-rUSD"
    EIGEN  = "EIGEN-rUSD"
    PENDLE = "PENDLE-rUSD"
    GOAT   = "GOAT-rUSD"
    GRASS  = "GRASS-rUSD"
    kNEIRO = "kNEIRO-rUSD"
    DOT    = "DOT-rUSD"
    LTC    = "LTC-rUSD"
    PYTH   = "PYTH-rUSD"
    JUP    = "JUP-rUSD"
    PENGU  = "PENGU-rUSD"
    TRUMP  = "TRUMP-rUSD"
    HYPE   = "HYPE-rUSD"
    VIRTUAL= "VIRTUAL-rUSD"
    AI16Z  = "AI16Z-rUSD"
    AIXBT  = "AIXBT-rUSD"
    S      = "S-rUSD"
    FARTCOIN = "FARTCOIN-rUSD"

class MarketPriceStreams(Enum):
    ETH = "ETHUSDMARK"
    BTC = "BTCUSDMARK"
    SOL = "SOLUSDMARK"
    ARB = "ARBUSDMARK"
    OP  = "OPUSDMARK"
    AVAX = "AVAXUSDMARK"
    MKR = "MKRUSDMARK"
    LINK = "LINKUSDMARK"
    AAVE = "AAVEUSDMARK"
    CRV = "CRVUSDMARK"
    UNI = "UNIUSDMARK"
    SUI = "SUIUSDMARK"
    TIA = "TIAUSDMARK"
    SEI = "SEIUSDMARK"
    ZRO = "ZROUSDMARK"
    XRP = "XRPUSDMARK"
    WIF = "WIFUSDMARK"
    kPEPE = "1000PEPEUSDMARK"
    POPCAT = "POPCATUSDMARK"
    DOGE = "DOGEUSDMARK"
    kSHIB = "1000SHIBUSDMARK"
    kBONK = "1000BONKUSDMARK"
    APT = "APTUSDMARK"
    BNB = "BNBUSDMARK"
    JTO = "JTOUSDMARK"

COLLATERAL_PRICE_STREAMS = ["USDCUSD","DEUSDUSD","SDEUSDDEUSD","USDEUSD","SUSDEUSD","ETHUSD"]
ALL_PRICE_STREAMS = COLLATERAL_PRICE_STREAMS + [o.value for o in MarketPriceStreams]