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
        """Close all open positions."""
        try:
            positions = await self._t.data.positions()
        except ApiException as e:
            logger.warning(f"Failed to get positions (API may not have market trackers in Redis): {e}")
            if fail_if_none:
                logger.warning(
                    "Ignoring positions error since fail_if_none=True means we don't require positions to exist"
                )
            return None

        if len(positions) == 0:
            logger.warning("No position to close")
            if fail_if_none:
                assert False
            return None

        for symbol, position in positions.items():
            # Re-fetch position to get current qty (may have changed due to trigger orders)
            current_position = await self._t.data.position(symbol)
            if current_position is None:
                logger.info(f"Position {symbol} already closed, skipping")
                continue

            price_with_offset = 0 if current_position.side == Side.B else 1000000000000

            limit_order_params = LimitOrderParameters(
                symbol=symbol,
                is_buy=not (current_position.side == Side.B),
                limit_px=str(price_with_offset),
                qty=str(current_position.qty),
                time_in_force=TimeInForce.IOC,
                reduce_only=True,
            )
            logger.debug(f"Order params: {limit_order_params}")

            order_id = await self._t.orders.create_limit(limit_order_params)
            assert order_id is None

        # Wait for positions to be actually closed
        start_time = time.time()
        timeout = 10

        while time.time() - start_time < timeout:
            position_after = await self._t.data.positions()
            if len(position_after) == 0:
                elapsed_time = time.time() - start_time
                logger.info(f"✅ All positions closed successfully (took {elapsed_time:.2f}s)")
                return

            logger.debug(f"Still have {len(position_after)} positions, waiting...")
            await asyncio.sleep(0.05)

        # Timeout reached
        position_after = await self._t.data.positions()
        if len(position_after) > 0:
            logger.error(f"Failed to close positions after {timeout}s timeout: {position_after}")
            assert False

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

        close_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px="0",
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

        flip_order_params = LimitOrderParameters(
            symbol=symbol,
            limit_px="0",
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
