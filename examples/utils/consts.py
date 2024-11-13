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