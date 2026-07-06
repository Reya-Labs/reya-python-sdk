from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

import pytest


def ws_exec_account_env_vars(prefix: str, account_number: int) -> tuple[str, str, str]:
    return (
        f"{prefix}_PRIVATE_KEY_{account_number}",
        f"{prefix}_ACCOUNT_ID_{account_number}",
        f"{prefix}_WALLET_ADDRESS_{account_number}",
    )


def missing_env_vars(env_vars: Iterable[str]) -> list[str]:
    return [name for name in env_vars if not os.environ.get(name)]


def ws_exec_prerequisite_missing(reason: str, *, missing_env: Sequence[str] = ()) -> None:
    details = f"WS exec test prerequisites missing: {reason}"
    if missing_env:
        details += f"; missing env vars: {', '.join(missing_env)}"

    pytest.fail(details, pytrace=False)
