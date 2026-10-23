"""Enter once when the mark crosses a level, then protect the whole position with a stop-loss.

The API version of a simple hosted rule: "sell $30 of ETH when the mark crosses down
through 2,492, with a stop-loss at 2,560". It:

1. watches the mark on the WebSocket Info API (see mark_feed.py);
2. fires when the previous fresh mark was above the level and the new one is at or
   below it (``--direction down``; ``up`` is the reverse). A crossing during a feed gap
   or a reconnect is reported, never fired late, and a crossing mark already past the
   stop does not enter;
3. sends one IOC limit order at the mark plus or minus ``--bound-bps``, sized from
   ``--notional`` at that limit price and rounded down to the market's quantity step;
4. once the fill shows in the position, arms a STOP_LOSS that protects the whole
   position, with an IOC child 1% beyond the trigger.

Dry run by default: it prints the orders it would send and stops, signing and sending
nothing. Add ``--live`` to send. The client still needs a signer key in ``.env``.
Rate limits: one run uses two create operations (the entry and the stop), well inside
the Standard tier's burst of five.

Configured from the SDK's ``.env``
(``PERP_ACCOUNT_ID_1``, ``PERP_WALLET_ADDRESS_1``, ``PERP_PRIVATE_KEY_1`` = your
authorised signer, plus the environment values). Try it on devnet1 first.

Usage:
    python -m examples.strategies.price_cross_entry --symbol ETHRUSDPERP --direction down --level 2492 \
        --side sell --notional 30 --stop-px 2560 [--bound-bps 25] [--live]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters

from .mark_feed import Mark, MarkFeed, now_ms

logger = logging.getLogger("price_cross_entry")

STOP_LIMIT_SLIPPAGE = Decimal("0.01")  # the stop's IOC child may fill up to 1% beyond the trigger
REARM_BAND_BPS = 50  # if the level is already passed at start, wait until the mark is this far back
POSITION_WAIT_READS = 10
POSITION_WAIT_S = 0.5


def round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def round_up(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def wire(value: Decimal) -> str:
    return format(value.normalize(), "f")


def new_client_order_id() -> int:
    """A non-zero uint64 to find this order again in openOrders and orderChanges."""
    return secrets.randbits(63) | 1


def stop_not_protective(is_buy: bool, stop_px: Decimal, price: Decimal) -> bool:
    """True when a stop at ``stop_px`` would not sit on the losing side of ``price``."""
    return stop_px >= price if is_buy else stop_px <= price


def past_level(price: Decimal, level: Decimal, direction: str) -> bool:
    return price <= level if direction == "down" else price >= level


def back_past_band(price: Decimal, level: Decimal, direction: str) -> bool:
    band = Decimal(REARM_BAND_BPS) / Decimal(10_000)
    return price > level * (1 + band) if direction == "down" else price < level * (1 - band)


async def wait_for_crossing(feed: MarkFeed, level: Decimal, direction: str) -> Mark:
    """Return the first fresh mark that crosses the level from a fresh mark on the other side."""
    previous: Mark | None = None
    waiting_for_band = False
    while True:
        mark = await feed.next_mark(timeout=1.0)
        now = now_ms()
        if mark is None:
            if previous is not None and not previous.is_fresh(now):
                logger.warning("no fresh mark for 10 s: not firing until the feed recovers")
                previous = None
            continue
        if not mark.is_fresh(now):
            logger.warning("stale mark (%d ms old) ignored", mark.age_ms(now))
            previous = None
            continue
        if previous is not None and mark.connection != previous.connection:
            # The feed reconnected: marks were missed, so never compare across the gap.
            logger.info("feed reconnected; starting the comparison again from mark %s", mark.price)
            previous = None
        if previous is None:
            if past_level(mark.price, level, direction):
                # Already past the level at start, or the level was crossed during a gap.
                logger.info("mark %s is already past %s; waiting for it to move back first", mark.price, level)
                waiting_for_band = True
            previous = mark
            continue
        if waiting_for_band:
            if back_past_band(mark.price, level, direction):
                logger.info("mark %s is back past the re-arm band; armed", mark.price)
                waiting_for_band = False
            previous = mark
            continue
        if not past_level(previous.price, level, direction) and past_level(mark.price, level, direction):
            return mark
        previous = mark


async def read_position(client: ReyaTradingClient, symbol: str):
    for position in await client.get_positions():
        if position.symbol == symbol and position.account_id == client.config.account_id:
            return position
    return None


def error_code(exc: ApiException) -> str | None:
    try:
        code = json.loads(exc.body or "{}").get("error")
    except (AttributeError, TypeError, ValueError):
        return None
    return code if isinstance(code, str) else None


async def send_entry(client: ReyaTradingClient, params: LimitOrderParameters):
    """Send the entry once. Never resend: an unclear outcome must be reconciled first."""
    try:
        return await client.create_limit_order(params)
    except ApiException as exc:
        code = error_code(exc)
        if code == "ORDER_OUTCOME_UNKNOWN_ERROR" or exc.status is None or exc.status >= 500:
            logger.error(
                "entry outcome unknown (%s): check openOrders, orderChanges and executions for "
                "clientOrderId %s before sending anything else",
                code,
                params.client_order_id,
            )
        else:
            logger.error("entry rejected: %s %s", code, exc.body)
        return None
    except Exception:  # pylint: disable=broad-exception-caught
        # A timeout or dropped connection: the order may still have been accepted.
        logger.exception("entry outcome unknown: reconcile clientOrderId %s before retrying", params.client_order_id)
        return None


async def arm_stop_after_fill(
    client: ReyaTradingClient, symbol: str, expected_side: Side, before_seq: int, stop: TriggerOrderParameters
) -> int:
    """Arm the stop once the fill shows in the position. Any failure says POSITION UNPROTECTED."""
    for _ in range(POSITION_WAIT_READS):
        try:
            position = await read_position(client, symbol)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("position read failed; reading again", exc_info=True)
            position = None
        if position and position.side == expected_side and position.last_trade_sequence_number > before_seq:
            break
        await asyncio.sleep(POSITION_WAIT_S)
    else:
        logger.error("the fill is not in the position yet: POSITION UNPROTECTED, arm the stop by hand")
        return 1

    try:
        armed = await client.create_trigger_order(stop)
    except ApiException as exc:
        logger.error("stop not armed (%s): POSITION UNPROTECTED. %s", error_code(exc), exc.body)
        return 1
    except Exception:  # pylint: disable=broad-exception-caught
        # A timeout or dropped connection: the stop may or may not be on the book. Never resend blindly.
        logger.exception(
            "stop outcome unknown: POSITION MAY BE UNPROTECTED; check openOrders for clientOrderId %s",
            stop.client_order_id,
        )
        return 1
    logger.info("stop %s %s: trigger %s, limit %s", armed.order_id, armed.status, stop.trigger_px, stop.limit_px)
    return 0


async def main(args: argparse.Namespace) -> int:
    level = Decimal(args.level)
    stop_px = Decimal(args.stop_px)
    notional = Decimal(args.notional)
    is_buy = args.side == "buy"
    bound = Decimal(args.bound_bps) / Decimal(10_000)
    if not 10 <= args.bound_bps <= 100:
        logger.error("--bound-bps must be 10-100")
        return 2
    if stop_not_protective(is_buy, stop_px, level):
        logger.error("the stop must be below the level for a buy, above it for a sell")
        return 2

    async with ReyaTradingClient() as client:
        await client.start()
        definitions = {m.symbol: m for m in await client.reference.get_perp_market_definitions()}
        market = definitions.get(args.symbol)
        if market is None:
            logger.error("unknown perp market %s", args.symbol)
            return 2
        tick, step, min_qty = Decimal(market.tick_size), Decimal(market.qty_step_size), Decimal(market.min_order_qty)

        feed = MarkFeed(args.symbol)
        feed_task = asyncio.create_task(feed.run())
        try:
            logger.info("watching %s for a cross %s through %s", args.symbol, args.direction, level)
            mark = await wait_for_crossing(feed, level, args.direction)
        finally:
            feed.stop()
            feed_task.cancel()

        # The crossing mark can overshoot the level, past the stop: the stop would fire at once.
        if stop_not_protective(is_buy, stop_px, mark.price):
            logger.error("mark %s is already beyond the stop %s; not entering", mark.price, stop_px)
            return 1

        # Price the IOC from the mark at firing, not from the level, and round inward to the tick.
        limit = round_down(mark.price * (1 + bound), tick) if is_buy else round_up(mark.price * (1 - bound), tick)
        qty = round_down(notional / limit, step)
        logger.info("crossed: mark %s; %s up to %s at %s or better", mark.price, args.side, wire(qty), wire(limit))
        if qty < min_qty:
            logger.error("$%s is below the minimum order of %s (~$%s)", notional, wire(min_qty), wire(min_qty * limit))
            return 1
        if qty * limit < notional:
            logger.info(
                "$%s becomes %s (~$%s) in %s steps; %s would be ~$%s",
                notional,
                wire(qty),
                wire(qty * limit),
                wire(step),
                wire(qty + step),
                wire((qty + step) * limit),
            )

        entry = LimitOrderParameters(
            symbol=args.symbol,
            is_buy=is_buy,
            limit_px=wire(limit),
            qty=wire(qty),
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
            client_order_id=new_client_order_id(),
        )
        close_is_buy = not is_buy
        trigger = round_up(stop_px, tick) if close_is_buy else round_down(stop_px, tick)
        stop_limit = (
            round_down(trigger * (1 + STOP_LIMIT_SLIPPAGE), tick)
            if close_is_buy
            else round_up(trigger * (1 - STOP_LIMIT_SLIPPAGE), tick)
        )
        stop = TriggerOrderParameters(
            symbol=args.symbol,
            is_buy=close_is_buy,
            trigger_px=wire(trigger),
            trigger_type=OrderType.STOP_LOSS,
            limit_px=wire(stop_limit),
            time_in_force=TimeInForce.IOC,
            client_order_id=new_client_order_id(),
        )
        if not args.live:
            logger.info("DRY RUN, nothing sent. Entry: %s", entry)
            logger.info("DRY RUN, nothing sent. Stop after the fill: %s", stop)
            return 0

        before = await read_position(client, args.symbol)
        before_seq = before.last_trade_sequence_number if before else -1
        response = await send_entry(client, entry)
        if response is None:
            return 1
        filled = Decimal(response.exec_qty or "0")
        logger.info("entry %s: %s, filled %s at %s", response.order_id, response.status, filled, response.avg_fill_px)
        if filled == 0:
            logger.info("nothing filled within the bound; no position, no stop")
            return 0

        # Arm the stop only once the fill shows in the position.
        return await arm_stop_after_fill(client, args.symbol, Side.B if is_buy else Side.A, before_seq, stop)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", required=True, help="e.g. ETHRUSDPERP")
    parser.add_argument("--direction", choices=["down", "up"], required=True)
    parser.add_argument("--level", required=True)
    parser.add_argument("--side", choices=["buy", "sell"], default="buy")
    parser.add_argument("--notional", required=True, help="USD for the entry")
    parser.add_argument("--stop-px", required=True, help="whole-position stop-loss trigger")
    parser.add_argument("--bound-bps", type=int, default=25, help="how far from the mark the entry may fill (10-100)")
    parser.add_argument("--live", action="store_true", help="send the orders (default: dry run)")
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    sys.exit(asyncio.run(main(parse_args(sys.argv[1:]))))
