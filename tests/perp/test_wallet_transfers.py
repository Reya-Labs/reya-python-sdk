#!/usr/bin/env python3
"""Tests for the wallet transfer history (account ledger) endpoint.

``GET /v2/wallet/{address}/transfers`` returns one entry per account side of
every on-chain transfer leg, newest first, paged by an opaque cursor. These
tests are read-only and environment-agnostic: they assert the contract of
whatever history the shared test wallet already has. The end-to-end fee-leg
assertions for a controlled fill live in ``tests/engine/test_order_history.py``.

Deployments that predate the endpoint answer 404; the tests skip there so the
suite stays useful across environments.
"""

import re
from decimal import Decimal

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.transfer import Transfer
from sdk.open_api.models.transfer_list import TransferList
from sdk.open_api.models.transfer_type import TransferType
from tests.helpers import ReyaTester
from tests.helpers.reya_tester import logger

_TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


async def _transfers(tester: ReyaTester, **params) -> TransferList:
    try:
        return await tester.client.get_transfers(**params)
    except ApiException as error:
        if error.status == 404:
            pytest.skip("wallet transfers endpoint is not deployed on this environment")
        raise


def _assert_entry_shape(entry: Transfer, account_ids: set[int]) -> None:
    assert entry.account_id in account_ids, "entry must belong to one of the wallet's accounts"
    assert entry.type != TransferType.UNKNOWN, "SDK predates a label the server now emits"
    assert entry.asset, "entry must name its asset"
    amount = Decimal(entry.amount)
    Decimal(entry.net_deposits_after)  # parses as a decimal string
    assert entry.timestamp > 0
    assert _TX_HASH.match(entry.transaction_hash), entry.transaction_hash
    if entry.counterparty_account_id is not None:
        assert entry.counterparty_account_id > 0, "account 0 never appears as a counterparty"
    if entry.type in (TransferType.DEPOSIT, TransferType.WITHDRAWAL):
        assert entry.counterparty_account_id is None, "deposits and withdrawals have no counterparty"
    if entry.type == TransferType.DEPOSIT:
        assert amount > 0, "a deposit credits the account"
    if entry.type == TransferType.WITHDRAWAL:
        assert amount < 0, "a withdrawal debits the account"
    if entry.type == TransferType.PERP_TAKER_FEE:
        assert amount < 0, "the taker pays the gross fee"
    if entry.fill_id is not None:
        assert entry.fill_id != "0", "a zero nonce is never a fill id"


@pytest.mark.asyncio
async def test_get_wallet_transfers_contract(reya_tester: ReyaTester):
    """Entries are well-formed, newest first, and hide zero amounts."""
    assert reya_tester.owner_wallet_address is not None, "Owner wallet address required"
    accounts = await reya_tester.client.get_accounts()
    account_ids = {account.account_id for account in accounts}

    page = await _transfers(reya_tester)

    assert page.meta.limit == 100, "the default page is the maximum page"
    assert page.meta.count == len(page.data)
    assert len(page.data) <= 100
    sequence_numbers = [entry.sequence_number for entry in page.data]
    assert sequence_numbers == sorted(sequence_numbers, reverse=True), "newest first by sequence number"
    assert len(set(sequence_numbers)) == len(sequence_numbers), "sequence numbers are unique"
    for entry in page.data:
        _assert_entry_shape(entry, account_ids)
        assert Decimal(entry.amount) != 0, "zero amounts must not be exposed"

    logger.info(f"✅ Wallet transfers contract test completed - {len(page.data)} entries")


@pytest.mark.asyncio
async def test_get_wallet_transfers_cursor_pagination(reya_tester: ReyaTester):
    """Walking pages with the cursor visits entries exactly once, in order."""
    first = await _transfers(reya_tester, limit=1)
    if first.meta.count == 0:
        pytest.skip("the test wallet has no transfer history to page through")
    assert first.meta.limit == 1
    assert len(first.data) == 1

    if first.meta.next_cursor is None:
        logger.info("✅ Wallet transfers pagination test completed - a single entry, no next page")
        return

    second = await _transfers(reya_tester, limit=1, cursor=first.meta.next_cursor)
    assert len(second.data) == 1
    assert second.data[0].sequence_number < first.data[0].sequence_number, "the next page is strictly older"

    # A page may end between the two sides of one leg (same leg, different
    # side): the cursor must still not repeat or skip either entry. A larger
    # page is read after the two singles, so entries that settled meanwhile
    # sit above the pair rather than displacing it.
    full = await _transfers(reya_tester, limit=10)
    numbers = [entry.sequence_number for entry in full.data]
    if first.data[0].sequence_number not in numbers[:-1]:
        pytest.skip("the wallet settled a page of new entries mid-test; nothing to compare")
    position = numbers.index(first.data[0].sequence_number)
    assert numbers[position : position + 2] == [
        first.data[0].sequence_number,
        second.data[0].sequence_number,
    ], "the two single-entry pages must be consecutive in one larger page"

    logger.info("✅ Wallet transfers pagination test completed")


@pytest.mark.asyncio
async def test_get_wallet_transfers_type_filter(reya_tester: ReyaTester):
    """The type filter returns only the requested labels; UNKNOWN and a foreign cursor are rejected."""
    filtered = await _transfers(reya_tester, types=[TransferType.DEPOSIT, TransferType.WITHDRAWAL])
    for entry in filtered.data:
        assert entry.type in (TransferType.DEPOSIT, TransferType.WITHDRAWAL)

    # The SDK's open-enum sentinel is not a label the server accepts.
    with pytest.raises(ValueError):
        await reya_tester.client.get_transfers(types=[TransferType.UNKNOWN])

    with pytest.raises(ApiException) as rejected:
        await reya_tester.client.wallet.get_wallet_transfers_with_http_info(
            address=reya_tester.owner_wallet_address or "",
            cursor="not-a-cursor",
        )
    assert rejected.value.status == 400, "a cursor the server did not issue is rejected"

    logger.info("✅ Wallet transfers type filter test completed")
