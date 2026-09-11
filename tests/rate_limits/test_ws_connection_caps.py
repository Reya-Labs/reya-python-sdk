"""Concurrent per-IP caps and forged-header resistance on both socket surfaces."""

from __future__ import annotations

import asyncio
import os
from urllib.parse import urlsplit

import pytest
from websocket import WebSocketBadStatusException, create_connection  # type: ignore[attr-defined]

from tests.rate_limits.rl_config import requires_rate_limits

pytestmark = [pytest.mark.rate_limits, requires_rate_limits]


@pytest.mark.parametrize(
    "url_key,cap_key",
    [
        ("REYA_WS_EXEC_URL", "RL_TEST_WS_CONNECTION_CAP"),
        ("REYA_WS_URL", "RL_TEST_MD_WS_CONNECTION_CAP"),
    ],
)
async def test_concurrent_cap_ignores_forged_headers_and_releases_slot(url_key, cap_key):
    url, raw_cap = os.environ.get(url_key), os.environ.get(cap_key)
    if not url or not raw_cap:
        pytest.skip(f"concurrent-cap coverage requires {url_key} and {cap_key}")
    assert urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1"), "concurrent-cap probe is localnet-only"
    cap = int(raw_cap)
    assert 1 <= cap <= 32, "concurrent-cap probe requires a small explicit deployment limit"
    sockets = []
    try:
        for _ in range(cap):
            sockets.append(await asyncio.to_thread(create_connection, url, timeout=10))
        for headers in (
            [],
            ["X-Forwarded-For: 198.51.100.42"],
            ["x-envoy-external-address: 203.0.113.42"],
            ["X-Forwarded-For: 198.51.100.43", "x-envoy-external-address: 203.0.113.43"],
        ):
            with pytest.raises(WebSocketBadStatusException) as rejected:
                extra = await asyncio.to_thread(create_connection, url, header=headers, timeout=10)
                sockets.append(extra)
            assert rejected.value.status_code == 429
        await asyncio.to_thread(sockets.pop().close)
        await asyncio.sleep(0.5)
        sockets.append(await asyncio.to_thread(create_connection, url, timeout=10))
    finally:
        for socket in sockets:
            await asyncio.to_thread(socket.close)
