"""Throwaway probe: submit a single perp IOC order via the REST API using the
SAME account + market + price + qty as the ws-exec MVP. Lets us tell whether
the on-chain revert we're seeing in ws-exec is reproducible via REST too —
which would point at a chain-side problem (allowlist, account state) rather
than a ws-exec wiring bug."""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("rest_perp_ioc_probe")


async def main():
    load_dotenv()
    async with ReyaTradingClient() as client:
        log.info(
            "client ready: accountId=%s chain=%s wallet=%s api=%s",
            client.config.account_id,
            client.config.chain_id,
            client.owner_wallet_address,
            client.config.api_url,
        )
        await client.start()

        # Same params as the ws-exec MVP Flow 4
        # Realistic limit so the price-limit check passes on-chain.
        # ETHRUSDPERP ~ $3k; buying with a $40k limit fills at mark.
        params = LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            limit_px="40000",
            qty="0.01",
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
        )
        try:
            resp = await client.create_limit_order(params)
            log.info("REST perp IOC OK: orderId=%s status=%s", resp.order_id, resp.status)
        except Exception as exc:
            log.error("REST perp IOC FAILED: %s", exc)
            raise


if __name__ == "__main__":
    asyncio.run(main())
