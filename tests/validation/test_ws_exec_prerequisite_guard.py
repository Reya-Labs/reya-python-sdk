import pytest

from tests.helpers.ws_exec_prerequisites import (
    ALLOW_WS_EXEC_TEST_SKIPS_ENV,
    ws_exec_account_env_vars,
    ws_exec_prerequisite_missing,
)


def test_ws_exec_prerequisite_missing_fails_by_default(monkeypatch):
    monkeypatch.delenv(ALLOW_WS_EXEC_TEST_SKIPS_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception) as exc_info:
        ws_exec_prerequisite_missing(
            "ws-exec live tests need REYA_WS_EXEC_URL and PERP_*_1",
            missing_env=["REYA_WS_EXEC_URL", "PERP_PRIVATE_KEY_1"],
        )

    message = str(exc_info.value)
    assert "WS exec test prerequisites missing" in message
    assert "REYA_WS_EXEC_URL, PERP_PRIVATE_KEY_1" in message
    assert ALLOW_WS_EXEC_TEST_SKIPS_ENV in message


def test_ws_exec_prerequisite_missing_can_explicitly_skip(monkeypatch):
    monkeypatch.setenv(ALLOW_WS_EXEC_TEST_SKIPS_ENV, "1")

    with pytest.raises(pytest.skip.Exception) as exc_info:
        ws_exec_prerequisite_missing("ws-exec disabled in this environment")

    assert "ws-exec disabled in this environment" in str(exc_info.value)


def test_ws_exec_account_env_vars_for_perp_account():
    assert ws_exec_account_env_vars("PERP", 1) == (
        "PERP_PRIVATE_KEY_1",
        "PERP_ACCOUNT_ID_1",
        "PERP_WALLET_ADDRESS_1",
    )
