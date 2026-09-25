"""Minimal ws-exec quickstart: place a resting spot order and cancel it.

This is the canonical user-facing pattern for the ws-exec service. Broader
live regression coverage lives in :mod:`tests.ws_exec.test_ws_exec`.

Requires .env populated with SPOT_*_1 credentials and CHAIN_ID.

Usage:
    poetry shell
    python -m examples.websocket.exec.ws_exec
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

DEFAULT_WS_EXEC_URL = "wss://ws-exec-devnet.reya-cronos.network"
EXAMPLE_SYMBOL = "WETHRUSD"


def resolve_ws_exec_url() -> str:
    """Resolve ws-exec without requiring generated-code edits."""
    return os.environ.get("REYA_WS_EXEC_URL", DEFAULT_WS_EXEC_URL)


def build_example_order() -> LimitOrderParameters:
    """Build the resting order used by the quickstart.

    Kept separate from network setup so users and tests can validate the
    example payload offline.
    """
    return LimitOrderParameters(
        symbol=EXAMPLE_SYMBOL,
        is_buy=True,
        limit_px="1",  # far below the book; this just rests
        qty="0.001",
        time_in_force=TimeInForce.GTC,
    )


async def main() -> None:
    load_dotenv()
    ws_url = resolve_ws_exec_url()

    rest = ReyaTradingClient(TradingConfig.from_env_spot(account_number=1))
    await rest.start()  # loads market definitions

    try:
        async with ReyaWsExecClient(rest_client=rest, ws_url=ws_url) as ws:
            create_resp = await ws.create_limit_order(build_example_order())
            print(f"created: orderId={create_resp.order_id} status={create_resp.status}")

            if create_resp.order_id is not None:
                cancel_resp = await ws.cancel_order(
                    order_id=create_resp.order_id,
                    symbol=EXAMPLE_SYMBOL,
                    account_id=rest.config.account_id,
                )
                print(f"cancelled: orderId={cancel_resp.order_id} status={cancel_resp.status}")
    finally:
        await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
