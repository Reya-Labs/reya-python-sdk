"""Async polling helper for the market-data tests.

Market-data surfaces (WS frames, REST projections) settle asynchronously behind
the ingest drain, so tests poll for a condition within a generous window rather
than asserting on a fixed sleep (which flakes under full-suite load).
"""

from __future__ import annotations

from typing import Callable

import asyncio
import inspect
import time
from collections.abc import Awaitable


async def wait_until(
    predicate: Callable[[], bool | Awaitable[bool]],
    *,
    timeout: float = 10.0,
    interval: float = 0.1,
) -> bool:
    """Poll ``predicate`` until it is truthy or ``timeout`` elapses.

    ``predicate`` may be sync or async. Returns whether it became true.
    """
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if inspect.isawaitable(result):
            result = await result
        if result:
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval)
