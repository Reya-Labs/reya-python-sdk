import pytest

from tests.helpers import ws_exec_prerequisites
from tests.helpers.ws_exec_prerequisites import ws_exec_account_env_vars, ws_exec_prerequisite_missing


def test_ws_exec_prerequisite_missing_always_fails():
    with pytest.raises(pytest.fail.Exception) as exc_info:
        ws_exec_prerequisite_missing(
            "ws-exec live tests need REYA_WS_EXEC_URL and PERP_*_1",
            missing_env=["REYA_WS_EXEC_URL", "PERP_PRIVATE_KEY_1"],
        )

    message = str(exc_info.value)
    assert "WS exec test prerequisites missing" in message
    assert "REYA_WS_EXEC_URL, PERP_PRIVATE_KEY_1" in message


def test_ws_exec_prerequisite_guard_has_no_escape_hatch():
    assert not any(name.startswith("ALLOW_") for name in dir(ws_exec_prerequisites))


def test_ws_exec_account_env_vars_for_perp_account():
    assert ws_exec_account_env_vars("PERP", 1) == (
        "PERP_PRIVATE_KEY_1",
        "PERP_ACCOUNT_ID_1",
        "PERP_WALLET_ADDRESS_1",
    )
