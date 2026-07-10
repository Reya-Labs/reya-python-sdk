from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

PriceInput = Decimal | int | float | str


@dataclass(frozen=True)
class SelfMatchPrices:
    """Resting prices for a same-account bid that can modify up into its ask."""

    bid_px: str
    ask_px: str


def _decimal(value: PriceInput) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def price_tick(tick_size: PriceInput) -> Decimal:
    tick = _decimal(tick_size)
    if tick <= 0:
        raise ValueError(f"tick_size must be positive, got {tick_size!r}")
    return tick


def format_price(price: Decimal) -> str:
    return format(price, "f")


def quantize_price(value: PriceInput, tick_size: PriceInput) -> Decimal:
    tick = price_tick(tick_size)
    units = (_decimal(value) / tick).to_integral_value(rounding=ROUND_HALF_UP)
    return (units * tick).quantize(tick)


def limit_price(oracle_price: PriceInput, tick_size: PriceInput, multiplier: PriceInput = 1) -> Decimal:
    return quantize_price(_decimal(oracle_price) * _decimal(multiplier), tick_size)


def floor_price(oracle_price: PriceInput, tick_size: PriceInput, multiplier: PriceInput = 1) -> Decimal:
    tick = price_tick(tick_size)
    raw_price = _decimal(oracle_price) * _decimal(multiplier)
    return ((raw_price // tick) * tick).quantize(tick)


def one_tick_above(price: PriceInput, tick_size: PriceInput) -> Decimal:
    tick = price_tick(tick_size)
    return quantize_price(_decimal(price) + tick, tick)


def one_tick_below(price: PriceInput, tick_size: PriceInput) -> Decimal | None:
    tick = price_tick(tick_size)
    candidate = quantize_price(_decimal(price) - tick, tick)
    if candidate <= 0:
        return None
    return candidate


def one_tick_inside_spread(
    *,
    best_bid: PriceInput | None,
    best_ask: PriceInput | None,
    tick_size: PriceInput,
) -> Decimal | None:
    if best_bid is not None:
        candidate = one_tick_above(best_bid, tick_size)
        if best_ask is not None and candidate >= _decimal(best_ask):
            return None
        return candidate

    if best_ask is None:
        return None

    return one_tick_below(best_ask, tick_size)


def self_match_resting_prices(
    *,
    best_bid: PriceInput | None,
    best_ask: PriceInput | None,
    tick_size: PriceInput,
    fallback_bid: PriceInput = "1",
    fallback_ask: PriceInput = "2",
) -> SelfMatchPrices | None:
    if best_bid is None and best_ask is None:
        fallback_bid_price = quantize_price(fallback_bid, tick_size)
        fallback_ask_price = quantize_price(fallback_ask, tick_size)
        if fallback_bid_price <= 0 or fallback_bid_price >= fallback_ask_price:
            return None
        return SelfMatchPrices(
            bid_px=format_price(fallback_bid_price),
            ask_px=format_price(fallback_ask_price),
        )

    resting_ask = one_tick_inside_spread(best_bid=best_bid, best_ask=best_ask, tick_size=tick_size)
    if resting_ask is None:
        return None

    resting_bid = one_tick_below(resting_ask, tick_size)
    if resting_bid is None:
        return None

    return SelfMatchPrices(bid_px=format_price(resting_bid), ask_px=format_price(resting_ask))
