"""Offline tests for the strategy examples: sizing, the crossing rule, the feed and the order payloads."""

from __future__ import annotations

from types import SimpleNamespace

import asyncio
import logging
from decimal import Decimal
from importlib import import_module

import pytest
from eth_account import Account

from sdk.async_api.market_summary_update_payload import MarketSummaryUpdatePayload
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters, TriggerOrderParameters
from sdk.reya_websocket.config import WebSocketConfig

pytestmark = pytest.mark.offline

FEED = import_module("examples.strategies.mark_feed")
ENTRY = import_module("examples.strategies.price_cross_entry")

NOW_MS = 1_700_000_000_000
LEVEL = Decimal("2492")


class _ScriptedFeed:
    """Hands out scripted marks, then nothing, yielding to the loop like the real feed."""

    def __init__(self, marks: list) -> None:
        self._marks = list(marks)

    async def next_mark(self, timeout: float = 1.0):  # pylint: disable=unused-argument
        await asyncio.sleep(0)
        return self._marks.pop(0) if self._marks else None


def _marks(*prices_and_ages: tuple[str, int]) -> list:
    return [FEED.Mark(Decimal(price), NOW_MS - age) for price, age in prices_and_ages]


async def _first_crossing(monkeypatch, marks: list, direction: str = "down"):
    monkeypatch.setattr(ENTRY, "now_ms", lambda: NOW_MS)
    try:
        return await asyncio.wait_for(ENTRY.wait_for_crossing(_ScriptedFeed(marks), LEVEL, direction), timeout=0.2)
    except asyncio.TimeoutError:
        return None


def test_dollar_amounts_round_down_to_the_quantity_step():
    # $30 of SOL at 108.15 in 0.01 steps is 0.27 (~$29.20); the next step is 0.28 (~$30.28).
    qty = ENTRY.round_down(Decimal(30) / Decimal("108.15"), Decimal("0.01"))
    assert qty == Decimal("0.27")
    assert ENTRY.wire(qty * Decimal("108.15")) == "29.2005"
    assert ENTRY.wire((qty + Decimal("0.01")) * Decimal("108.15")) == "30.282"


def test_ioc_limits_round_toward_the_mark():
    tick = Decimal("0.01")
    mark = Decimal("2491.73")
    bound = Decimal(25) / Decimal(10_000)
    assert ENTRY.round_down(mark * (1 + bound), tick) == Decimal("2497.95")  # buy: never above the bound
    assert ENTRY.round_up(mark * (1 - bound), tick) == Decimal("2485.51")  # sell: never below the bound


def test_a_stop_must_sit_on_the_losing_side_of_the_price():
    # Checked against the level at start and against the crossing mark, which can overshoot it.
    assert ENTRY.stop_not_protective(True, Decimal("2480"), Decimal("2479.5"))  # buy: mark already below the stop
    assert not ENTRY.stop_not_protective(True, Decimal("2480"), Decimal("2491"))
    assert ENTRY.stop_not_protective(False, Decimal("2560"), Decimal("2561"))  # sell: mark already above the stop
    assert not ENTRY.stop_not_protective(False, Decimal("2560"), Decimal("2491"))


def test_client_order_ids_are_non_zero_uint64():
    for _ in range(100):
        value = ENTRY.new_client_order_id()
        assert 0 < value < 2**64


async def test_fires_when_the_mark_crosses_down(monkeypatch):
    hit = await _first_crossing(monkeypatch, _marks(("2495", 0), ("2493", 0), ("2491.5", 0)))
    assert hit is not None and hit.price == Decimal("2491.5")


async def test_touching_the_level_counts(monkeypatch):
    hit = await _first_crossing(monkeypatch, _marks(("2493", 0), ("2492", 0)))
    assert hit is not None and hit.price == LEVEL


async def test_crosses_up(monkeypatch):
    hit = await _first_crossing(monkeypatch, _marks(("2490", 0), ("2492.5", 0)), direction="up")
    assert hit is not None and hit.price == Decimal("2492.5")


