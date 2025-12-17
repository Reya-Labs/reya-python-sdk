"""Unified matching logic for executions and orders.

This module provides a single source of truth for matching WebSocket/REST
events against expected values, supporting both perp and spot execution types.
"""

from typing import Any, Optional, Union

from sdk.async_api.order import Order as AsyncOrder
from sdk.async_api.perp_execution import PerpExecution as AsyncPerpExecution
from sdk.async_api.spot_execution import SpotExecution as AsyncSpotExecution
from sdk.open_api.models.order import Order
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.spot_execution import SpotExecution


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


class ExecutionMatcher:
    """Unified matching for perp and spot executions.

    Provides static methods for matching executions against expected orders,
    handling the differences between perp (no order_id) and spot (has order_id).
    """

    @staticmethod
    def match_perp(
        execution: Union[PerpExecution, AsyncPerpExecution],
        expected: Order,
        expected_qty: Optional[str] = None,
    ) -> bool:
        """Match a perp execution against expected order values.

        Note: PerpExecution does NOT have order_id, so we match by:
        - account_id, symbol, side, qty

        Args:
            execution: The perp execution to check.
            expected: The expected order to match against.
            expected_qty: Optional override for qty comparison (used for closing orders).

        Returns:
            True if execution matches expected values.
        """
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

    @staticmethod
    def match_spot(
        execution: Union[SpotExecution, AsyncSpotExecution],
        expected: Order,
    ) -> bool:
        """Match a spot execution against expected values.

        Performs strict matching on ALL important fields:
        - order_id, account_id, symbol, side, qty

        Args:
            execution: The spot execution to check.
            expected: The expected order to match against.

        Returns:
            True if execution matches expected values.
        """
        if str(execution.order_id) != str(expected.order_id):
            return False
        if execution.account_id != expected.account_id:
            return False
        if execution.symbol != expected.symbol:
            return False
        if _get_enum_value(execution.side) != _get_enum_value(expected.side):
            return False
        if not _compare_qty(execution.qty, expected.qty):
            return False

        return True


class ValidationResult:
    """Result of field validation with error details."""

    def __init__(self) -> None:
        self._errors: list[str] = []

    def add_error(self, message: str) -> None:
        """Add a validation error."""
        self._errors.append(message)

    @property
    def is_valid(self) -> bool:
        """Return True if no validation errors."""
        return len(self._errors) == 0

    @property
    def errors(self) -> list[str]:
        """Return list of validation error messages."""
        return self._errors.copy()


class FieldValidator:
    """Validates execution fields against expected values with detailed errors."""

    @staticmethod
    def validate_spot_execution(
        execution: Union[SpotExecution, AsyncSpotExecution],
        expected: Order,
    ) -> ValidationResult:
        """Validate all key fields of a spot execution.

        Args:
            execution: The spot execution to validate.
            expected: The expected order values.

        Returns:
            ValidationResult with any errors found.
        """
        result = ValidationResult()

        if str(execution.order_id) != str(expected.order_id):
            result.add_error(f"order_id mismatch: expected {expected.order_id}, got {execution.order_id}")
        if execution.account_id != expected.account_id:
            result.add_error(f"account_id mismatch: expected {expected.account_id}, got {execution.account_id}")
        if execution.symbol != expected.symbol:
            result.add_error(f"symbol mismatch: expected {expected.symbol}, got {execution.symbol}")

        exec_side = _get_enum_value(execution.side)
        expected_side = _get_enum_value(expected.side)
        if exec_side != expected_side:
            result.add_error(f"side mismatch: expected {expected_side}, got {exec_side}")

        if not _compare_qty(execution.qty, expected.qty):
            result.add_error(f"qty mismatch: expected {expected.qty}, got {execution.qty}")

        return result

    @staticmethod
    def validate_order_change(
        order: AsyncOrder,
        expected: Order,
    ) -> ValidationResult:
        """Validate all key fields of a WebSocket order change.

        Args:
            order: The WebSocket order change to validate.
            expected: The expected order values.

        Returns:
            ValidationResult with any errors found.
        """
        result = ValidationResult()

        if str(order.order_id) != str(expected.order_id):
            result.add_error(f"order_id mismatch: expected {expected.order_id}, got {order.order_id}")
        if order.account_id != expected.account_id:
            result.add_error(f"account_id mismatch: expected {expected.account_id}, got {order.account_id}")
        if order.symbol != expected.symbol:
            result.add_error(f"symbol mismatch: expected {expected.symbol}, got {order.symbol}")

        order_side = _get_enum_value(order.side)
        expected_side = _get_enum_value(expected.side)
        if order_side != expected_side:
            result.add_error(f"side mismatch: expected {expected_side}, got {order_side}")

        if expected.qty and hasattr(order, "qty") and order.qty:
            if not _compare_qty(order.qty, expected.qty):
                result.add_error(f"qty mismatch: expected {expected.qty}, got {order.qty}")

        if expected.order_type and hasattr(order, "order_type") and order.order_type:
            order_type = _get_enum_value(order.order_type)
            expected_type = _get_enum_value(expected.order_type)
            if order_type != expected_type:
                result.add_error(f"order_type mismatch: expected {expected_type}, got {order_type}")

        if expected.time_in_force and hasattr(order, "time_in_force") and order.time_in_force:
            order_tif = _get_enum_value(order.time_in_force)
            expected_tif = _get_enum_value(expected.time_in_force)
            if order_tif != expected_tif:
                result.add_error(f"time_in_force mismatch: expected {expected_tif}, got {order_tif}")

        if expected.limit_px and hasattr(order, "limit_px") and order.limit_px:
            try:
                if abs(float(order.limit_px) - float(expected.limit_px)) > 1e-6:
                    result.add_error(f"limit_px mismatch: expected {expected.limit_px}, got {order.limit_px}")
            except (ValueError, TypeError):
                pass

        return result
