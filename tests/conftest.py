"""Pytest fixtures for the SDK's offline test suites."""

import pytest

from sdk.reya_rest_api import client as reya_client_module
from tests.offline_clock import PinnedClock, assert_pinned_clock_is_in_the_past


@pytest.fixture(autouse=True)
def pin_offline_clock(request, monkeypatch):
    """Freeze the client's clock for every `offline`-marked test.

    The offline suites pin their signed inputs to the April-2025 window the TS
    parity vectors were generated at. Leaving the client on the wall clock
    makes two things break in ways that read as logic regressions: a defaulted
    `deadline` changes every run, and any rule expressed against "now" compares
    a pinned past expiry against the real present. Pinning the clock is what
    keeps one client code path usable by both live and offline callers.
    """
    if request.node.get_closest_marker("offline") is None:
        return
    assert_pinned_clock_is_in_the_past()
    monkeypatch.setattr(reya_client_module, "time", PinnedClock())