async def test_a_level_already_passed_waits_for_the_re_arm_band(monkeypatch):
    assert await _first_crossing(monkeypatch, _marks(("2490", 0), ("2489", 0), ("2491", 0))) is None
    # 2505 is more than 50 bps above 2492, so the next cross fires.
    hit = await _first_crossing(monkeypatch, _marks(("2490", 0), ("2505", 0), ("2491", 0)))
    assert hit is not None and hit.price == Decimal("2491")


async def test_a_crossing_during_a_stale_gap_never_fires_late(monkeypatch):
    assert await _first_crossing(monkeypatch, _marks(("2495", 0), ("2491", 20_000), ("2490", 0))) is None


async def test_a_crossing_across_a_reconnect_never_fires_late(monkeypatch):
    # Both marks are fresh, but the feed reconnected between them: the cross may have happened in the gap.
    above, below = _marks(("2495", 0), ("2491", 0))
    assert await _first_crossing(monkeypatch, [above, FEED.Mark(below.price, below.at_ms, connection=1)]) is None
    # After the reconnect the run re-arms past the band as usual, then fires on a real cross.
    after = [FEED.Mark(m.price, m.at_ms, connection=1) for m in _marks(("2491", 0), ("2505", 0), ("2491", 0))]
    hit = await _first_crossing(monkeypatch, [above, *after])
    assert hit is not None and hit.price == Decimal("2491") and hit.connection == 1


def test_mark_freshness():
    assert FEED.Mark(LEVEL, NOW_MS - 10_000).is_fresh(NOW_MS)
    assert not FEED.Mark(LEVEL, NOW_MS - 10_001).is_fresh(NOW_MS)
    assert FEED.Mark(LEVEL, NOW_MS + 5_000).is_fresh(NOW_MS)
    assert not FEED.Mark(LEVEL, NOW_MS + 5_001).is_fresh(NOW_MS)


class _ImmediateLoop:
    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, fn, *args) -> None:
        fn(*args)


def _summary_update(mark_price: str, prices_updated_at: int) -> MarketSummaryUpdatePayload:
    return MarketSummaryUpdatePayload.model_validate(
        {
            "type": "channel_data",
            "timestamp": prices_updated_at,
            "channel": "/v2/perpMarket/ETHRUSDPERP/summary",
            "data": {
                "symbol": "ETHRUSDPERP",
                "updatedAt": prices_updated_at,
                "oiQty": "1",
                "fundingRate": "0",
                "longFundingValue": "0",
                "shortFundingValue": "0",
                "volume24h": "0",
                "markPrice": mark_price,
                "pricesUpdatedAt": prices_updated_at,
            },
        }
    )


async def test_the_feed_keeps_new_marks_and_drops_the_oldest_when_full(monkeypatch):
    monkeypatch.setattr(FEED, "QUEUE_SIZE", 2)
    feed = FEED.MarkFeed("ETHRUSDPERP", config=WebSocketConfig(url="wss://example.invalid/"))
    feed._loop = _ImmediateLoop()  # pylint: disable=protected-access
    for price, at in [("2491", 1), ("2491", 1), ("2492", 2), ("2493", 3), ("2490", 2)]:
        feed._on_message(None, _summary_update(price, NOW_MS + at))  # pylint: disable=protected-access
    first, second = await feed.next_mark(timeout=0.1), await feed.next_mark(timeout=0.1)
    assert [first.price, second.price] == [Decimal("2492"), Decimal("2493")]
    assert await feed.next_mark(timeout=0.01) is None


async def test_a_close_starts_a_new_connection_for_later_marks():
    feed = FEED.MarkFeed("ETHRUSDPERP", config=WebSocketConfig(url="wss://example.invalid/"))
    feed._loop = _ImmediateLoop()  # pylint: disable=protected-access
    feed._on_message(None, _summary_update("2491", NOW_MS + 1))  # pylint: disable=protected-access
    feed._on_close(None, 1012, "restart")  # pylint: disable=protected-access
    feed._on_message(None, _summary_update("2492", NOW_MS + 2))  # pylint: disable=protected-access
    first, second = await feed.next_mark(timeout=0.1), await feed.next_mark(timeout=0.1)
    assert (first.connection, second.connection) == (0, 1)


