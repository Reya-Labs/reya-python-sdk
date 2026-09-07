#!/usr/bin/env python3
"""
Seed a devnet order book — spot WETHRUSD or perp ETHRUSDPERP.

The devnet book often comes up empty after a fresh deploy, which makes the
UI look broken. This script places a small ladder of resting GTC orders so
there is something to render and trade against.

Layout
------

* account 1 (SPOT_*_1) places N **bids** below the anchor price.
* account 2 (SPOT_*_2) places N **asks** above the anchor price.

The two accounts never share a side, so their resters cannot self-cross. The
ladder is centered on ``--price`` (required — devnet's oracle is not always
trustworthy, and an empty book has no mid to read).

For ``--market perp`` the script auto-discovers each wallet's MAINPERP
account on devnet (``GET /v2/wallet/{addr}/accounts``) and overrides the
SPOT_ACCOUNT_ID_* value from .env with it. The same wallets / private keys
are reused — devnet derives both spot and perp accounts from the same EOA.

Endpoints
---------

Hard-pinned to the devnet base ``https://api-devnet.reya-cronos.network/v2``
regardless of REYA_API_URL in .env (the same .env is shared with
cronos-testnet). The wire symbols stay RUSD-quoted (WETHRUSD / ETHRUSDPERP);
the UI may show USDC.

This script targets the v2.3.0 unified spot+perp SDK (PR #51 / feat/perpOB)
and uses the public ``create_limit_order`` / ``mass_cancel`` calls directly.

Usage
-----

    poetry shell
    python -m scripts.seed_devnet_book --price 2000                  # spot WETHRUSD
    python -m scripts.seed_devnet_book --market perp --price 2000    # perp ETHRUSDPERP

Required env (same .env as cronos-testnet works on devnet1):

    SPOT_WALLET_ADDRESS_1, SPOT_PRIVATE_KEY_1, SPOT_ACCOUNT_ID_1
    SPOT_WALLET_ADDRESS_2, SPOT_PRIVATE_KEY_2, SPOT_ACCOUNT_ID_2
"""

from __future__ import annotations

from typing import Union

import argparse
import asyncio
import logging
import sys
from decimal import ROUND_DOWN, ROUND_UP, Decimal

from dotenv import load_dotenv

from sdk.open_api.models import TimeInForce
from sdk.open_api.models.account_type import AccountType
from sdk.open_api.models.market_definition import MarketDefinition
from sdk.open_api.models.spot_market_definition import SpotMarketDefinition
from sdk.reya_rest_api import ReyaTradingClient, get_spot_config
from sdk.reya_rest_api.models.orders import LimitOrderParameters

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
logger = logging.getLogger("seed_devnet_book")

DEVNET_API_URL = "https://api-devnet.reya-cronos.network/v2"

DEFAULT_SYMBOL = {"spot": "WETHRUSD", "perp": "ETHRUSDPERP"}
ACCOUNT_TYPE_FOR_MARKET = {"spot": AccountType.SPOT, "perp": AccountType.MAINPERP}

MarketMeta = Union[SpotMarketDefinition, MarketDefinition]


async def make_client(account_number: int, market: str) -> ReyaTradingClient:
    config = get_spot_config(account_number=account_number)
    if config.private_key is None:
        raise RuntimeError(f"SPOT_PRIVATE_KEY_{account_number} is required")
    if config.account_id is None:
        # The from_env_spot wrapper still requires this, even when we're about
        # to override it with the MAINPERP id discovered from devnet.
        raise RuntimeError(f"SPOT_ACCOUNT_ID_{account_number} is required (used as a stand-in to bootstrap the client)")
    # Force devnet regardless of REYA_API_URL in .env.
    config.api_url = DEVNET_API_URL
    client = ReyaTradingClient(config)
    await client.start()

    # For perp, swap the spot id for the wallet's MAINPERP account. Devnet
    # exposes both on /v2/wallet/{addr}/accounts; .env only carries the spot
    # one because that's all this script's older spot-only mode needed.
    if market == "perp":
        target_type = ACCOUNT_TYPE_FOR_MARKET[market]
        accts = await client.wallet.get_wallet_accounts(address=config.owner_wallet_address)
        match = next((a for a in accts if a.type == target_type), None)
        if match is None:
            raise RuntimeError(
                f"Wallet {config.owner_wallet_address} has no {target_type.value} account on devnet "
                f"(found: {[(a.account_id, a.type.value) for a in accts]})"
            )
        config.account_id = match.account_id
        logger.info(
            "account %d: %s -> using %s account_id=%s",
            account_number,
            config.owner_wallet_address,
            target_type.value,
            match.account_id,
        )
    return client


async def get_market_meta(client: ReyaTradingClient, symbol: str, market: str) -> MarketMeta:
    """Return the market definition for ``symbol`` (tick + qty step sizes)."""
    defs: list[MarketMeta]
    if market == "spot":
        defs = list(await client.reference.get_spot_market_definitions())
    else:
        defs = list(await client.reference.get_perp_market_definitions())
    for definition in defs:
        if definition.symbol == symbol:
            return definition
    available = ", ".join(sorted(d.symbol for d in defs))
    raise RuntimeError(f"{market.capitalize()} symbol {symbol!r} not found. Available: {available}")


