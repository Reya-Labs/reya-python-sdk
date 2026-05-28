"""Minimal ws-exec quickstart: place a resting spot order and cancel it.

This is the canonical user-facing pattern for the ws-exec service. The
sister script under :mod:`tests.ws_exec.mvp` covers every operation + every
error mode in a single run — use it as a reference for the full surface.

Requires .env populated with SPOT_*_1 credentials and CHAIN_ID.

Usage:
    poetry shell
    python -m examples.ws_exec.quickstart
"""

from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters
from sdk.reya_ws_exec import ReyaWsExecClient

DEFAULT_WS_EXEC_URL = "wss://ws-exec-testnet.reya.xyz"


async def main() -> None:
    load_dotenv()
    ws_url = os.environ.get("REYA_WS_EXEC_URL", DEFAULT_WS_EXEC_URL)

    rest = ReyaTradingClient(TradingConfig.from_env_spot(account_number=1))
    await rest.start()  # loads market definitions

    try:
        async with ReyaWsExecClient(rest_client=rest, ws_url=ws_url) as ws:
            create_resp = await ws.create_limit_order(
                LimitOrderParameters(
                    symbol="WETHRUSD",
                    is_buy=True,
                    limit_px="1",  # far below the book; this just rests
                    qty="0.001",
                    time_in_force=TimeInForce.GTC,
                )
            )
            print(f"created: orderId={create_resp.order_id} status={create_resp.status}")

            if create_resp.order_id is not None:
                cancel_resp = await ws.cancel_order(
                    order_id=create_resp.order_id,
                    symbol="WETHRUSD",
                    account_id=rest.config.account_id,
                )
                print(f"cancelled: orderId={cancel_resp.order_id} status={cancel_resp.status}")
    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
