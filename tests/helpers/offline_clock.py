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
#
# It MUST stay in the past. The pin also covers `_get_next_nonce`, whose
# per-wallet watermark is class-level and shared with every later live test in
# the same session. A past instant is discarded by that watermark's max(); a
# future one would raise it, and every subsequent live order would sign an
# inflated nonce that the engine burns as that signer's permanent floor.
OFFLINE_CLOCK_S = 1_745_000_000


def assert_pinned_clock_is_in_the_past() -> None:
    """Guard the constraint documented above, at the point it is applied."""
    if OFFLINE_CLOCK_S >= _real_time.time():
        raise AssertionError(
            "OFFLINE_CLOCK_S must stay in the past: pinning it forward raises the "
            "class-level per-wallet nonce watermark and poisons every later live "
            "test in the same session."
        )


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
