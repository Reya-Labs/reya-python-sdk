"""Offline checks for the live execution-bust session guard."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from sdk.open_api.models.execution_bust_reason import ExecutionBustReason
from tests.conftest import _unexpected_execution_bust_changes


def _bust(taker_order_id: str, reason: Any):
    return SimpleNamespace(taker_order_id=taker_order_id, reason=reason)


@pytest.mark.offline
def test_reduce_only_busts_are_allowed_by_session_guard() -> None:
    start = {"wallet-a": [_bust("1", "older bust")]}
    end = {
        "wallet-a": [
            _bust("1", "older bust"),
            _bust("2", "Reduce-Only order size above position size. Please refresh page and try again"),
        ]
    }

    assert not _unexpected_execution_bust_changes(start, end)


@pytest.mark.offline
def test_decoded_reduce_only_busts_are_allowed_by_session_guard() -> None:
    start = {"wallet-a": [_bust("1", "older bust")]}
    end = {
        "wallet-a": [
            _bust("1", "older bust"),
            _bust(
                "2",
                ExecutionBustReason.from_dict(
                    {
                        "reasonName": "ReduceOnlyConditionFailed",
                        "marketId": 1,
                        "accountId": 10,
                    }
                ),
            ),
        ]
    }

    assert not _unexpected_execution_bust_changes(start, end)


@pytest.mark.offline
def test_non_reduce_only_busts_still_fail_session_guard() -> None:
    start = {"wallet-a": [_bust("1", "older bust")]}
    end = {"wallet-a": [_bust("1", "older bust"), _bust("2", "unexpected settlement failure")]}

    assert _unexpected_execution_bust_changes(start, end) == {"wallet-a": (1, 2)}
