"""Client-side L2 book maintained from a snapshot + absolute per-level diffs.

Depth continuity contract: subscribing yields a full snapshot, then every
update is an *absolute* per-level statement — a level's ``qty`` replaces the
prior value, and ``qty == "0"`` removes the level. Rebuilding the book this way
and comparing it to ``GET /v2/market/{symbol}/depth`` (no param = full book) is
the convergence check T2/T3 rely on.
"""

from __future__ import annotations

from decimal import Decimal

from sdk.async_api.depth import Depth as AsyncDepth
from sdk.open_api.models.depth_snapshot import DepthSnapshot as RestDepthSnapshot

# A price/qty level normalised to Decimals for exact set comparison.
Levels = dict[Decimal, Decimal]


class LocalBook:
    """Bids/asks maintained by applying absolute per-level depth diffs."""

    def __init__(self) -> None:
        self.bids: Levels = {}
        self.asks: Levels = {}

    @classmethod
    def from_snapshot(cls, depth: AsyncDepth) -> LocalBook:
        book = cls()
        book._replace_side(book.bids, depth.bids)
        book._replace_side(book.asks, depth.asks)
        return book

    def apply(self, depth: AsyncDepth) -> None:
        """Apply one depth update frame with absolute per-level semantics."""
        self._merge_side(self.bids, depth.bids)
        self._merge_side(self.asks, depth.asks)

    @staticmethod
    def _replace_side(side: Levels, levels) -> None:
        side.clear()
        for level in levels:
            qty = Decimal(level.qty)
            if qty > 0:
                side[Decimal(level.px)] = qty

    @staticmethod
    def _merge_side(side: Levels, levels) -> None:
        for level in levels:
            px = Decimal(level.px)
            qty = Decimal(level.qty)
            if qty > 0:
                side[px] = qty
            else:
                side.pop(px, None)

    def bids_sorted(self) -> list[tuple[Decimal, Decimal]]:
        return sorted(self.bids.items(), key=lambda kv: kv[0], reverse=True)

    def asks_sorted(self) -> list[tuple[Decimal, Decimal]]:
        return sorted(self.asks.items(), key=lambda kv: kv[0])


def rest_levels(levels) -> list[tuple[Decimal, Decimal]]:
    """Normalise a REST/async Depth side to sorted (px, qty) Decimals, dropping zeros."""
    out = [(Decimal(level.px), Decimal(level.qty)) for level in levels if Decimal(level.qty) > 0]
    return out


def depth_sides(depth: RestDepthSnapshot | AsyncDepth) -> tuple[list, list]:
    """Return (bids, asks) as sorted Decimal tuples: bids descending, asks ascending."""
    bids = sorted(rest_levels(depth.bids), key=lambda kv: kv[0], reverse=True)
    asks = sorted(rest_levels(depth.asks), key=lambda kv: kv[0])
    return bids, asks
