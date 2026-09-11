"""Strict REST/WS ledger assertions for controlled Localnet transactions.

The SDK does not yet dispatch the transfers WS channel, so observe its public
wire contract directly with websocket-client. No live frame is synthesized from
REST, and the subscription must be acknowledged before the producer runs.
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from decimal import Decimal
from urllib.parse import urlsplit

from websocket import WebSocketTimeoutException, create_connection

from sdk.open_api.models.transfer import Transfer
from tests.helpers import ReyaTester


def localnet_url(name: str, scheme: str) -> str:
    value = os.environ.get(name, "")
    parsed = urlsplit(value)
    if (
        os.environ.get("CHAIN_ID") != "31337"
        or parsed.scheme != scheme
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError(f"{name} must identify the loopback Localnet {scheme} endpoint")
    return value


async def net_deposits(tester: ReyaTester) -> Decimal:
    balances = await tester.client.get_account_balances()
    matches = [row for row in balances if row.account_id == tester.account_id and row.asset == "RUSD"]
    assert len(matches) == 1, "the controlled account must have one RUSD balance"
    return Decimal(matches[0].balance_deprecated)


async def assert_net_deposits(tester: ReyaTester, expected: Decimal) -> None:
    deadline = asyncio.get_running_loop().time() + 30
    while True:
        actual = await net_deposits(tester)
        if actual == expected:
            return
        assert asyncio.get_running_loop().time() < deadline, f"net deposits: {actual} != {expected}"
        await asyncio.sleep(0.2)


def assert_running_net_deposits(entries: list[Transfer], baseline: Decimal) -> Decimal:
    assert entries, "no ledger entries to reconcile"
    for entry in sorted(entries, key=lambda item: item.sequence_number):
        baseline += Decimal(entry.amount)
        assert Decimal(entry.net_deposits_after) == baseline, entry.to_dict()
    return baseline


async def wait_for_transaction_transfers(tester: ReyaTester, transaction_hash: str, expected: int) -> list[Transfer]:
    deadline = asyncio.get_running_loop().time() + 30
    while True:
        # Strict: a 404, an empty ledger, or missing legs must fail on Localnet.
        page = await tester.client.get_transfers()
        entries = [
            entry
            for entry in page.data
            if entry.transaction_hash.lower() == transaction_hash.lower() and entry.account_id == tester.account_id
        ]
        if len(entries) >= expected:
            assert len(entries) == expected, [entry.to_dict() for entry in entries]
            return entries
        assert asyncio.get_running_loop().time() < deadline, f"missing transfers for {transaction_hash}: {entries}"
        await asyncio.sleep(0.2)


class WalletTransfersSocket:
    """One acknowledged subscription, preserving live events independently of its snapshot."""

    def __init__(self, socket, channel: str):
        self.socket = socket
        self.channel = channel
        self.snapshot: list[Transfer] = []
        self.live: list[Transfer] = []

    async def receive(self, deadline: float) -> dict:
        while asyncio.get_running_loop().time() < deadline:
            try:
                raw = await asyncio.to_thread(self.socket.recv)
            except WebSocketTimeoutException:
                continue
            assert raw, "transfers WebSocket closed before the expected message"
            frame = json.loads(raw)
            assert isinstance(frame, dict), "expected a WebSocket message object"
            assert frame.get("type") != "error", frame
            if frame.get("type") == "ping":
                await asyncio.to_thread(self.socket.send, json.dumps({"type": "pong"}))
                continue
            if frame.get("channel") == self.channel:
                return frame
        raise AssertionError("timed out waiting for the transfers WebSocket")

    async def assert_live_matches(self, expected: list[Transfer]) -> None:
        assert expected, "live parity must compare actual produced transfers"
        ids = {entry.sequence_number for entry in expected}
        assert len(ids) == len(expected), "duplicate REST entry ids"
        deadline = asyncio.get_running_loop().time() + 30
        while not ids.issubset({entry.sequence_number for entry in self.live}):
            frame = await self.receive(deadline)
            assert frame["type"] == "channel_data", frame
            self.live.extend(Transfer.model_validate(row) for row in frame["data"])
        observed = [entry for entry in self.live if entry.sequence_number in ids]
        assert len(observed) == len(expected), "duplicate live transfers"
        assert {entry.sequence_number: entry.to_dict() for entry in observed} == {
            entry.sequence_number: entry.to_dict() for entry in expected
        }


@asynccontextmanager
async def wallet_transfers_socket(tester: ReyaTester):
    localnet_url("REYA_API_URL", "http")
    url = localnet_url("REYA_WS_URL", "ws")
    assert tester.owner_wallet_address is not None
    channel = f"/v2/wallet/{tester.owner_wallet_address.lower()}/transfers"
    socket = await asyncio.to_thread(create_connection, url, timeout=1)
    observer = WalletTransfersSocket(socket, channel)
    try:
        await asyncio.to_thread(socket.send, json.dumps({"type": "subscribe", "channel": channel}))
        frame = await observer.receive(asyncio.get_running_loop().time() + 30)
        assert frame["type"] == "subscribed", frame
        observer.snapshot = [Transfer.model_validate(row) for row in frame["contents"]["data"]]
        yield observer
    finally:
        await asyncio.to_thread(socket.close)


async def assert_transfer_snapshot(tester: ReyaTester, expected: list[Transfer]) -> None:
    async with wallet_transfers_socket(tester) as observer:
        snapshot = {entry.sequence_number: entry.to_dict() for entry in observer.snapshot}
        for entry in expected:
            assert snapshot.get(entry.sequence_number) == entry.to_dict(), entry.to_dict()
