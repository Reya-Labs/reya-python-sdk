"""Market-parametrized ws-exec harness shared by the engine ws-exec suites.

Each engine ws-exec suite (gtt / modify / post_only / cod) pins the WS-EXEC
TRANSPORT over BOTH markets (spot, perp); the engine SEMANTICS are proven
transport-independently by the REST ``[spot, perp]``-parametrized suites (the
ws-exec client reuses the REST ``build_*_payload`` signing path verbatim, so the
two transports put byte-identical signed envelopes on the wire). This builder
yields a connected ws-exec client for a given market plus the symbol / min_qty /
passive resting price the transport tests need. Spot missing-prerequisite paths
and ws-exec URL/credential gaps fail by default so coverage cannot silently
disappear.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_ws_exec import ReyaWsExecClient
from tests.helpers.ws_exec_prerequisites import missing_env_vars, ws_exec_account_env_vars, ws_exec_prerequisite_missing

load_dotenv()

# Symbols + passive resting prices mirror tests/ws_exec/test_ws_exec.py. A far-
# from-touch buy rests without crossing: spot $1 on an ETH-priced book; perp
# $100 is far below the mark and rests as an OrdersGateway conditional (so it is
# not subject to the perp IOC execution band).
_SPOT_SYMBOL = "WETHRUSD"
_PERP_SYMBOL = "ETHRUSDPERP"
_SPOT_REST_BUY_PX = "1"
_PERP_REST_BUY_PX = "100"

# Both markets need the ws-exec URL; each market needs its own account creds.
_COMMON_ENV = ("REYA_WS_EXEC_URL",)
_SPOT_ENV = ws_exec_account_env_vars("SPOT", 1)
_PERP_ENV = ws_exec_account_env_vars("PERP", 1)

WS_EXEC_MARKETS = ("spot", "perp")


@dataclass
class WsExecMarket:
    """A connected ws-exec client + the market metadata a transport test needs."""

    market_type: str  # "spot" | "perp"
    rest: ReyaTradingClient
    ws: ReyaWsExecClient
    symbol: str
    min_qty: str
    tick_size: str
    rest_buy_px: str  # a passive, far-from-touch buy price that rests (never crosses)


@asynccontextmanager
async def build_ws_exec_market(market_type: str) -> AsyncIterator[WsExecMarket]:
    """Build a connected ws-exec client + market metadata for ``market_type``.

    Missing ws-exec URL/account prerequisites fail by default. Cleans up the
    REST + ws clients on exit even when a test fails. Mirrors the per-suite spot
    harnesses it replaces."""
    if market_type not in WS_EXEC_MARKETS:
        raise ValueError(f"unknown ws-exec market: {market_type!r}")

    market_env = _SPOT_ENV if market_type == "spot" else _PERP_ENV
    missing = missing_env_vars(_COMMON_ENV + market_env)
    if missing:
        reason = f"ws-exec {market_type} tests need {', '.join(_COMMON_ENV + market_env)}"
        ws_exec_prerequisite_missing(reason, missing_env=missing)

    if market_type == "spot":
        config = TradingConfig.from_env_spot(account_number=1)
        symbol, rest_buy_px = _SPOT_SYMBOL, _SPOT_REST_BUY_PX
    else:
        config = TradingConfig.from_env()  # the default config is the perp account
        symbol, rest_buy_px = _PERP_SYMBOL, _PERP_REST_BUY_PX

    rest = ReyaTradingClient(config)
    await rest.start()
    ws: ReyaWsExecClient | None = None
    try:
        # Resolve min_qty per branch — spot/perp definitions are distinct types,
        # so a shared dict would not type-check.
        if market_type == "spot":
            spot_defs = {d.symbol: d for d in await rest.reference.get_spot_market_definitions()}
            if symbol not in spot_defs:
                ws_exec_prerequisite_missing(f"{symbol} not found in /v2/spotMarketDefinitions")
            min_qty = str(spot_defs[symbol].min_order_qty)
            tick_size = str(spot_defs[symbol].tick_size)
        else:
            perp_defs = {d.symbol: d for d in await rest.reference.get_perp_market_definitions()}
            if symbol not in perp_defs:
                ws_exec_prerequisite_missing(f"{symbol} not found in /v2/perpMarketDefinitions")
            min_qty = str(perp_defs[symbol].min_order_qty)
            tick_size = str(perp_defs[symbol].tick_size)
        ws = ReyaWsExecClient(rest_client=rest, ws_url=os.environ["REYA_WS_EXEC_URL"])
        await ws.connect()
        yield WsExecMarket(
            market_type=market_type,
            rest=rest,
            ws=ws,
            symbol=symbol,
            min_qty=min_qty,
            tick_size=tick_size,
            rest_buy_px=rest_buy_px,
        )
    finally:
        if ws is not None:
            await ws.close()
        await rest.close()
