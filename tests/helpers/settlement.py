"""Market-agnostic settlement probes for [spot, perp]-parametrized fill tests.

A crossing/filling test asserts the same engine behavior on both markets
(FILLED, execQty, both orderIds, no busts) — only the SETTLEMENT proof
diverges: a spot fill moves asset balances, a perp fill moves a signed
position. These probes encapsulate exactly that divergence behind one
interface (`capture_baseline` / `assert_settled`), so a fill test can be
written ONCE and parametrized, with the right probe injected by market type.

Each probe preserves the FULL strength of the hand-written twin it replaces:
- SpotSettlementProbe → `verify_spot_trade_balance_changes` (exact zero-fee
  base + quote deltas on BOTH accounts), after polling the buyer's base
  balance to its expected value to absorb indexer lag.
- PerpSettlementProbe → two `check.position_delta` assertions (both accounts'
  signed positions moved by exactly ±qty from their captured baselines).

Convention: `buyer` is the aggressor (the account whose order crosses /
takes), `seller` is the resting counterparty.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal

from sdk.reya_rest_api.config import REYA_DEX_ID
from tests.helpers.market_config import MarketTestConfig

if TYPE_CHECKING:
    from tests.helpers import ReyaTester

QUOTE_ASSET = "RUSD"
SETTLEMENT_TIMEOUT_S = 10.0


class SettlementProbe(Protocol):
    """Captures pre-fill settlement state and asserts the post-fill delta."""

    async def capture_baseline(self) -> None: ...  # noqa: E704

    async def assert_settled(  # noqa: E704
        self, qty: str, price: str, timeout_s: float = SETTLEMENT_TIMEOUT_S
    ) -> None: ...


@dataclass
class SpotSettlementProbe:
    """Asserts a spot fill moved both accounts' asset balances by the EXACT
    zero-fee amounts (base ±qty, quote ∓qty·price)."""

    market_config: MarketTestConfig
    buyer: ReyaTester
    seller: ReyaTester
    _buyer_initial: dict | None = field(default=None, repr=False)
    _seller_initial: dict | None = field(default=None, repr=False)

    async def capture_baseline(self) -> None:
        self._buyer_initial = await self.buyer.data.balances()
        self._seller_initial = await self.seller.data.balances()

    async def assert_settled(self, qty: str, price: str, timeout_s: float = SETTLEMENT_TIMEOUT_S) -> None:
        assert self._buyer_initial is not None and self._seller_initial is not None, "capture_baseline() not called"
        base_asset = self.market_config.base_asset

        # Poll the buyer's base balance to its expected post-fill value first
        # (the position/balance view trails on-chain settlement by the indexer
        # write lag), then assert the EXACT deltas on both accounts.
        buyer_initial_base = self._buyer_initial.get(base_asset)
        assert buyer_initial_base is not None, f"Buyer has no {base_asset} balance to baseline against"
        expected_buyer_base = Decimal(buyer_initial_base.real_balance) + Decimal(qty)
        deadline = time.time() + timeout_s
        buyer_final = await self.buyer.data.balances()
        while time.time() < deadline:
            buyer_final_base = buyer_final.get(base_asset)
            if buyer_final_base is not None and Decimal(buyer_final_base.real_balance) == expected_buyer_base:
                break
            await asyncio.sleep(0.2)
            buyer_final = await self.buyer.data.balances()
        seller_final = await self.seller.data.balances()

        # The aggressor (buyer) is the trade-TAKER; the resting counterparty
        # (seller) is the trade-MAKER and is NOT the buyer → is_maker_buyer=False.
        self.buyer.ws.verify_spot_trade_balance_changes(
            maker_initial_balances=self._seller_initial,
            maker_final_balances=seller_final,
            taker_initial_balances=self._buyer_initial,
            taker_final_balances=buyer_final,
            base_asset=base_asset,
            quote_asset=QUOTE_ASSET,
            qty=qty,
            price=price,
            is_maker_buyer=False,
        )


@dataclass
class PerpSettlementProbe:
    """Asserts a perp fill moved both accounts' signed positions by exactly
    ±qty from their captured baselines (`price` unused — perp settles to
    positions, not balances)."""

    market_config: MarketTestConfig
    buyer: ReyaTester
    seller: ReyaTester
    _buyer_baseline: Decimal | None = field(default=None, repr=False)
    _seller_baseline: Decimal | None = field(default=None, repr=False)

    async def capture_baseline(self) -> None:
        symbol = self.market_config.symbol
        self._buyer_baseline = await self.buyer.positions.signed_qty(symbol)
        self._seller_baseline = await self.seller.positions.signed_qty(symbol)

    async def assert_settled(  # pylint: disable=unused-argument
        self, qty: str, price: str, timeout_s: float = SETTLEMENT_TIMEOUT_S
    ) -> None:
        # `price` is part of the uniform SettlementProbe interface but unused
        # here — perp settles to a signed position, not a notional balance.
        assert self._buyer_baseline is not None and self._seller_baseline is not None, "capture_baseline() not called"
        symbol = self.market_config.symbol
        await self.buyer.check.position_delta(
            symbol=symbol,
            baseline=self._buyer_baseline,
            expected_delta=Decimal(qty),
            expected_account_id=self.buyer.account_id,
            expected_exchange_id=REYA_DEX_ID,
            timeout=timeout_s,
        )
        await self.seller.check.position_delta(
            symbol=symbol,
            baseline=self._seller_baseline,
            expected_delta=-Decimal(qty),
            expected_account_id=self.seller.account_id,
            expected_exchange_id=REYA_DEX_ID,
            timeout=timeout_s,
        )


def make_settlement_probe(
    market_type: str, market_config: MarketTestConfig, buyer: ReyaTester, seller: ReyaTester
) -> SettlementProbe:
    """Build the settlement probe matching the active market parametrization."""
    if market_type == "spot":
        return SpotSettlementProbe(market_config=market_config, buyer=buyer, seller=seller)
    return PerpSettlementProbe(market_config=market_config, buyer=buyer, seller=seller)
