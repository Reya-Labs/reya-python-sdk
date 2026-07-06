from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

import pytest

ALLOW_SPOT_TEST_SKIPS_ENV = "ALLOW_SPOT_TEST_SKIPS"


def spot_account_env_vars(account_number: int) -> tuple[str, str, str]:
    return (
        f"SPOT_PRIVATE_KEY_{account_number}",
        f"SPOT_ACCOUNT_ID_{account_number}",
        f"SPOT_WALLET_ADDRESS_{account_number}",
    )


def missing_env_vars(env_vars: Iterable[str]) -> list[str]:
    return [name for name in env_vars if not os.environ.get(name)]


def spot_prerequisite_missing(reason: str, *, missing_env: Sequence[str] = ()) -> None:
    details = f"Spot test prerequisites missing: {reason}"
    if missing_env:
        details += f"; missing env vars: {', '.join(missing_env)}"
    details += f". Set {ALLOW_SPOT_TEST_SKIPS_ENV}=1 only when this is an intentionally non-spot environment."

    if os.environ.get(ALLOW_SPOT_TEST_SKIPS_ENV) == "1":
        pytest.skip(details)
    pytest.fail(details, pytrace=False)
