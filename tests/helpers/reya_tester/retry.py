"""Retry utilities for handling transient API errors."""

from typing import Callable, TypeVar

import asyncio
import logging
from collections.abc import Awaitable

from sdk.open_api.exceptions import ApiException, ServiceException

logger = logging.getLogger("reya.integration_tests")

T = TypeVar("T")


async def with_retry(
    operation: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    retry_delay: float = 1.0,
    operation_name: str = "operation",
) -> T:
    """Execute an async operation with retry logic for transient errors.

    Retries on:
    - 502 Bad Gateway
    - 503 Service Unavailable
    - 504 Gateway Timeout
    - SignatureExpired errors
    - fetch failed errors

    Args:
        operation: Async callable to execute
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
        operation_name: Name for logging purposes

    Returns:
        Result of the operation

    Raises:
        The last exception if all retries fail
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return await operation()
        except (ServiceException, ApiException) as e:
            last_exception = e

            # Check if this is a retryable error
            is_retryable = False
            error_msg = str(e)

            # Check for transient HTTP errors
            if isinstance(e, ServiceException):
                if hasattr(e, "status") and e.status in [502, 503, 504]:
                    is_retryable = True

            # Check for specific error messages
            if "SignatureExpired" in error_msg:
                is_retryable = True
            if "fetch failed" in error_msg:
                is_retryable = True
            if "Bad Gateway" in error_msg:
                is_retryable = True

            if is_retryable and attempt < max_retries:
                logger.warning(
                    f"⚠️ {operation_name} failed (attempt {attempt + 1}/{max_retries + 1}): {error_msg[:100]}... Retrying in {retry_delay}s"
                )
                await asyncio.sleep(retry_delay)
                continue

            # Not retryable or out of retries
            raise

    # Should not reach here, but just in case
    if last_exception:
        raise last_exception
    raise RuntimeError(f"{operation_name} failed after {max_retries + 1} attempts")
