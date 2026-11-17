"""Leverage to custom-risk conversion for POST /v2/updateLeverage.

A leverage update does not sign the leverage. It signs `customRisk`, the SD1x18
risk value Core stores for the (account, market) pair. The client and the server
derive it from the leverage with the same integer-only rule, and the server
verifies the signature over its own value, so a client that rounds differently
gets UNAUTHORIZED_SIGNATURE_ERROR. Every step here is integer arithmetic on the
exact multiplier; no float is involved.

Mirrors `packages/common/src/utils/custom-leverage.ts` in reya-off-chain-monorepo
(`customRiskFromLeverage`, `customRiskForLeverageSetting`, `parseImMultiplier`).
"""

from typing import Any

import re
from decimal import Decimal

_E18 = 10**18
_E54 = _E18**3

# The largest custom risk value Core accepts: 1 (SD1x18, 1e18). Zero is the
# reset, so a leverage setting signs a value in (0, MAX_CUSTOM_RISK_E18].
MAX_CUSTOM_RISK_E18 = _E18

# The value signed at the market's `maxLeverage`: it clears the account's
# setting, which returns the market to its default initial margin.
RESET_CUSTOM_RISK = 0

# `MarketDefinition.imMultiplier` is published as a plain decimal with at most 18
# fractional digits (the multiplier is stored on chain as a raw 1e18 integer).
# Exponent notation is refused rather than interpreted, as the API's own parser does.
_IM_MULTIPLIER_PATTERN = re.compile(r"\d+(\.\d{1,18})?")


def _require_whole_number(name: str, value: Any) -> None:
    # `bool` is an `int` subclass: `True` would otherwise sign leverage 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be a whole number (got {value!r})")


def parse_im_multiplier(im_multiplier: str) -> int:
    """Return the raw 1e18 integer of a published ``imMultiplier``, exactly.

    ``"1.3"`` is ``1300000000000000000`` and ``"2"`` is ``2 * 10**18``. The
    digits are read from the ``Decimal`` tuple, so no context precision or
    rounding is involved however many digits the value has.
    """
    if not isinstance(im_multiplier, str) or not _IM_MULTIPLIER_PATTERN.fullmatch(im_multiplier):
        raise ValueError(f"imMultiplier must be a positive decimal with at most 18 decimals (got {im_multiplier!r})")
    _sign, digits, exponent = Decimal(im_multiplier).as_tuple()
    # The pattern admits only finite values with exponent in [-18, 0].
    assert isinstance(exponent, int)
    value: int = int("".join(map(str, digits))) * 10 ** (18 + exponent)
    if value <= 0:
        raise ValueError(f"imMultiplier must be positive (got {im_multiplier!r})")
    return value


def custom_risk_from_leverage(leverage: int, im_multiplier_e18: int) -> int:
    """Return the custom risk value (SD1x18, raw 1e18 units) that caps a market
    at the whole-number ``leverage``.

    Core keeps the value ``d`` in the units of the market's risk-matrix cell, so
    the initial margin rate it implies is ``imMultiplier * sqrt(d)`` and the
    leverage is ``1 / (imMultiplier * sqrt(d))``. Solving for ``d`` with
    ``m = im_multiplier_e18 / 1e18``::

        d = (1 / (m * L))**2 * 1e18 = 10**54 / (im_multiplier_e18**2 * L**2)

    rounded up, so the stored value never allows more leverage than requested
    (a larger ``d`` is a larger margin).

    Raises ``ValueError`` for a leverage that is not a positive whole number, a
    non-positive multiplier, or a pair whose value would exceed 1
    (``m * L < 1``), which Core rejects with ``InvalidCustomRisk``.
    """
    _require_whole_number("leverage", leverage)
    if leverage <= 0:
        raise ValueError(f"leverage must be a positive whole number (got {leverage})")
    _require_whole_number("im_multiplier_e18", im_multiplier_e18)
    if im_multiplier_e18 <= 0:
        raise ValueError(f"im_multiplier_e18 must be a positive raw 1e18 integer (got {im_multiplier_e18})")

    denominator = im_multiplier_e18 * im_multiplier_e18 * leverage * leverage
    custom_risk = -(-_E54 // denominator)  # ceiling division, integers only
    if not 0 < custom_risk <= MAX_CUSTOM_RISK_E18:
        raise ValueError(
            f"leverage {leverage} with imMultiplier {im_multiplier_e18} (1e18) needs a custom risk value of "
            f"{custom_risk}, outside (0, {MAX_CUSTOM_RISK_E18}]"
        )
    return custom_risk


def custom_risk_for_leverage_setting(leverage: int, im_multiplier_e18: int, max_leverage: int) -> int:
    """Return the ``customRisk`` a POST /v2/updateLeverage request signs.

    Below the market's ``max_leverage`` it is :func:`custom_risk_from_leverage`;
    at ``max_leverage`` it is ``0``, the reset to the market's default. The
    server derives the value from this same rule.

    Raises ``ValueError`` for a leverage that is not a whole number in
    ``[1, max_leverage]``, or a maximum that is not a positive whole number.
    """
    _require_whole_number("max_leverage", max_leverage)
    if max_leverage <= 0:
        raise ValueError(f"max_leverage must be a positive whole number (got {max_leverage})")
    _require_whole_number("leverage", leverage)
    if not 1 <= leverage <= max_leverage:
        raise ValueError(f"leverage must be a whole number from 1 to {max_leverage} (got {leverage})")
    if leverage == max_leverage:
        return RESET_CUSTOM_RISK
    return custom_risk_from_leverage(leverage, im_multiplier_e18)
