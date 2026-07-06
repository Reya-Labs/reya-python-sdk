import pytest

from tests.helpers import spot_prerequisites
from tests.helpers.spot_prerequisites import missing_env_vars, spot_prerequisite_missing


def test_spot_prerequisite_missing_always_fails():
    with pytest.raises(pytest.fail.Exception) as exc_info:
        spot_prerequisite_missing(
            "Asset 'ETH' not available",
            missing_env=["SPOT_PRIVATE_KEY_1", "SPOT_ACCOUNT_ID_1"],
        )

    message = str(exc_info.value)
    assert "Spot test prerequisites missing: Asset 'ETH' not available" in message
    assert "SPOT_PRIVATE_KEY_1, SPOT_ACCOUNT_ID_1" in message


def test_spot_prerequisite_guard_has_no_escape_hatch():
    assert not any(name.startswith("ALLOW_") for name in dir(spot_prerequisites))


def test_missing_env_vars_reports_names_without_values(monkeypatch):
    monkeypatch.setenv("SPOT_PRIVATE_KEY_1", "secret-value")
    monkeypatch.delenv("SPOT_ACCOUNT_ID_1", raising=False)

    assert missing_env_vars(("SPOT_PRIVATE_KEY_1", "SPOT_ACCOUNT_ID_1")) == ["SPOT_ACCOUNT_ID_1"]


def test_regular_product_gap_skip_remains_a_skip():
    with pytest.raises(pytest.skip.Exception) as exc_info:
        pytest.skip("TP/SL is a server-side facade, not a live feature yet (PRO-150)")

    assert "PRO-150" in str(exc_info.value)
