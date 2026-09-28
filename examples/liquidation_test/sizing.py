"""Single-market, rUSD-only sizing at the signed worst execution price."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext


def decimal(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("All numeric inputs must be finite")
    return result


def positive(value: object) -> Decimal:
    result = decimal(value)
    if result <= 0:
        raise ValueError("Expected a strictly positive value")
    return result


def floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / positive(step)).to_integral_value(rounding=ROUND_FLOOR) * step


def quote(
    *,
    balance: Decimal,
    mark: Decimal,
    lmr_rate: Decimal,
    fee_rate: Decimal,
    buffer: Decimal,
    slippage_bps: Decimal,
    tick: Decimal,
    step: Decimal,
    minimum: Decimal,
    levels: list[dict],
    is_buy: bool,
    max_notional: Decimal,
) -> dict:
    """Maximize lot-aligned quantity subject to a positive estimated LM buffer.

    No leverage recommendation or below-LMR target is implicit in this function:
    the operator supplies the collateral, buffer, slippage and notional bounds.
    """
    for value in (balance, mark, lmr_rate, buffer, tick, step, minimum, max_notional):
        positive(value)
    if not Decimal(0) <= slippage_bps < Decimal(10000) or not Decimal(0) <= fee_rate < Decimal(1):
        raise ValueError("Invalid slippage or fee rate")
    if balance <= buffer:
        raise ValueError("Collateral does not cover the requested positive margin buffer")
    if not levels:
        raise ValueError("No opposite-side liquidity")
    parsed = sorted(
        [(positive(level["px"]), positive(level["qty"])) for level in levels],
        reverse=not is_buy,
    )
    with localcontext() as context:
        context.prec = 60
        direction = Decimal(1 if is_buy else -1)
        raw_limit = parsed[0][0] * (1 + direction * slippage_bps / 10000)
        rounding = ROUND_CEILING if is_buy else ROUND_FLOOR
        limit = (raw_limit / tick).to_integral_value(rounding=rounding) * tick
        positive(limit)
        # MB_after - LMR_after = B - q * cost, for an initially flat account.
        cost = mark * lmr_rate + limit * fee_rate + direction * (limit - mark)
        if cost <= 0:
            raise ValueError("Execution/mark discrepancy gives a non-positive margin cost; inspect prices")
        qty = floor_step((balance - buffer) / cost, step)
        if qty < minimum:
            raise ValueError("Collateral cannot support the market minimum with the requested buffer")
        if qty * mark > max_notional:
            raise ValueError("Near-LMR size exceeds the operator's maximum notional")
        available = sum((size for price, size in parsed if (price <= limit if is_buy else price >= limit)), Decimal(0))
        if available < qty:
            raise ValueError("Insufficient visible liquidity for the complete near-LMR position")
        return {
            "side": "LONG" if is_buy else "SHORT",
            "qty": str(qty),
            "limit_px": str(limit),
            "mark": str(mark),
            "lmr_rate": str(lmr_rate),
            "fee_rate_bound": str(fee_rate),
            "estimated_margin_balance_at_limit": str(balance - qty * (cost - mark * lmr_rate)),
            "estimated_lmr": str(qty * mark * lmr_rate),
            "estimated_liquidation_delta_at_limit": str(balance - qty * cost),
            "notional_at_mark": str(qty * mark),
        }
