"""Probe which perp markets on devnet have order-entry enabled.

Walks every perp market in /v2/marketDefinitions and tries to post a tiny
far-from-market GTC sell as PERP_ACCOUNT_ID_1. Cancels each one immediately
on success. Used to find which symbols are past the matching-engine
PERP_OB_MARKET_IDS launch gate without doing any real trading.
"""

from __future__ import annotations

import asyncio
import logging

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models import LimitOrderParameters

logging.basicConfig(level=logging.WARNING)


async def main() -> None:
    client = ReyaTradingClient()
    await client.start()
    try:
        defs = await client.reference.get_perp_market_definitions()
        perp_defs = [d for d in defs if d.symbol.endswith("PERP")]
        print(f"Found {len(perp_defs)} perp market(s): {[d.symbol for d in perp_defs]}")

        for d in perp_defs:
            symbol = d.symbol
            # Get oracle price; place far-below sell so it can't match.
            try:
                price_resp = await client.markets.get_price(symbol)
                oracle = float(price_resp.oracle_price)
            except ApiException as e:
                print(f"  {symbol}: NO ORACLE PRICE ({e.body if hasattr(e, 'body') else e})")
                continue

            far_sell_px = str(round(oracle * 2.0, 2))
            params = LimitOrderParameters(
                symbol=symbol,
                is_buy=False,
                limit_px=far_sell_px,
                qty=str(d.min_order_qty),
                time_in_force=TimeInForce.GTC,
            )

            try:
                resp = await client.create_limit_order(params)
                order_id = resp.order_id
                print(f"  {symbol}: ✅ ORDER ENTRY ENABLED (order_id={order_id})")
                if order_id:
                    try:
                        await client.cancel_order(
                            symbol=symbol,
                            account_id=client.config.account_id,
                            order_id=order_id,
                        )
                    except ApiException as cancel_err:
                        print(f"    (cancel failed: {cancel_err})")
            except ApiException as e:
                msg = str(e)
                if "Perp order entry is not enabled" in msg:
                    print(f"  {symbol}: ❌ GATED (Perp order entry not enabled)")
                else:
                    print(f"  {symbol}: ❌ OTHER ERROR — {msg.splitlines()[-1] if msg else e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
