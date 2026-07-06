from __future__ import annotations

from dataclasses import dataclass

from tests.conftest import _strict_prerequisite_missing_env


@dataclass
class _Item:
    markers: set[str]

    def get_closest_marker(self, name: str):
        return object() if name in self.markers else None


def _clear_env(monkeypatch, names: tuple[str, ...]) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


def test_missing_ws_exec_env_is_detected_before_execution_busts_guard_api_calls(monkeypatch):
    required = (
        "REYA_WS_EXEC_URL",
        "SPOT_PRIVATE_KEY_1",
        "SPOT_ACCOUNT_ID_1",
        "SPOT_WALLET_ADDRESS_1",
        "SPOT_PRIVATE_KEY_2",
        "SPOT_ACCOUNT_ID_2",
        "SPOT_WALLET_ADDRESS_2",
        "PERP_PRIVATE_KEY_1",
        "PERP_ACCOUNT_ID_1",
        "PERP_WALLET_ADDRESS_1",
    )
    _clear_env(monkeypatch, required)

    assert _strict_prerequisite_missing_env([_Item({"strict_ws_exec_prerequisites"})]) == list(required)


def test_missing_spot_env_is_detected_before_execution_busts_guard_api_calls(monkeypatch):
    required = (
        "SPOT_PRIVATE_KEY_1",
        "SPOT_ACCOUNT_ID_1",
        "SPOT_WALLET_ADDRESS_1",
        "SPOT_PRIVATE_KEY_2",
        "SPOT_ACCOUNT_ID_2",
        "SPOT_WALLET_ADDRESS_2",
    )
    _clear_env(monkeypatch, required)

    assert _strict_prerequisite_missing_env([_Item({"strict_spot_prerequisites"})]) == list(required)


def test_non_strict_items_do_not_disable_execution_busts_guard(monkeypatch):
    monkeypatch.delenv("REYA_WS_EXEC_URL", raising=False)

    assert not _strict_prerequisite_missing_env([_Item(set())])