def snap(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    """Round ``value`` to a multiple of ``step`` using the given rounding mode."""
    if step <= 0:
        raise ValueError(f"step must be > 0, got {step}")
    return (value / step).quantize(Decimal("1"), rounding=rounding) * step


def build_ladder_prices(
    anchor: Decimal,
    *,
    tick: Decimal,
    levels: int,
    spread_bps: Decimal,
    step_bps: Decimal,
    is_buy: bool,
) -> list[Decimal]:
    """Build a price ladder snapped to ``tick``.

    ``spread_bps`` is the half-spread from the anchor to the best bid/ask.
    ``step_bps`` is the gap between adjacent levels. Bids step further below
    the anchor as i grows; asks step further above.
    """
    bps = Decimal("10000")
    direction = Decimal(-1) if is_buy else Decimal(1)
    rounding = ROUND_DOWN if is_buy else ROUND_UP
    out: list[Decimal] = []
    for i in range(levels):
        offset_bps = spread_bps + step_bps * i
        px = anchor * (Decimal(1) + direction * offset_bps / bps)
        out.append(snap(px, tick, rounding))
    return out


async def place_ladder(
    client: ReyaTradingClient,
    *,
    symbol: str,
    is_buy: bool,
    levels: list[Decimal],
    qty: Decimal,
    side_label: str,
) -> None:
    """Place ``levels`` GTC orders on one side, logging each."""
    for i, px in enumerate(levels):
        params = LimitOrderParameters(
            symbol=symbol,
            is_buy=is_buy,
            limit_px=str(px),
            qty=str(qty),
            time_in_force=TimeInForce.GTC,
        )
        resp = await client.create_limit_order(params)
        logger.info("%s #%d: px=%s qty=%s -> orderId=%s", side_label, i + 1, px, qty, resp.order_id)


async def seed(
    *,
    market: str,
    symbol: str,
    anchor: Decimal,
    levels: int,
    qty_per_level: Decimal,
    spread_bps: Decimal,
    step_bps: Decimal,
) -> None:
    bids_client = await make_client(1, market)
    asks_client = await make_client(2, market)
    try:
        meta = await get_market_meta(bids_client, symbol, market)
        tick = Decimal(meta.tick_size)
        qty_step = Decimal(meta.qty_step_size)
        min_qty = Decimal(meta.min_order_qty)

        qty = snap(qty_per_level, qty_step, ROUND_DOWN)
        if qty < min_qty:
            raise RuntimeError(
                f"qty-per-level {qty_per_level} rounds to {qty}, below minOrderQty {min_qty} for {symbol}"
            )

        bids = build_ladder_prices(
            anchor, tick=tick, levels=levels, spread_bps=spread_bps, step_bps=step_bps, is_buy=True
        )
        asks = build_ladder_prices(
            anchor, tick=tick, levels=levels, spread_bps=spread_bps, step_bps=step_bps, is_buy=False
        )

        logger.info("=" * 72)
        logger.info(
            "Seeding %s (%s) on devnet: anchor=%s, tick=%s, qtyStep=%s, levels=%d, qty/lvl=%s",
            symbol,
            market,
            anchor,
            tick,
            qty_step,
            levels,
            qty,
        )
        logger.info("Bids (account 1): %s", [str(p) for p in bids])
        logger.info("Asks (account 2): %s", [str(p) for p in asks])
        logger.info("=" * 72)

        await bids_client.mass_cancel(symbol=symbol)
        logger.info("Cleared resting orders for account 1 on %s", symbol)
        await asks_client.mass_cancel(symbol=symbol)
        logger.info("Cleared resting orders for account 2 on %s", symbol)

        await place_ladder(bids_client, symbol=symbol, is_buy=True, levels=bids, qty=qty, side_label="BID")
        await place_ladder(asks_client, symbol=symbol, is_buy=False, levels=asks, qty=qty, side_label="ASK")

        logger.info("Done. Best bid=%s, best ask=%s, spread=%s", bids[0], asks[0], asks[0] - bids[0])
    finally:
        await bids_client.close()
        await asks_client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--market",
        choices=("spot", "perp"),
        default="spot",
        help="Which order book to seed. Default: spot.",
    )
    parser.add_argument(
        "--symbol",
        default=None,
        help="Symbol override. Default: WETHRUSD for spot, ETHRUSDPERP for perp.",
    )
    parser.add_argument(
        "--price",
        type=Decimal,
        required=True,
        help="Anchor price around which the ladder is centered (devnet's oracle is unreliable, so explicit).",
    )
    parser.add_argument("--levels", type=int, default=5, help="Number of price levels per side. Default 5.")
    parser.add_argument(
        "--qty-per-level",
        type=Decimal,
        default=Decimal("0.01"),
        help="Qty placed at each level (will be snapped down to qtyStepSize). Default 0.01.",
    )
    parser.add_argument(
        "--spread-bps",
        type=Decimal,
        default=Decimal("10"),
        help="Half-spread in bps from anchor to best bid/ask (10 bps = 0.1%%). Default 10.",
    )
    parser.add_argument(
        "--step-bps",
        type=Decimal,
        default=Decimal("5"),
        help="Gap in bps between adjacent levels (5 bps = 0.05%%). Default 5.",
    )
    return parser.parse_args()


async def main() -> None:
    load_dotenv()
    args = parse_args()
    symbol = args.symbol if args.symbol is not None else DEFAULT_SYMBOL[args.market]
    await seed(
        market=args.market,
        symbol=symbol,
        anchor=args.price,
        levels=args.levels,
        qty_per_level=args.qty_per_level,
        spread_bps=args.spread_bps,
        step_bps=args.step_bps,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
