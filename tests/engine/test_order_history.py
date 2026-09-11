"""
Order-history end-to-end coverage for the matching-engine orderbook path.

This live devnet1 test expects `/v2/wallet/{address}/orderHistory` to be
deployed on the perpOB API branch.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from contextlib import AsyncExitStack
from decimal import Decimal

import pytest
import pytest_asyncio

from sdk.async_api.perp_execution import PerpExecution as AsyncPerpExecution
from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.order import Order
from sdk.open_api.models.order_history_list import OrderHistoryList
from sdk.open_api.models.order_status import OrderStatus
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.perp_execution import PerpExecution
from sdk.open_api.models.side import Side
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.open_api.models.transfer import Transfer
from sdk.open_api.models.transfer_type import TransferType
from sdk.reya_rest_api.models import LimitOrderParameters
from tests.helpers import ReyaTester
from tests.helpers.localnet_fee_v3 import RUSD_SCALE, WAD, configured_localnet_fee_v3, wait_for_indexed_fee_v3_row
from tests.helpers.market_config import PerpTestConfig
from tests.helpers.order_lifecycle import assert_px_qty, wait_for_taker_perp_execution
from tests.helpers.reya_tester import logger
from tests.helpers.wallet_transfers import (
    assert_net_deposits,
    assert_running_net_deposits,
    assert_transfer_snapshot,
    net_deposits,
    wait_for_transaction_transfers,
    wallet_transfers_socket,
)

_REQUIRED_ORDER_HISTORY_E2E_ENV = (
    "PERP_ACCOUNT_ID_1",
    "PERP_PRIVATE_KEY_1",
    "PERP_WALLET_ADDRESS_1",
    "PERP_ACCOUNT_ID_2",
    "PERP_PRIVATE_KEY_2",
    "PERP_WALLET_ADDRESS_2",
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.perp,
    pytest.mark.skipif(
        not all(os.environ.get(name) for name in _REQUIRED_ORDER_HISTORY_E2E_ENV),
        reason="orderHistory E2E requires configured live perp maker/taker accounts",
    ),
]


_FEE_V3_COMPONENT_FIELDS = ("protocol_fee_credit", "referrer_fee_credit", "taker_rebate_credit", "pool_fee_credit")


def _fee_v3_breakdown(execution: PerpExecution | AsyncPerpExecution) -> dict[str, Decimal]:
    """The four public Fee v3 components as Decimals (PRO-853). A Fee v3 fill
    exposes every one of them; the API never synthesizes a partial set."""
    breakdown: dict[str, Decimal] = {}
    for field in _FEE_V3_COMPONENT_FIELDS:
        value = getattr(execution, field)
        assert value is not None, f"Fee v3 execution must expose {field}"
        breakdown[field] = Decimal(value)
    return breakdown


async def _wait_for_ws_perp_execution(
    tester: ReyaTester,
    sequence_number: int,
    timeout_s: float = 15.0,
) -> AsyncPerpExecution:
    """The wallet `perpExecutions` WS event for one settled fill, by sequence number."""
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        match = tester.ws.perp_executions.find_last(lambda e: e.sequence_number == sequence_number)
        if match is not None:
            return match
        await asyncio.sleep(0.2)
    raise AssertionError(f"No WS perp execution with sequenceNumber={sequence_number} within {timeout_s}s")


_PERP_FEE_LEG_TYPES = [
    TransferType.PERP_TAKER_FEE,
    TransferType.PERP_REFERRER_REBATE,
    TransferType.PERP_TAKER_REBATE,
    TransferType.PERP_POOL_REBATE,
]


async def _wait_for_fill_ledger_entries(
    tester: ReyaTester,
    execution: PerpExecution,
    *,
    expected: int,
    timeout_s: float = 15.0,
) -> list[Transfer] | None:
    """Match Localnet legs by the indexed transaction, including pre-#752 chains.

    Other environments retain the existing optional-endpoint behavior. Localnet
    requires the endpoint and every expected leg, even before fill links ship.
    """
    if tester.chain_id == 31337:
        assert execution.fill_id is not None
        indexed = await asyncio.to_thread(wait_for_indexed_fee_v3_row, execution.fill_id)
        return await wait_for_transaction_transfers(tester, indexed.transaction_hash, expected)

    deadline = asyncio.get_running_loop().time() + timeout_s
    entries: list[Transfer] = []
    while True:
        try:
            page = await tester.client.get_transfers(types=_PERP_FEE_LEG_TYPES)
        except ApiException as error:
            if error.status == 404:
                return None
            raise
        # Same block, same account, same market. Where the link is live the
        # linked entries win, so another fill of this account in the same
        # second (a close in the next call, a concurrent session on the
        # shared devnet accounts) cannot pad the count.
        candidates = [
            entry
            for entry in page.data
            if entry.timestamp == execution.timestamp
            and entry.account_id == tester.account_id
            and entry.symbol == execution.symbol
        ]
        entries = [entry for entry in candidates if entry.fill_id == execution.fill_id] or [
            entry for entry in candidates if entry.fill_id is None
        ]
        if len(entries) >= expected or asyncio.get_running_loop().time() >= deadline:
            return entries
        await asyncio.sleep(0.25)


def _assert_fill_link(entries: list[Transfer], fill_id: str) -> bool:
    """True when the entries carry the on-chain fill link; consistent either way."""
    assert entries, "no ledger entries for this fill"
    linked = {entry.fill_id for entry in entries}
    assert linked in ({None}, {fill_id}), f"fill link must be all-or-nothing per fill, got {linked}"
    return linked == {fill_id}


def _ledger_amount(entries: list[Transfer], entry_type: TransferType) -> Decimal | None:
    matching = [entry for entry in entries if entry.type == entry_type]
    assert len(matching) <= 1, f"one {entry_type.value} entry per fill, got {len(matching)}"
    return Decimal(matching[0].amount) if matching else None


async def _wait_for_history_order(
    tester: ReyaTester,
    order_id: str,
    predicate: Callable[[Order], bool],
    timeout_s: float = 15.0,
) -> Order:
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_history: OrderHistoryList | None = None

    while asyncio.get_running_loop().time() < deadline:
        last_history = await tester.client.get_order_history()
        for order in last_history.data:
            if order.order_id == order_id and predicate(order):
                return order
        await asyncio.sleep(0.5)

    seen_ids = [order.order_id for order in (last_history.data if last_history else [])[:10]]
    raise AssertionError(f"order {order_id} not found in orderHistory; first seen ids: {seen_ids}")


async def _assert_time_window_refetch_contains_order(tester: ReyaTester, expected_order: Order) -> None:
    assert expected_order.sequence_number is not None

    history = await tester.client.get_order_history(
        start_time=expected_order.last_update_at,
        end_time=expected_order.last_update_at,
    )

    sequence_numbers = {order.sequence_number for order in history.data}
    assert expected_order.sequence_number in sequence_numbers
    assert history.meta.count == len(history.data)
    assert history.meta.start_time == expected_order.last_update_at
    assert history.meta.end_time == expected_order.last_update_at
    assert all(order.last_update_at == expected_order.last_update_at for order in history.data)


def _assert_filled_order_projection(
    order: Order,
    *,
    account_id: int,
    symbol: str,
    side: Side,
    limit_px: str,
    qty: str,
) -> None:
    assert order.exchange_id >= 0
    assert order.account_id == account_id
    assert order.symbol == symbol
    assert order.side == side
    assert_px_qty(order, limit_px, qty)
    assert order.order_type == OrderType.LIMIT
    assert order.time_in_force in (TimeInForce.GTC, TimeInForce.IOC)
    assert order.status == OrderStatus.FILLED
    assert order.created_at > 0
    assert order.last_update_at >= order.created_at
    assert order.sequence_number is not None
    assert order.sequence_number >= 0
    assert order.first_fill_id is not None
    assert int(order.first_fill_id) > 0
    assert order.fill_count is not None
    assert order.fill_count >= 1


@pytest_asyncio.fixture
async def localnet_transfer_observers(
    perp_maker_tester: ReyaTester, perp_taker_tester: ReyaTester, with_referrer: bool
):
    """Subscribe before placing the fill; always close every observer on failure."""
    if perp_taker_tester.chain_id != 31337:
        yield None
        return
    async with AsyncExitStack() as stack:
        taker_ws = await stack.enter_async_context(wallet_transfers_socket(perp_taker_tester))
        pool_ws = await stack.enter_async_context(wallet_transfers_socket(perp_maker_tester))
        referrer = None
        referrer_ws = None
        if with_referrer:
            assert all(
                os.environ.get(f"SPOT_{field}_1") for field in ("ACCOUNT_ID", "PRIVATE_KEY", "WALLET_ADDRESS")
            ), "Localnet referrer requires spot account 1"
            referrer = ReyaTester(spot_account_number=1)
            stack.push_async_callback(referrer.close)
            await referrer.client.start()
            assert referrer.account_id >= 10**10
            assert referrer.account_id not in (perp_maker_tester.account_id, perp_taker_tester.account_id)
            referrer_ws = await stack.enter_async_context(wallet_transfers_socket(referrer))
        yield taker_ws, pool_ws, referrer, referrer_ws


@pytest.mark.parametrize(
    "with_referrer",
    [
        pytest.param(False, id="unreferred"),
        pytest.param(
            True,
            id="referred",
            marks=pytest.mark.skipif(
                os.environ.get("CHAIN_ID") != "31337", reason="referral configuration is Localnet-only"
            ),
        ),
    ],
)
@pytest.mark.asyncio
async def test_perp_order_history_records_maker_and_taker_fill_e2e(
    perp_market_config: PerpTestConfig,
    perp_maker_tester: ReyaTester,
    perp_taker_tester: ReyaTester,
    localnet_transfer_observers,
    with_referrer: bool,
) -> None:
    """Crossing maker/taker GTC fill should appear in wallet orderHistory."""
    market_config = perp_market_config
    maker = perp_maker_tester
    taker = perp_taker_tester

    await market_config.refresh_order_book(maker.data)
    await maker.orders.close_all(fail_if_none=False)
    await taker.orders.close_all(fail_if_none=False)

    if market_config.has_any_external_liquidity:
        assert taker.chain_id != 31337, "Localnet fee-leg evidence requires a controlled maker/taker book"
        pytest.skip("external liquidity present — orderHistory assertions require a controlled maker/taker fill")

    taker_baseline = await net_deposits(taker) if localnet_transfer_observers else None
    pool_baseline = await net_deposits(maker) if localnet_transfer_observers else None

    referrer = localnet_transfer_observers[2] if localnet_transfer_observers else None
    referrer_baseline = await net_deposits(referrer) if referrer else None

    cross_px = str(market_config.price(0.99))
    qty = market_config.min_qty
    assert taker.owner_wallet_address is not None

    with configured_localnet_fee_v3(
        taker_owner=taker.owner_wallet_address,
        pool_account_id=maker.account_id,
        referrer_owner=referrer.owner_wallet_address if referrer else None,
        referrer_account_id=referrer.account_id if referrer else None,
    ) as fee_v3_scenario:
        maker_order_id = await maker.orders.create_limit(
            LimitOrderParameters(
                symbol=market_config.symbol,
                is_buy=True,
                limit_px=cross_px,
                qty=qty,
                time_in_force=TimeInForce.GTC,
            )
        )
        assert maker_order_id is not None
        await maker.wait.for_order_creation(maker_order_id)

        taker_response = await taker.client.create_limit_order(
            LimitOrderParameters(
                symbol=market_config.symbol,
                is_buy=False,
                limit_px=cross_px,
                qty=qty,
                time_in_force=TimeInForce.GTC,
            )
        )
        taker_order_id = taker_response.order_id
        assert taker_order_id is not None

        maker_history_order = await _wait_for_history_order(
            maker,
            maker_order_id,
            lambda order: order.status == OrderStatus.FILLED and order.first_fill_id is not None,
        )
        taker_history_order = await _wait_for_history_order(
            taker,
            taker_order_id,
            lambda order: order.status == OrderStatus.FILLED and order.first_fill_id is not None,
        )

        _assert_filled_order_projection(
            maker_history_order,
            account_id=maker.account_id,
            symbol=market_config.symbol,
            side=Side.B,
            limit_px=cross_px,
            qty=qty,
        )
        _assert_filled_order_projection(
            taker_history_order,
            account_id=taker.account_id,
            symbol=market_config.symbol,
            side=Side.A,
            limit_px=cross_px,
            qty=qty,
        )

        assert maker_history_order.fill_count == 1, "maker should map to one fill"
        assert taker_history_order.fill_count == 1, "single-level taker should map to one fill"

        execution = await wait_for_taker_perp_execution(taker, taker_order_id, timeout_s=15.0)
        assert execution.fill_id == taker_history_order.first_fill_id
        assert execution.fill_id == maker_history_order.first_fill_id
        assert execution.maker_fee is None, "fee-model-v3 executions must not project the legacy makerFee field"

        # PRO-853: the public REST execution decomposes takerFee into its four
        # settlement buckets, and takerFee is exactly their sum.
        rest_breakdown = _fee_v3_breakdown(execution)
        assert sum(rest_breakdown.values()) == Decimal(execution.taker_fee)

        # The wallet WS event for the same fill must carry identical values.
        ws_execution = await _wait_for_ws_perp_execution(taker, execution.sequence_number)
        assert ws_execution.fill_id == execution.fill_id
        assert ws_execution.taker_fee == execution.taker_fee
        assert ws_execution.maker_fee is None
        assert _fee_v3_breakdown(ws_execution) == rest_breakdown
        for field in _FEE_V3_COMPONENT_FIELDS:
            assert getattr(ws_execution, field) == getattr(execution, field), field

        # PRO-852: the account ledger shows the same fill as fee legs signed
        # from each account's view, linked by fillId (design §4, §5; I1, I2).
        assert execution.fill_id is not None
        expected_taker_entries = 1 + (1 if Decimal(execution.taker_rebate_credit or 0) > 0 else 0)
        taker_ledger = await _wait_for_fill_ledger_entries(taker, execution, expected=expected_taker_entries)
        if taker_ledger is None:
            logger.info("transfers endpoint not deployed here; skipping the ledger assertions")
        elif not taker_ledger and fee_v3_scenario is None:
            # The endpoint is served but the ledger is not written here
            # (LEDGER_ENABLED off); Localnet turns it on and is strict.
            logger.info("no ledger entries for this fill; the ledger is not enabled here")
        else:
            assert len(taker_ledger) == expected_taker_entries, [entry.type for entry in taker_ledger]
            if not _assert_fill_link(taker_ledger, execution.fill_id):
                # The chain here predates reya-network#752: legs are labelled
                # but not linked to the fill.
                logger.info("ledger entries matched by block; the on-chain fill link is not live here")
            for entry in taker_ledger:
                assert entry.account_id == taker.account_id
                assert entry.asset == "RUSD"
                if entry.fill_id is not None:
                    assert entry.symbol == market_config.symbol
                assert entry.timestamp == execution.timestamp
                assert entry.counterparty_account_id is not None, "the fee collector is the counterparty"
            # The taker pays the gross fee and gets its rebate back as two entries;
            # the execution fields describe the split, the ledger the movements.
            assert _ledger_amount(taker_ledger, TransferType.PERP_TAKER_FEE) == -Decimal(execution.taker_fee)
            if expected_taker_entries == 2:
                assert _ledger_amount(taker_ledger, TransferType.PERP_TAKER_REBATE) == Decimal(
                    execution.taker_rebate_credit or 0
                )
            assert _ledger_amount(taker_ledger, TransferType.PERP_REFERRER_REBATE) is None
            assert _ledger_amount(taker_ledger, TransferType.PERP_POOL_REBATE) is None

        if fee_v3_scenario is not None:
            assert execution.fill_id is not None
            indexed = await asyncio.to_thread(wait_for_indexed_fee_v3_row, execution.fill_id)
            assert indexed.account_id == taker.account_id
            assert indexed.counterparty_account_id == maker.account_id
            assert indexed.fee > 0

            expected_taker_rebate = indexed.fee * fee_v3_scenario.taker_rebate_rate // WAD
            remaining_after_taker = indexed.fee - expected_taker_rebate
            expected_referrer_credit = remaining_after_taker * fee_v3_scenario.referrer_rebate_rate // WAD
            remaining_after_referrer = remaining_after_taker - expected_referrer_credit
            expected_pool_credit = remaining_after_referrer * fee_v3_scenario.pool_rebate_rate // WAD
            expected_protocol_credit = remaining_after_referrer - expected_pool_credit
            assert indexed.referrer_fee_credit == expected_referrer_credit
            if with_referrer:
                assert expected_referrer_credit > 0

            assert indexed.taker_rebate_credit == expected_taker_rebate
            assert indexed.pool_fee_credit == expected_pool_credit
            assert indexed.protocol_fee_credit == expected_protocol_credit
            assert indexed.fee == (
                indexed.protocol_fee_credit
                + indexed.referrer_fee_credit
                + indexed.taker_rebate_credit
                + indexed.pool_fee_credit
            )
            assert Decimal(execution.taker_fee) == Decimal(indexed.fee) / Decimal(RUSD_SCALE)
            # The public breakdown mirrors the persisted buckets exactly.
            assert rest_breakdown == {
                "protocol_fee_credit": Decimal(indexed.protocol_fee_credit) / Decimal(RUSD_SCALE),
                "referrer_fee_credit": Decimal(indexed.referrer_fee_credit) / Decimal(RUSD_SCALE),
                "taker_rebate_credit": Decimal(indexed.taker_rebate_credit) / Decimal(RUSD_SCALE),
                "pool_fee_credit": Decimal(indexed.pool_fee_credit) / Decimal(RUSD_SCALE),
            }
            assert indexed.exchange_fee_credit is None
            assert indexed.maker_fee_credit is None
            assert indexed.maker_fee_debit is None
            assert indexed.transaction_hash.startswith("0x")

            # Localnet runs the whole stack from source with the ledger on, so
            # the legs must be present and exact: leg 0 and leg 2 on the taker,
            # leg 3 on the pool account, which this scenario points at the
            # maker. The referred variant also credits leg 1 to a spot account.
            assert taker_ledger, "Localnet must persist the fill's ledger legs"
            assert _ledger_amount(taker_ledger, TransferType.PERP_TAKER_FEE) == -Decimal(indexed.fee) / Decimal(
                RUSD_SCALE
            )
            assert _ledger_amount(taker_ledger, TransferType.PERP_TAKER_REBATE) == Decimal(
                indexed.taker_rebate_credit
            ) / Decimal(RUSD_SCALE)
            assert all(entry.transaction_hash == indexed.transaction_hash for entry in taker_ledger)

            pool_ledger = await _wait_for_fill_ledger_entries(maker, execution, expected=1)
            assert pool_ledger, "the pool rebate leg must reach the pool account's wallet"
            _assert_fill_link(pool_ledger, execution.fill_id)
            assert [entry.type for entry in pool_ledger] == [TransferType.PERP_POOL_REBATE]
            assert pool_ledger[0].account_id == maker.account_id
            assert Decimal(pool_ledger[0].amount) == Decimal(indexed.pool_fee_credit) / Decimal(RUSD_SCALE)
            assert pool_ledger[0].counterparty_account_id == taker_ledger[0].counterparty_account_id

            assert localnet_transfer_observers is not None
            assert taker_baseline is not None and pool_baseline is not None
            taker_ws, pool_ws, _, referrer_ws = localnet_transfer_observers
            await taker_ws.assert_live_matches(taker_ledger)
            await pool_ws.assert_live_matches(pool_ledger)
            await assert_net_deposits(taker, assert_running_net_deposits(taker_ledger, taker_baseline))
            await assert_net_deposits(maker, assert_running_net_deposits(pool_ledger, pool_baseline))
            await assert_transfer_snapshot(taker, taker_ledger)
            await assert_transfer_snapshot(maker, pool_ledger)

            if with_referrer:
                assert referrer is not None and referrer_ws is not None and referrer_baseline is not None
                referrer_ledger = await _wait_for_fill_ledger_entries(referrer, execution, expected=1)
                assert referrer_ledger, "the referrer rebate must reach the referrer's spot wallet"
                _assert_fill_link(referrer_ledger, execution.fill_id)
                entry = referrer_ledger[0]
                assert entry.type == TransferType.PERP_REFERRER_REBATE
                assert entry.account_id == referrer.account_id
                assert entry.asset == "RUSD"
                assert entry.timestamp == execution.timestamp
                assert entry.counterparty_account_id == taker_ledger[0].counterparty_account_id
                assert entry.transaction_hash == indexed.transaction_hash
                assert Decimal(entry.amount) == Decimal(expected_referrer_credit) / Decimal(RUSD_SCALE)
                if entry.fill_id is not None:
                    assert entry.symbol == market_config.symbol
                await referrer_ws.assert_live_matches(referrer_ledger)
                await assert_net_deposits(referrer, assert_running_net_deposits(referrer_ledger, referrer_baseline))
                await assert_transfer_snapshot(referrer, referrer_ledger)

    await _assert_time_window_refetch_contains_order(maker, maker_history_order)
    await _assert_time_window_refetch_contains_order(taker, taker_history_order)
