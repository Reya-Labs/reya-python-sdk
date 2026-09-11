"""Explicit localnet control commands, executed without a shell."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess  # nosec B404 — operator-supplied argv, no shell

import pytest


def require_hook(action: str) -> str:
    name = f"RL_TEST_{action.upper()}_CMD"
    template = os.environ.get(name)
    if not template:
        pytest.skip(f"this live scenario requires {name}")
    return template


async def run_hook(action: str, wallet: str, account_id: int, expected: int = 0) -> str:
    template = require_hook(action)
    argv = shlex.split(template.format(wallet=wallet, account_id=account_id))
    result = await asyncio.to_thread(
        subprocess.run, argv, capture_output=True, text=True, check=False, timeout=90  # nosec B603
    )
    output = result.stdout + result.stderr
    assert result.returncode == expected, f"{action}: expected exit {expected}, got {result.returncode}: {output}"
    return output
