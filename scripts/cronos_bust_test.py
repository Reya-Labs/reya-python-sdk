#!/usr/bin/env python3
"""
Cronos bust-test helper.

Sets up the two partial-bust scenarios the off-chain team uses to exercise
the SpotExecutionBust / fillOrderBytes path end-to-end on cronos.

Both scenarios rely on the cronos deployment running with the deadline
checks disabled in api + matching-engine (env var
``DISABLE_ORDER_DEADLINE_CHECKS=true`` — see the matching feature branch in
reya-off-chain-monorepo and reya-chain). On any other environment the
api/ME will reject orders with a past deadline before they reach the book.

Scenarios
---------

IDEA 1 — resting maker that absorbs both clean fills and busts:

  * account A (SPOT_*_1, the "UI" / cristian's account) rests a single
    GTC at the limit price (default: 1% inside oracle on the buy side).
  * account B (SPOT_*_2) sweeps with 3 IOC takes (normal deadlines) that
    settle on-chain, then 3 IOC takes with a past deadline. The 3 stale
    ones match in the ME against A's rester and then bust on-chain.

IDEA 2 — aggressor consumes resting orders, some of which bust:

  * account B (SPOT_*_2) places 6 GTC orders against A. Half have a fresh
    24h deadline; the other half are signed with a deadline well in the
    past so they bust the moment they get matched.
  * the aggressive 100-qty taker comes from the UI (Cristian drives that
    by hand from the app), so the script just seeds the maker side and
    waits for the user to fire the take. We print clOrdIds + a one-line
    summary so the UI dev can confirm what they should see in the book.

Usage
-----

    poetry shell
    python -m scripts.cronos_bust_test --test 1
    python -m scripts.cronos_bust_test --test 2 --symbol WBTCRUSD --qty 0.001

Required env (cronos):

    CHAIN_ID=89346162
    SPOT_WALLET_ADDRESS_1=...    # account A — UI / cristian's
    SPOT_PRIVATE_KEY_1=...
    SPOT_ACCOUNT_ID_1=...
    SPOT_WALLET_ADDRESS_2=...    # account B — script's resting/taking side
    SPOT_PRIVATE_KEY_2=...
    SPOT_ACCOUNT_ID_2=...
"""

from __future__ import annotations

from typing import Optional

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from decimal import Decimal

from dotenv import load_dotenv

from sdk.open_api.models import TimeInForce
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.create_order_response import CreateOrderResponse
from sdk.open_api.models.order_type import OrderType
from sdk.reya_rest_api import ReyaTradingClient, get_spot_config
from sdk.reya_rest_api.constants.enums import OrdersGatewayOrderType

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
logger = logging.getLogger("cronos_bust_test")


# We sign orders with a deadline 5 minutes in the past so the on-chain
# settlement clock-skew tolerance can't accidentally accept them.
PAST_DEADLINE_OFFSET_S = 5 * 60


@dataclass(frozen=True)
class TestParams:
    symbol: str
    rester_qty: Decimal
    take_qty: Decimal
    num_normal_takes: int
    num_bust_takes: int
    num_resting_orders: int  # only used in IDEA 2
    buy_limit_multiplier: Decimal  # rester buy price / oracle
    sell_limit_multiplier: Decimal  # rester sell price / oracle


async def make_client(account_number: int) -> ReyaTradingClient:
    config = get_spot_config(account_number=account_number)
    if config.account_id is None:
        raise RuntimeError(f"SPOT_ACCOUNT_ID_{account_number} is required")
    if config.private_key is None:
        raise RuntimeError(f"SPOT_PRIVATE_KEY_{account_number} is required")
    client = ReyaTradingClient(config)
    await client.start()
    return client


async def get_oracle_price(client: ReyaTradingClient, symbol: str) -> Decimal:
    """Return a price we can quote around.

    Prefers the midpoint of the live book; falls back to whichever side has
    liquidity. Cronos sometimes has an empty book — in that case the user
    has to supply --price.
    """
    depth = await client.markets.get_market_depth(symbol=symbol)
    best_bid = Decimal(depth.bids[0].px) if depth.bids else None
    best_ask = Decimal(depth.asks[0].px) if depth.asks else None
    if best_bid and best_ask:
        return (best_bid + best_ask) / 2
    if best_bid:
        return best_bid
    if best_ask:
        return best_ask
    raise RuntimeError(f"Order book for {symbol} is empty — pass --price to seed quotes manually.")


