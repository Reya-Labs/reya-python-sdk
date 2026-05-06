"""Position operations for ReyaTester."""

from typing import TYPE_CHECKING

import asyncio
import logging
import time

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.config import REYA_DEX_ID
from sdk.reya_rest_api.models import LimitOrderParameters

from .utils import limit_order_params_to_order

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


class PositionOperations:
    """Position management operations."""

    def __init__(self, tester: "ReyaTester"):
        self._t = tester

    async def close_all(self, fail_if_none: bool = True) -> None:
        """Best-effort: try to flatten any open positions.

        Under perpOB (peer-to-peer matching, no AMM), a reduce-only IOC only
        closes a position if there's a counterparty on the book at a crossing
        price. Without orchestration between maker and taker testers, the
        cleanup IOC may end up CANCELLED with the position still open — that's
        expected, not a test failure. Tests that genuinely need a clean
        position slate should verify it explicitly via assertions on
        `positions()` rather than relying on this helper.
        """
        try:
            positions = await self._t.data.positions()
        except ApiException as e:
            logger.warning(f"Failed to get positions (API may not have market trackers in Redis): {e}")
            return None

        if len(positions) == 0:
            logger.warning("No position to close")
            if fail_if_none:
                assert False
            return None

        for symbol, _ in positions.items():
            # Re-fetch position to get current qty (may have changed due to trigger orders)
            current_position = await self._t.data.position(symbol)
            if current_position is None:
                logger.info(f"Position {symbol} already closed, skipping")
                continue

            # Sentinel prices anchored to oracle so the reduce-only IOC always
            # crosses any resting order without violating ME bounds. The ME
            # rejects `limit_px <= 0` and `limit_px > 2^64 / 1e9 ≈ 1.844e10`,
            # so the legacy `0` / `1e12` sentinels don't work under perpOB.
            # 0.5x / 1.5x oracle is wide enough to cross anything the test
            # suite places (which sits within ±5% of oracle).
            oracle_price = float(await self._t.data.current_price(symbol))
            close_price = oracle_price * (0.5 if current_position.side == Side.B else 1.5)

            limit_order_params = LimitOrderParameters(
                symbol=symbol,
                is_buy=not (current_position.side == Side.B),
                limit_px=str(round(close_price, 2)),
                qty=str(current_position.qty),
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )
            logger.debug(f"Order params: {limit_order_params}")

            try:
                await self._t.orders.create_limit(limit_order_params)
            except ApiException as e:
                logger.warning(f"Reduce-only close attempt failed for {symbol}: {e}")
                continue

        # Wait briefly for positions to clear — short timeout because under
        # perpOB the cleanup IOC may be CANCELLED with position still open
        # (no counterparty), and there's no point waiting in that case.
        start_time = time.time()
        timeout = 3
        while time.time() - start_time < timeout:
            position_after = await self._t.data.positions()
            if len(position_after) == 0:
                logger.info(f"✅ All positions closed (took {time.time() - start_time:.2f}s)")
                return
            await asyncio.sleep(0.1)

        # Timeout reached — best-effort, log and return rather than failing
        # the test fixture. See class docstring for rationale.
        position_after = await self._t.data.positions()
        if len(position_after) > 0:
            logger.warning(
                f"close_all: {len(position_after)} position(s) remain open after {timeout}s — "
                "under perpOB, reduce-only IOC requires a resting counterparty. "
                f"Symbols: {list(position_after.keys())}"
            )

    async def setup(
        self,
        symbol: str = "ETHRUSDPERP",
        is_buy: bool = True,
        qty: str = "0.01",
        price_multiplier: float = 1.01,
        reduce_only: bool = False,
    ) -> tuple[str, str]:
        """
        Set up a position by creating and executing a limit order.

        Returns:
            tuple[str, str]: (market_price, position_side) where position_side is 'B' for long, 'A' for short
        """
        market_price = await self._t.data.current_price(symbol)
        logger.info(f"Setting up {'long' if is_buy else 'short'} position at market price: ${market_price}")

        limit_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(float(market_price) * price_multiplier),
            is_buy=is_buy,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=reduce_only,
        )
        await self._t.orders.create_limit(limit_order_params)

        account_id = self._t.account_id
        if account_id is None:
            raise ValueError("account_id is required for position setup")
        expected_order = limit_order_params_to_order(limit_order_params, account_id)
        await self._t.wait.for_order_execution(expected_order)
        await self._t.check.no_open_orders()

        await asyncio.sleep(0.05)

        expected_side = Side.B if is_buy else Side.A
        await self._t.check.position(
            symbol=symbol,
            expected_exchange_id=REYA_DEX_ID,
            expected_account_id=account_id,
            expected_qty=qty,
            expected_side=expected_side,
        )

        return market_price, expected_side.value

    async def close(self, symbol: str, qty: str = "0.01") -> None:
        """Manually close a position using a market order."""
        position = await self._t.data.position(symbol)
        if position is None:
            raise RuntimeError("No position found to close")

        is_buy = position.side == Side.A

        # See close_all comment: oracle-anchored sentinel within ME bounds.
        oracle_price = float(await self._t.data.current_price(symbol))
        close_price = oracle_price * (1.5 if is_buy else 0.5)

        close_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(round(close_price, 2)),
            is_buy=is_buy,
            time_in_force=TimeInForce.IOC,
            qty=qty,
            reduce_only=True,
        )
        await self._t.orders.create_limit(close_order_params)

        account_id = self._t.account_id
        if account_id is None:
            raise ValueError("account_id is required for position close")
        expected_order = limit_order_params_to_order(close_order_params, account_id)
        execution = await self._t.wait.for_closing_order_execution(expected_order)
        await self._t.check.order_execution(execution, expected_order, qty)
        await self._t.check.position_not_open(symbol)

    async def flip(self, symbol: str, current_qty: str = "0.01", flip_qty: str = "0.02") -> None:
        """Flip a position from long to short or vice versa."""
        position = await self._t.data.position(symbol)
        if position is None:
            raise RuntimeError("No position found to flip")

        is_buy = position.side == Side.A

        # See close_all comment: oracle-anchored sentinel within ME bounds.
        oracle_price = float(await self._t.data.current_price(symbol))
        flip_price = oracle_price * (1.5 if is_buy else 0.5)

        flip_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px=str(round(flip_price, 2)),
            is_buy=is_buy,
            time_in_force=TimeInForce.IOC,
            qty=flip_qty,
            reduce_only=False,
        )
        await self._t.orders.create_limit(flip_order_params)

        account_id = self._t.account_id
        if account_id is None:
            raise ValueError("account_id is required for position flip")
        expected_order = limit_order_params_to_order(flip_order_params, account_id)
        execution = await self._t.wait.for_order_execution(expected_order)
        await self._t.check.order_execution(execution, expected_order, flip_qty)

        await asyncio.sleep(0.1)

        expected_side = Side.B if is_buy else Side.A
        remaining_qty = str(float(flip_qty) - float(current_qty))

        await self._t.check.position(
            symbol=symbol,
            expected_exchange_id=REYA_DEX_ID,
            expected_account_id=account_id,
            expected_qty=remaining_qty,
            expected_side=expected_side,
        )
