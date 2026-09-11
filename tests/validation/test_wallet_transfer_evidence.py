"""Prove the Localnet evidence checks reject missing or incorrect observations."""

from types import SimpleNamespace

import json
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

import pytest

from sdk.open_api.models.transfer import Transfer
from tests.helpers import ReyaTester
from tests.helpers.wallet_transfers import (
    WalletTransfersSocket,
    assert_running_net_deposits,
    localnet_url,
    wait_for_transaction_transfers,
)

pytestmark = pytest.mark.offline
CHANNEL = "/v2/wallet/0x1111111111111111111111111111111111111111/transfers"
TX = "0x" + "ab" * 32


def entry(sequence=2, amount="-1", after="9"):
    return Transfer.model_validate(
        {
            "sequenceNumber": sequence,
            "accountId": 123,
            "asset": "RUSD",
            "amount": amount,
            "netDepositsAfter": after,
            "type": "WITHDRAWAL",
            "timestamp": 1,
            "transactionHash": TX,
        }
    )


class Socket:
    def __init__(self, frames):
        self.frames = iter(frames)

    def recv(self):
        return next(self.frames, "")


def test_net_deposits_follow_chain_order():
    rows = [entry(5, "0.2", "9.2"), entry()]
    assert assert_running_net_deposits(rows, Decimal(10)) == Decimal("9.2")


@pytest.mark.parametrize("rows", [[], [entry(after="10")], [entry(), entry(5, "0.2", "9.3")]])
def test_net_deposits_reject_missing_or_incorrect_legs(rows):
    with pytest.raises(AssertionError):
        assert_running_net_deposits(rows, Decimal(10))


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "error", "message": "provider missing"},
        {"type": "subscribed", "channel": CHANNEL, "contents": {"data": [entry().to_dict()]}},
        {"type": "channel_data", "channel": CHANNEL, "data": [entry(after="10").to_dict()]},
        {"type": "channel_data", "channel": CHANNEL, "data": [entry().to_dict(), entry().to_dict()]},
    ],
)
@pytest.mark.asyncio
async def test_live_evidence_rejects_errors_snapshot_substitution_and_bad_rows(frame):
    observer = WalletTransfersSocket(Socket([json.dumps(frame)]), CHANNEL)
    with pytest.raises(AssertionError):
        await observer.assert_live_matches([entry()])


@pytest.mark.asyncio
async def test_live_evidence_requires_a_real_live_frame():
    observer = WalletTransfersSocket(Socket([]), CHANNEL)
    observer.snapshot = [entry()]
    with pytest.raises(AssertionError, match="closed"):
        await observer.assert_live_matches([entry()])


@pytest.mark.asyncio
async def test_live_evidence_accepts_batched_entries():
    rows = [entry(), entry(5, "0.2", "9.2")]
    frame = {"type": "channel_data", "channel": CHANNEL, "data": [row.to_dict() for row in rows]}
    observer = WalletTransfersSocket(Socket([json.dumps(frame)]), CHANNEL)
    await observer.assert_live_matches(rows)


@pytest.mark.asyncio
async def test_transaction_lookup_excludes_other_accounts_and_transactions():
    other_account = entry().model_copy(update={"account_id": 456})
    other_tx = entry().model_copy(update={"transaction_hash": "0x" + "cd" * 32})
    tester = Mock(
        spec=ReyaTester,
        account_id=123,
        client=SimpleNamespace(
            get_transfers=AsyncMock(return_value=SimpleNamespace(data=[other_account, other_tx, entry()]))
        ),
    )
    assert await wait_for_transaction_transfers(tester, TX, 1) == [entry()]


@pytest.mark.parametrize(
    "url", ["https://api.reya.xyz/v2", "http://example.com:3000/v2", "http://user@localhost:3000/v2"]
)
def test_localnet_endpoint_guard_rejects_remote_or_credentialed_urls(monkeypatch, url):
    monkeypatch.setenv("CHAIN_ID", "31337")
    monkeypatch.setenv("REYA_API_URL", url)
    with pytest.raises(RuntimeError):
        localnet_url("REYA_API_URL", "http")