class _FakeTradingClient:
    """Answers position reads from a script and records the stop it was asked to arm."""

    def __init__(self, reads: list, stop_result, failed_reads: int = 0) -> None:
        self.config = SimpleNamespace(account_id=7)
        self._reads = list(reads)
        self._failed_reads = failed_reads
        self._stop_result = stop_result
        self.stops: list = []

    async def get_positions(self):
        if self._failed_reads:
            self._failed_reads -= 1
            raise TimeoutError("read timed out")
        return self._reads.pop(0) if self._reads else []

    async def create_trigger_order(self, params):
        self.stops.append(params)
        if isinstance(self._stop_result, Exception):
            raise self._stop_result
        return self._stop_result


def _position(side, seq: int):
    return SimpleNamespace(symbol="ETHRUSDPERP", account_id=7, side=side, last_trade_sequence_number=seq)


def _stop_params() -> TriggerOrderParameters:
    return TriggerOrderParameters(
        symbol="ETHRUSDPERP",
        is_buy=True,
        trigger_px="2560",
        trigger_type=OrderType.STOP_LOSS,
        limit_px="2585.6",
        time_in_force=TimeInForce.IOC,
        client_order_id=12345,
    )


async def _arm(monkeypatch, client) -> int:
    monkeypatch.setattr(ENTRY, "POSITION_WAIT_S", 0)
    return int(await ENTRY.arm_stop_after_fill(client, "ETHRUSDPERP", ENTRY.Side.A, 10, _stop_params()))


async def test_the_stop_arms_after_a_failed_position_read(monkeypatch):
    armed = SimpleNamespace(order_id="1", status="OPEN")
    client = _FakeTradingClient([[_position(ENTRY.Side.A, 11)]], armed, failed_reads=1)
    assert await _arm(monkeypatch, client) == 0
    assert len(client.stops) == 1


async def test_a_fill_that_never_shows_reports_unprotected(monkeypatch, caplog):
    client = _FakeTradingClient([[_position(ENTRY.Side.A, 10)]] * ENTRY.POSITION_WAIT_READS, None)
    with caplog.at_level(logging.ERROR, logger="price_cross_entry"):
        assert await _arm(monkeypatch, client) == 1
    assert not client.stops and "POSITION UNPROTECTED" in caplog.text


async def test_a_stop_request_with_an_unclear_outcome_is_reported_not_resent(monkeypatch, caplog):
    client = _FakeTradingClient([[_position(ENTRY.Side.A, 11)]], TimeoutError("connection dropped"))
    with caplog.at_level(logging.ERROR, logger="price_cross_entry"):
        assert await _arm(monkeypatch, client) == 1
    assert len(client.stops) == 1
    assert "POSITION MAY BE UNPROTECTED" in caplog.text and "12345" in caplog.text


def test_entry_and_stop_payloads_build_with_the_sdk():
    throwaway = Account.create()  # in memory only; nothing is sent
    client = ReyaTradingClient(
        TradingConfig(
            api_url="https://api-devnet.reya-cronos.network/v2",
            chain_id=89346162,
            owner_wallet_address=throwaway.address,
            private_key=throwaway.key.hex(),
            account_id=1,
            orders_gateway_address="0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D",
            dex_id_override=1,
        )
    )
    client._symbol_to_market_id = {"ETHRUSDPERP": 1}  # pylint: disable=protected-access
    client._initialized = True  # pylint: disable=protected-access

    entry, _ = client.build_create_limit_order_payload(
        LimitOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=False,
            limit_px="2485.51",
            qty="0.012",
            time_in_force=TimeInForce.IOC,
            reduce_only=False,
            client_order_id=ENTRY.new_client_order_id(),
        )
    )
    assert entry["orderType"] == "LIMIT" and entry["timeInForce"] == "IOC" and entry["reduceOnly"] is False

    stop, _ = client.build_create_trigger_order_payload(
        TriggerOrderParameters(
            symbol="ETHRUSDPERP",
            is_buy=True,
            trigger_px="2560",
            trigger_type=OrderType.STOP_LOSS,
            limit_px="2585.6",
            time_in_force=TimeInForce.IOC,
            client_order_id=ENTRY.new_client_order_id(),
        )
    )
    assert stop["orderType"] == "STOP_LOSS" and "qty" not in stop and "reduceOnly" not in stop
