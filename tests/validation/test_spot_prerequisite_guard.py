import pytest

from tests.helpers.spot_prerequisites import ALLOW_SPOT_TEST_SKIPS_ENV, missing_env_vars, spot_prerequisite_missing


def test_spot_prerequisite_missing_fails_by_default(monkeypatch):
    monkeypatch.delenv(ALLOW_SPOT_TEST_SKIPS_ENV, raising=False)

    with pytest.raises(pytest.fail.Exception) as exc_info:
        spot_prerequisite_missing(
            "Asset 'ETH' not available",
            missing_env=["SPOT_PRIVATE_KEY_1", "SPOT_ACCOUNT_ID_1"],
        )

    message = str(exc_info.value)
    assert "Spot test prerequisites missing: Asset 'ETH' not available" in message
    assert "SPOT_PRIVATE_KEY_1, SPOT_ACCOUNT_ID_1" in message
    assert ALLOW_SPOT_TEST_SKIPS_ENV in message


def test_spot_prerequisite_missing_can_explicitly_skip(monkeypatch):
    monkeypatch.setenv(ALLOW_SPOT_TEST_SKIPS_ENV, "1")

    with pytest.raises(pytest.skip.Exception) as exc_info:
        spot_prerequisite_missing("Insufficient balances for SPOT tests")

    assert "Insufficient balances for SPOT tests" in str(exc_info.value)


def test_missing_env_vars_reports_names_without_values(monkeypatch):
    monkeypatch.setenv("SPOT_PRIVATE_KEY_1", "secret-value")
    monkeypatch.delenv("SPOT_ACCOUNT_ID_1", raising=False)

    assert missing_env_vars(("SPOT_PRIVATE_KEY_1", "SPOT_ACCOUNT_ID_1")) == ["SPOT_ACCOUNT_ID_1"]


def test_regular_product_gap_skip_remains_a_skip():
    with pytest.raises(pytest.skip.Exception) as exc_info:
        pytest.skip("TP/SL is a server-side facade, not a live feature yet (PRO-150)")

    assert "PRO-150" in str(exc_info.value)