async def submit_spot_order(
    client: ReyaTradingClient,
    *,
    symbol: str,
    is_buy: bool,
    limit_px: Decimal,
    qty: Decimal,
    time_in_force: TimeInForce,
    deadline_override: Optional[int] = None,
    client_order_id: Optional[int] = None,
) -> CreateOrderResponse:
    """Place a spot order with an arbitrary deadline.

    The SDK refuses to set ``expires_after`` on GTC orders (see
    ``ReyaTradingClient.create_limit_order``). This helper rebuilds the
    request manually so the script can plant resting GTCs with a past
    deadline.
    """
    if client._signature_generator is None:
        raise RuntimeError("Client has no signature generator (private key missing)")
    if client.config.account_id is None:
        raise RuntimeError("Account ID missing on client config")

    market_id = client._get_market_id_from_symbol(symbol)
    if not client._is_spot_market(symbol):
        raise RuntimeError(f"This script is spot-only. Symbol {symbol!r} resolves to a perp market.")

    nonce = client._get_next_nonce()
    inputs = client._signature_generator.encode_inputs_limit_order(
        is_buy=is_buy,
        limit_px=limit_px,
        qty=qty,
    )

    if deadline_override is not None:
        deadline = deadline_override
    elif time_in_force == TimeInForce.IOC:
        deadline = int(time.time()) + 10
    else:
        deadline = int(time.time()) + 86400  # match SDK GTC default

    signature = client._signature_generator.sign_raw_order(
        account_id=client.config.account_id,
        market_id=market_id,
        exchange_id=client.config.dex_id,
        counterparty_account_ids=[],  # spot: matched against book, no pool
        order_type=OrdersGatewayOrderType.LIMIT_ORDER_SPOT,
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    request = CreateOrderRequest(
        accountId=client.config.account_id,
        symbol=symbol,
        exchangeId=client.config.dex_id,
        isBuy=is_buy,
        limitPx=str(limit_px),
        qty=str(qty),
        orderType=OrderType.LIMIT,
        timeInForce=time_in_force,
        expiresAfter=deadline,
        reduceOnly=None,
        signature=signature,
        nonce=str(nonce),
        signerWallet=client.signer_wallet_address,
        clientOrderId=client_order_id,
    )
    return await client.orders.create_order(create_order_request=request)


def _fmt_response(label: str, resp: CreateOrderResponse) -> str:
    return f"{label}: orderId={resp.order_id}"


async def run_idea_one(params: TestParams, price: Decimal) -> None:
    """A rests one big GTC; B sweeps it with normal then stale IOCs."""
    account_a = await make_client(1)
    account_b = await make_client(2)
    try:
        rester_price = (price * params.sell_limit_multiplier).quantize(Decimal("0.01"))
        taker_price = (price * params.sell_limit_multiplier * Decimal("1.001")).quantize(Decimal("0.01"))
        logger.info("=" * 72)
        logger.info("IDEA 1 — resting GTC absorbs clean fills then busts")
        logger.info("=" * 72)
        logger.info(
            "A places GTC SELL %s %s @ %s; B sweeps with %d normal + %d stale IOC BUY @ %s",
            params.rester_qty,
            params.symbol,
            rester_price,
            params.num_normal_takes,
            params.num_bust_takes,
            taker_price,
        )

        # Step 1 — A places the resting GTC sell.
        rester = await submit_spot_order(
            account_a,
            symbol=params.symbol,
            is_buy=False,
            limit_px=rester_price,
            qty=params.rester_qty,
            time_in_force=TimeInForce.GTC,
        )
        logger.info(_fmt_response("Rester GTC", rester))

        # Step 2 — B clean IOC takes.
        for i in range(params.num_normal_takes):
            resp = await submit_spot_order(
                account_b,
                symbol=params.symbol,
                is_buy=True,
                limit_px=taker_price,
                qty=params.take_qty,
                time_in_force=TimeInForce.IOC,
            )
            logger.info(_fmt_response(f"Clean IOC #{i + 1}", resp))

        # Step 3 — B stale IOC takes (deadline in the past → on-chain bust).
        stale_deadline = int(time.time()) - PAST_DEADLINE_OFFSET_S
        for i in range(params.num_bust_takes):
            resp = await submit_spot_order(
                account_b,
                symbol=params.symbol,
                is_buy=True,
                limit_px=taker_price,
                qty=params.take_qty,
                time_in_force=TimeInForce.IOC,
                deadline_override=stale_deadline,
            )
            logger.info(_fmt_response(f"Stale IOC #{i + 1}", resp))

        logger.info(
            "Done. Expect %d successful fills + %d busted fills on the chain.",
            params.num_normal_takes,
            params.num_bust_takes,
        )
    finally:
        await account_a.close()
        await account_b.close()


async def run_idea_two(params: TestParams, price: Decimal) -> None:
    """B plants 6 resting GTCs (half stale); A takes from the UI."""
    account_b = await make_client(2)
    try:
        sell_price = (price * params.sell_limit_multiplier).quantize(Decimal("0.01"))
        logger.info("=" * 72)
        logger.info("IDEA 2 — half-stale resting book waiting on a UI-driven take")
        logger.info("=" * 72)
        total = params.num_resting_orders
        bust_count = total // 2
        normal_count = total - bust_count
        per_order_qty = (params.rester_qty / total).quantize(Decimal("0.0001"))
        logger.info(
            "B plants %d GTC SELL @ %s, qty %s each (%d stale, %d fresh). Symbol=%s",
            total,
            sell_price,
            per_order_qty,
            bust_count,
            normal_count,
            params.symbol,
        )

        stale_deadline = int(time.time()) - PAST_DEADLINE_OFFSET_S

        # Interleave stale + fresh so the UI dev sees a mix at the same level.
        for i in range(total):
            stale = i < bust_count
            deadline = stale_deadline if stale else None
            label = "stale" if stale else "fresh"
            resp = await submit_spot_order(
                account_b,
                symbol=params.symbol,
                is_buy=False,
                limit_px=sell_price,
                qty=per_order_qty,
                time_in_force=TimeInForce.GTC,
                deadline_override=deadline,
            )
            logger.info(_fmt_response(f"GTC #{i + 1} ({label})", resp))

        logger.info(
            "Resting orders in place. From the UI, send an aggressive BUY of %s %s to sweep.",
            params.rester_qty,
            params.symbol,
        )
        logger.info(
            "Expect %d clean fills + %d busted fills once the aggressor lands.",
            normal_count,
            bust_count,
        )
    finally:
        await account_b.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--test",
        type=int,
        choices=[1, 2],
        required=True,
        help="Which scenario to run (1 = IOC busts, 2 = resting GTC busts).",
    )
    parser.add_argument("--symbol", default="WETHRUSD", help="Spot symbol (default: WETHRUSD).")
    parser.add_argument(
        "--qty",
        type=Decimal,
        default=Decimal("0.06"),
        help="Total resting qty for the maker side (test 1) or "
        "aggregate qty across the 6 resters (test 2). Default 0.06.",
    )
    parser.add_argument(
        "--take-qty", type=Decimal, default=Decimal("0.01"), help="Qty of each IOC take (test 1 only). Default 0.01."
    )
    parser.add_argument(
        "--num-normal", type=int, default=3, help="Number of normal-deadline IOC takes in test 1. Default 3."
    )
    parser.add_argument(
        "--num-bust", type=int, default=3, help="Number of stale-deadline IOC takes in test 1. Default 3."
    )
    parser.add_argument(
        "--num-resters", type=int, default=6, help="Number of GTC resting orders in test 2. Default 6 (half stale)."
    )
    parser.add_argument(
        "--price",
        type=Decimal,
        default=None,
        help="Override anchor price (otherwise the script reads the book midpoint).",
    )
    return parser.parse_args()


async def main() -> None:
    load_dotenv()
    args = parse_args()

    params = TestParams(
        symbol=args.symbol,
        rester_qty=args.qty,
        take_qty=args.take_qty,
        num_normal_takes=args.num_normal,
        num_bust_takes=args.num_bust,
        num_resting_orders=args.num_resters,
        buy_limit_multiplier=Decimal("0.99"),
        sell_limit_multiplier=Decimal("1.01"),
    )

    # Anchor price: explicit --price wins, otherwise read from the book.
    if args.price is not None:
        anchor = args.price
    else:
        # Use whichever account works for reading market data.
        probe = await make_client(1)
        try:
            anchor = await get_oracle_price(probe, params.symbol)
        finally:
            await probe.close()
    logger.info("Anchor price for %s = %s", params.symbol, anchor)

    if args.test == 1:
        await run_idea_one(params, anchor)
    else:
        await run_idea_two(params, anchor)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
