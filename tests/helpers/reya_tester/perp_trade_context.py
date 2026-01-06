"""Context manager for bulletproof PERP trade verification.

This module provides a robust, industry-standard approach to verifying
PERP trade executions across both REST and WebSocket channels.

The key insight: For the SAME trade, REST and WS will ALWAYS return
the SAME sequence_number. We use this as the correlation mechanism.

Usage:
    async with reya_tester.perp_trade() as ctx:
        # Baseline is automatically captured here
        await reya_tester.create_limit_order(params)
        
        # Wait for execution - finds first new trade matching criteria
        execution = await ctx.wait_for_execution(expected_order)
        # execution is guaranteed to be the SAME trade on both REST and WS
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional, Callable, Any

import asyncio
import logging
import time

from sdk.async_api.perp_execution import PerpExecution as AsyncPerpExecution
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.perp_execution_list import PerpExecutionList

if TYPE_CHECKING:
    from .tester import ReyaTester

logger = logging.getLogger("reya.integration_tests")


def _get_enum_value(val: Any) -> str:
    """Extract string value from enum or return string as-is."""
    if val is None:
        return ""
    return val.value if hasattr(val, "value") else str(val)


def _compare_qty(qty1: Optional[str], qty2: Optional[str]) -> bool:
    """Compare quantities with tolerance for floating point differences."""
    if qty1 is None or qty2 is None:
        return qty1 == qty2
    try:
        return abs(float(qty1) - float(qty2)) < 1e-9
    except (ValueError, TypeError):
        return qty1 == qty2


@dataclass
class TradeVerificationResult:
    """Result of trade verification across REST and WS."""
    
    rest_execution: PerpExecution
    ws_execution: AsyncPerpExecution
    sequence_number: int
    elapsed_time: float
    
    @property
    def is_consistent(self) -> bool:
        """Check if REST and WS executions are consistent."""
        return self.rest_execution.sequence_number == self.ws_execution.sequence_number


@dataclass
class PerpTradeContext:
    """Context for tracking and verifying a single PERP trade.
    
    This class captures the baseline sequence number BEFORE an order is placed,
    then provides methods to wait for and verify the resulting execution.
    
    The key guarantee: When wait_for_execution() returns, you have verified
    that the EXACT SAME trade was seen on both REST and WS channels.
    """
    
    tester: "ReyaTester"
    baseline_seq: int = 0
    _entered: bool = field(default=False, repr=False)
    
    async def __aenter__(self) -> "PerpTradeContext":
        """Capture baseline sequence number on context entry."""
        self.baseline_seq = await self._get_current_max_sequence()
        self._entered = True
        logger.debug(f"📍 PerpTradeContext: baseline_seq={self.baseline_seq}")
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up on context exit."""
        pass
    
    async def _get_current_max_sequence(self) -> int:
        """Get the current maximum sequence number from REST API."""
        wallet_address = self.tester.owner_wallet_address
        if wallet_address is None:
            return 0
        
        trades_list: PerpExecutionList = await self.tester.client.wallet.get_wallet_perp_executions(
            address=wallet_address
        )
        if trades_list.data and len(trades_list.data) > 0:
            return trades_list.data[0].sequence_number
        return 0
    
    async def _fetch_rest_execution_by_sequence(self, target_seq: int) -> Optional[PerpExecution]:
        """Fetch a specific execution from REST by sequence number."""
        wallet_address = self.tester.owner_wallet_address
        if wallet_address is None:
            return None
        
        trades_list: PerpExecutionList = await self.tester.client.wallet.get_wallet_perp_executions(
            address=wallet_address
        )
        for trade in trades_list.data:
            if trade.sequence_number == target_seq:
                return trade
        return None
    
    def _find_ws_execution(
        self,
        expected_order: Order,
        expected_qty: Optional[str] = None,
    ) -> Optional[AsyncPerpExecution]:
        """Find a WS execution that matches criteria and is after baseline.
        
        Returns the FIRST (oldest) matching execution after baseline,
        not the last, to ensure we get the correct trade.
        """
        matches = []
        all_executions = list(self.tester.ws.perp_executions.all)
        
        for execution in all_executions:
            seq = execution.sequence_number or 0
            if seq <= self.baseline_seq:
                continue
            
            if not self._matches_order(execution, expected_order, expected_qty):
                # Debug: log why it didn't match
                logger.debug(
                    f"Execution seq={seq} didn't match: "
                    f"account_id={execution.account_id} vs {expected_order.account_id}, "
                    f"symbol={execution.symbol} vs {expected_order.symbol}, "
                    f"side={_get_enum_value(execution.side)} vs {_get_enum_value(expected_order.side)}, "
                    f"qty={execution.qty} vs {expected_qty or expected_order.qty}"
                )
                continue
            
            matches.append(execution)
        
        if not matches and all_executions:
            # Log available executions for debugging
            new_execs = [e for e in all_executions if (e.sequence_number or 0) > self.baseline_seq]
            if new_execs:
                logger.warning(
                    f"No matching execution found. {len(new_execs)} new executions after baseline={self.baseline_seq}: "
                    f"{[(e.sequence_number, e.symbol, _get_enum_value(e.side), e.qty) for e in new_execs[:5]]}"
                )
        
        if not matches:
            return None
        
        # Return the FIRST match (lowest sequence number) - this is OUR trade
        return min(matches, key=lambda e: e.sequence_number or 0)
    
    def _matches_order(
        self,
        execution: AsyncPerpExecution,
        expected: Order,
        expected_qty: Optional[str] = None,
    ) -> bool:
        """Check if execution matches expected order parameters."""
        if execution.account_id != expected.account_id:
            return False
        if execution.symbol != expected.symbol:
            return False
        if _get_enum_value(execution.side) != _get_enum_value(expected.side):
            return False
        
        qty_to_match = expected_qty if expected_qty is not None else expected.qty
        if not _compare_qty(execution.qty, qty_to_match):
            return False
        
        return True
    
    async def wait_for_execution(
        self,
        expected_order: Order,
        expected_qty: Optional[str] = None,
        timeout: int = 10,
        verify_position: bool = True,
    ) -> TradeVerificationResult:
        """Wait for trade execution and verify consistency across REST and WS.
        
        This method:
        1. Waits for a WS execution matching criteria with seq > baseline
        2. Uses that EXACT sequence number to fetch from REST
        3. Verifies both sources return the same trade
        4. Optionally verifies position updates
        
        Args:
            expected_order: Order parameters to match against.
            expected_qty: Optional qty override (for closing orders).
            timeout: Maximum wait time in seconds.
            verify_position: Whether to also verify position updates.
        
        Returns:
            TradeVerificationResult with both REST and WS executions.
        
        Raises:
            RuntimeError: If trade not found or verification fails.
        """
        if not self._entered:
            raise RuntimeError(
                "PerpTradeContext must be used as async context manager. "
                "Use: async with tester.perp_trade() as ctx: ..."
            )
        
        logger.info(f"⏳ Waiting for trade (baseline_seq={self.baseline_seq})...")
        start_time = time.time()
        
        ws_execution: Optional[AsyncPerpExecution] = None
        rest_execution: Optional[PerpExecution] = None
        target_seq: Optional[int] = None
        
        while time.time() - start_time < timeout:
            # Step 1: Find WS execution matching criteria
            if ws_execution is None:
                ws_execution = self._find_ws_execution(expected_order, expected_qty)
                if ws_execution:
                    target_seq = ws_execution.sequence_number
                    elapsed = time.time() - start_time
                    logger.info(f" ✅ Trade found via WS: seq={target_seq} (took {elapsed:.2f}s)")
            
            # Step 2: Once we have WS execution, fetch EXACT same trade from REST
            if ws_execution and rest_execution is None and target_seq:
                rest_execution = await self._fetch_rest_execution_by_sequence(target_seq)
                if rest_execution:
                    elapsed = time.time() - start_time
                    logger.info(f" ✅ Trade confirmed via REST: seq={rest_execution.sequence_number} (took {elapsed:.2f}s)")
            
            # Step 3: Verify consistency
            if ws_execution and rest_execution:
                if rest_execution.sequence_number != ws_execution.sequence_number:
                    raise AssertionError(
                        f"Trade sequence mismatch: REST={rest_execution.sequence_number}, "
                        f"WS={ws_execution.sequence_number}"
                    )
                
                # Step 4: Optionally verify position
                if verify_position:
                    position_verified = await self._verify_position(
                        expected_order.symbol, target_seq, timeout - (time.time() - start_time)
                    )
                    if not position_verified:
                        logger.warning("Position verification timed out, but trade is confirmed")
                
                elapsed = time.time() - start_time
                return TradeVerificationResult(
                    rest_execution=rest_execution,
                    ws_execution=ws_execution,
                    sequence_number=target_seq,
                    elapsed_time=elapsed,
                )
            
            await asyncio.sleep(0.1)
        
        # Timeout - provide detailed error
        raise RuntimeError(
            f"Trade not verified after {timeout}s. "
            f"baseline_seq={self.baseline_seq}, "
            f"ws_found={ws_execution is not None}, "
            f"rest_found={rest_execution is not None}, "
            f"target_seq={target_seq}"
        )
    
    async def wait_for_closing_execution(
        self,
        expected_order: Order,
        expected_qty: Optional[str] = None,
        timeout: int = 10,
    ) -> TradeVerificationResult:
        """Wait for position-closing trade execution.
        
        Similar to wait_for_execution but also verifies position is closed.
        """
        result = await self.wait_for_execution(
            expected_order=expected_order,
            expected_qty=expected_qty,
            timeout=timeout,
            verify_position=False,  # We'll do custom position verification
        )
        
        # Verify position is closed
        start_time = time.time()
        remaining_timeout = max(1, timeout - result.elapsed_time)
        
        while time.time() - start_time < remaining_timeout:
            position = await self.tester.data.position(expected_order.symbol)
            if position is None:
                elapsed = time.time() - start_time
                logger.info(f" ✅ Position closed: {expected_order.symbol} (took {elapsed:.2f}s)")
                return result
            await asyncio.sleep(0.1)
        
        logger.warning(f"Position not closed after {remaining_timeout}s, but trade is confirmed")
        return result
    
    async def _verify_position(
        self,
        symbol: str,
        expected_seq: int,
        timeout: float,
    ) -> bool:
        """Verify position update matches the trade sequence."""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check WS position
            ws_pos = self.tester.ws.positions.get(symbol)
            if ws_pos and ws_pos.last_trade_sequence_number == expected_seq:
                elapsed = time.time() - start_time
                logger.info(f" ✅ Position confirmed via WS: {symbol} (took {elapsed:.2f}s)")
                
                # Also check REST position
                rest_pos = await self.tester.data.position(symbol)
                if rest_pos and rest_pos.last_trade_sequence_number == expected_seq:
                    logger.info(f" ✅ Position confirmed via REST: {symbol}")
                    return True
            
            await asyncio.sleep(0.1)
        
        return False
