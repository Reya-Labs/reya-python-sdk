"""A pinned clock for the offline suites.

The offline suites (parity, wire serialization, client guards) assert on signed
bytes and on payload fields derived from pinned inputs — deadlines and expiries
in the April-2025 window the TS vectors were generated at. Any wall-clock read
on that path reintroduces a moving input: a defaulted deadline drifts every
run, and a client-side rule expressed against "now" compares a pinned past
expiry to the real present and refuses it.

Pinning the clock is what lets both live and offline callers share one code
path. It is applied by the `pin_offline_clock` fixture in `tests/conftest.py`
to every test carrying the `offline` marker, so an offline test that reaches
the client's time-dependent defaults gets the pinned instant rather than today.
"""

from typing import Any

import time as _real_time

# 2025-04-18T18:13:20Z — the instant the pinned parity vectors were signed at.
# Every pinned deadline/expiry in the offline suites sits at or after it.
OFFLINE_CLOCK_S = 1_745_000_000


class PinnedClock:
    """Stand-in for the `time` module whose `time()` never advances."""

    def __init__(self, now_s: int = OFFLINE_CLOCK_S) -> None:
        self._now_s = now_s

    def time(self) -> float:
        return float(self._now_s)

    def time_ns(self) -> int:
        return self._now_s * 1_000_000_000

    def __getattr__(self, name: str) -> Any:
        return getattr(_real_time, name)
