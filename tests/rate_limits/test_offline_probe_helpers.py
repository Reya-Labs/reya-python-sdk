"""Offline: the suite's own live-probe helpers, pinned without a deployment.

Both halves below only ever execute on a live run, inside a single probe, where
a silent defect reads as a green test rather than a failure:

* **count-cap spreading.** The §4.2 count cap has two granularities that answer
  with the SAME error code, so reaching the account total is not enough: the
  probe market must also still be below its own per-market cap, or the reject
  belongs to either rule and names neither. The spread arithmetic that arranges
  that is exactly the kind whose off-by-one still produces an
  ``OPEN_ORDER_COUNT_EXCEEDED_ERROR`` and still passes.
* **the raw-WebSocket flood probe.** A probe that stopped reading close frames,
  or fabricated a code when it read none, would turn a missing 4029 shed into a
  passing test — the one failure mode the read-side module exists to catch.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import json
import struct
from decimal import Decimal

import pytest
from websocket import (  # type: ignore[attr-defined]  # pylint: disable=no-name-in-module
    ABNF,
    WebSocket,
    WebSocketConnectionClosedException,
    WebSocketTimeoutException,
)

from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_ws_exec import WS_CLOSE_MSG_RATE_EXCEEDED
from tests.rate_limits.rl_actions import RlMarket, count_cap_markets, place_to_account_count_cap
from tests.rate_limits.rl_config import RateLimitSuiteConfig, StandardTierLimits, SuiteTiming
from tests.rate_limits.rl_ws_probe import await_close, flood_pings, recv_json

pytestmark = [pytest.mark.offline, pytest.mark.rate_limits]


# ---- count-cap spreading ---------------------------------------------------


def _market(symbol: str) -> RlMarket:
    return RlMarket(
        symbol=symbol,
        market_id=1,
        base_asset="WETH",
        min_qty=Decimal("1"),
        qty_step=Decimal("1"),
        tick_size=Decimal("1"),
        oracle_price=Decimal("100"),
    )


def _config(*, account_cap: int, per_market_cap: int) -> RateLimitSuiteConfig:
    """A suite config with the caps under test and zero pacing (no sleeping)."""
    limits = StandardTierLimits(
        place_per_min=60,
        place_burst=5,
        cancel_per_min=120,
        cancel_burst=10,
        bulk_cancel_per_min=10,
        bulk_cancel_burst=5,
        cod_control_per_min=100,
        cod_control_burst=10,
        open_order_count_cap=account_cap,
        open_order_per_market_cap=per_market_cap,
        open_notional_cap=Decimal("5000"),
    )
    timing = SuiteTiming(
        poll_interval_s=0.0,
        eject_timeout_s=1.0,
        max_burst_attempts=10,
        bucket_recovery_s=0.0,
        place_pace_s=0.0,
        retry_after_max_s=60.0,
        retry_after_slack_s=0.0,
        settle_timeout_s=1.0,
    )
    return RateLimitSuiteConfig(
        symbol="WETHRUSD",
        second_symbol="SRUSDRUSD",
        trigger_symbol=None,
        unknown_account_id=999_999_999,
        ws_inbound_msg_burst=100,
        md_ws_inbound_msg_burst=None,
        limits=limits,
        timing=timing,
        eject_cmd=None,
        eject_only_cmd=None,
        uneject_cmd=None,
    )


class _RecordingClient:
    """Records the symbol of every create; hands back a distinct order id.

    ``open_orders`` starts from whatever book the caller seeds, which is how the
    stray-book guard is exercised.
    """

    def __init__(self, resting: list[tuple[str, str]] | None = None) -> None:
        self.symbols: list[str] = []
        self.config = SimpleNamespace(account_id=1)
        self._resting = [
            SimpleNamespace(order_id=order_id, symbol=symbol, account_id=1) for order_id, symbol in (resting or [])
        ]

    async def create_limit_order(self, params: Any) -> Any:
        self.symbols.append(params.symbol)
        return SimpleNamespace(order_id=str(len(self.symbols)))

    async def get_open_orders(self) -> list[Any]:
        return list(self._resting)


def _client(resting: list[tuple[str, str]] | None = None) -> tuple[_RecordingClient, ReyaTradingClient]:
    recording = _RecordingClient(resting)
    return recording, cast(ReyaTradingClient, recording)


def test_single_market_count_cap_is_the_tighter_granularity() -> None:
    """What one market can hold is ``min(account total, per market)``."""
    assert _config(account_cap=10, per_market_cap=5).single_market_count_cap == 5
    assert _config(account_cap=10, per_market_cap=10).single_market_count_cap == 10
    # A per-market cap above the account total cannot fire; the total still bounds it.
    assert _config(account_cap=10, per_market_cap=50).single_market_count_cap == 10


def test_one_market_is_enough_only_when_it_can_hold_the_total_with_room_to_spare() -> None:
    """One market suffices only when its own cap is STRICTLY above the total.

    At equality the single market ends AT its per-market cap, so the probe meets
    both rules at once and the reject is unattributable — the same second market
    the strictly-lower case needs.
    """
    primary, second = _market("WETHRUSD"), _market("SRUSDRUSD")

    assert count_cap_markets(primary, second, _config(account_cap=10, per_market_cap=50)) == [primary]
    assert count_cap_markets(primary, second, _config(account_cap=10, per_market_cap=10)) == [primary, second]
    assert count_cap_markets(primary, second, _config(account_cap=10, per_market_cap=5)) == [primary, second]


def test_a_missing_second_market_skips_instead_of_asserting_the_other_rule() -> None:
    """Without a second market the reject would be the per-market cap's.

    Skipping is the only honest outcome: the test would still see
    ``OPEN_ORDER_COUNT_EXCEEDED_ERROR`` and still pass, having proven the rule
    its neighbour already covers.
    """
    with pytest.raises(pytest.skip.Exception, match="needs a second market"):
        count_cap_markets(_market("WETHRUSD"), None, _config(account_cap=10, per_market_cap=5))


async def test_the_probe_market_keeps_per_market_headroom() -> None:
    """The probe market (``markets[0]``) must end BELOW its own cap.

    This is the whole point of the spread: at 8 = 2 x 5 - 2 the non-probe market
    absorbs its full 5 and the probe takes 3, leaving 2 free slots. A greedy fill
    would seat 5 on the probe market and the account-total reject would be
    indistinguishable from the per-market one.
    """
    recording, client = _client()
    config = _config(account_cap=8, per_market_cap=5)
    probe, other = _market("WETHRUSD"), _market("SRUSDRUSD")

    placed = await place_to_account_count_cap(client, [probe, other], config)

    assert len(placed[probe.symbol]) == 3 < config.limits.open_order_per_market_cap
    assert len(placed[other.symbol]) == 5, "the non-probe market absorbs the fill first, up to its own cap"
    assert sum(len(ids) for ids in placed.values()) == config.limits.open_order_count_cap
    assert recording.symbols == ["WETHRUSD"] * 3 + ["SRUSDRUSD"] * 5
    assert len(set(sum(placed.values(), []))) == 8, "every placement must yield a distinct order id"


async def test_the_shipped_localnet_caps_are_attributable() -> None:
    """The Localnet chart's 8 total / 5 per market over two spot markets.

    Pinned because the numbers are the contract: at the old 10/5 the capacity
    equalled the account total and every market ended at its own cap, so these
    probes skipped themselves on the deployment they were written for.
    """
    _, client = _client()
    config = _config(account_cap=8, per_market_cap=5)

    markets = count_cap_markets(_market("WETHRUSD"), _market("SRUSDRUSD"), config)
    placed = await place_to_account_count_cap(client, markets, config)

    assert len(placed[markets[0].symbol]) < config.limits.open_order_per_market_cap


async def test_no_market_is_filled_beyond_its_own_cap() -> None:
    """Spilling across markets never exceeds a per-market cap or the total."""
    recording, client = _client()
    config = _config(account_cap=6, per_market_cap=5)

    placed = await place_to_account_count_cap(client, [_market("A"), _market("B"), _market("C")], config)

    assert all(len(ids) <= config.limits.open_order_per_market_cap for ids in placed.values())
    assert len(placed["A"]) < config.limits.open_order_per_market_cap, "the probe market keeps headroom"
    assert len(recording.symbols) == 6, "no order may be placed beyond the account cap"


async def test_a_book_on_an_unrelated_market_fails_loudly_before_the_fill() -> None:
    """A stray book eats account-cap slots, so the fill would land short.

    Short means the create the test expects to be REJECTED is admitted instead,
    and the failure surfaces as "openOrders never reached N" long after the
    cause. The guard names the strays instead. Orders on the markets being
    filled are not strays — the caller flattens those first.
    """
    _, client = _client(resting=[("77", "OTHERRUSD")])
    with pytest.raises(pytest.fail.Exception, match="outside"):
        await place_to_account_count_cap(client, [_market("WETHRUSD")], _config(account_cap=4, per_market_cap=5))


async def test_too_little_market_capacity_skips_rather_than_under_filling() -> None:
    """Under-filling would leave the next create ADMITTED and the test green-but-wrong."""
    _, client = _client()
    with pytest.raises(pytest.skip.Exception, match="capacity ABOVE the account cap"):
        await place_to_account_count_cap(client, [_market("WETHRUSD")], _config(account_cap=12, per_market_cap=5))


async def test_capacity_that_only_EQUALS_the_account_cap_skips_too() -> None:
    """2 x 5 = 10 reaches the total, but only with every market at its own cap.

    The fill would succeed and the reject would arrive — from a rule the test
    cannot name. This is the shipped-config trap the Localnet chart's 8 avoids.
    """
    _, client = _client()
    with pytest.raises(pytest.skip.Exception, match="capacity ABOVE the account cap"):
        await place_to_account_count_cap(
            client,
            [_market("WETHRUSD"), _market("SRUSDRUSD")],
            _config(account_cap=10, per_market_cap=5),
        )


# ---- the raw-WebSocket flood probe -----------------------------------------

_CLOSE_REASON = "MSG_RATE_EXCEEDED retry_after_ms=1000"


class _ScriptedSocket:
    """A socket that accepts ``accepts`` sends, then replays a read script.

    Entries are ``(opcode, frame)`` tuples to return or exceptions to raise, so
    a script can interleave recv timeouts with frames the way an idle socket
    does.

    ``write_error`` is what a socket the server is tearing down does to the
    answering writes the read side owes (the close echo): the flood's whole
    point is that those writes fail, and they must never cost the probe a frame
    it already holds.
    """

    def __init__(self, script: list[Any], accepts: int = 10_000, write_error: BaseException | None = None) -> None:
        self._script = list(script)
        self._accepts = accepts
        self._write_error = write_error
        self.sent = 0
        self.echoed_close = 0

    def settimeout(self, _timeout: float) -> None:
        pass

    def send(self, _payload: str) -> None:
        if self.sent >= self._accepts:
            raise WebSocketConnectionClosedException("stub hung up")
        self.sent += 1

    def recv_frame(self) -> Any:
        opcode, frame = self.recv_data_frame()
        return SimpleNamespace(opcode=opcode, data=frame.data, fin=getattr(frame, "fin", 1))

    def recv_data_frame(self, **_kwargs: Any) -> tuple[int, Any]:
        if not self._script:
            raise WebSocketConnectionClosedException("stub drained")
        step = self._script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return cast(tuple[int, Any], step)

    def send_close(self) -> None:
        self.echoed_close += 1
        if self._write_error is not None:
            raise self._write_error


def _socket(
    script: list[Any], accepts: int = 10_000, write_error: BaseException | None = None
) -> tuple[_ScriptedSocket, WebSocket]:
    scripted = _ScriptedSocket(script, accepts=accepts, write_error=write_error)
    return scripted, cast(WebSocket, scripted)


def test_the_flood_stops_when_the_server_hangs_up_mid_send() -> None:
    """The shed can land mid-flood; the probe reports what the transport took.

    Raising out of the flood would skip the close read entirely and report the
    shed as a transport error — the 4029 would never be seen.
    """
    scripted, ws = _socket([], accepts=7)
    assert flood_pings(ws, 50) == 7
    assert scripted.sent == 7


def test_the_close_frame_is_read_at_the_frame_layer() -> None:
    """The 4029 code and its reason survive the read, past an idle recv timeout."""
    body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + _CLOSE_REASON.encode("utf-8")
    _, ws = _socket(
        [
            WebSocketTimeoutException("idle"),
            (ABNF.OPCODE_CLOSE, SimpleNamespace(data=body)),
        ]
    )
    assert await_close(ws, timeout_s=5.0) == (WS_CLOSE_MSG_RATE_EXCEEDED, _CLOSE_REASON)


def test_the_close_code_survives_an_echo_the_dead_socket_refuses() -> None:
    """The shed's timing: the socket is gone before the probe can answer the close.

    Reading through ``recv_data_frame`` echoed the close INSIDE the read, so
    that write's ``OSError`` came back as a transport death and the 4029 —
    already received in full — was reported as ``(None, None)``. Then the
    docstring above would be lying: no code would no longer mean no close frame.
    """
    body = struct.pack("!H", WS_CLOSE_MSG_RATE_EXCEEDED) + _CLOSE_REASON.encode("utf-8")
    scripted, ws = _socket(
        [(ABNF.OPCODE_CLOSE, SimpleNamespace(data=body))],
        write_error=OSError("Broken pipe"),
    )

    assert await_close(ws, timeout_s=5.0) == (WS_CLOSE_MSG_RATE_EXCEEDED, _CLOSE_REASON)
    assert scripted.echoed_close == 1, "the echo is still attempted — just after the code is read off the frame"


def test_a_death_without_a_close_frame_reports_no_code() -> None:
    """No fabricated default: an absent code must fail the caller's assertion.

    ``(None, None)`` is what makes "observed close code None" a legible failure
    instead of a comparison against something the server never said.
    """
    _, ws = _socket([])
    assert await_close(ws, timeout_s=1.0) == (None, None)


def test_a_json_reply_is_read_past_non_text_frames() -> None:
    """The reconnect leg's read skips control/binary noise and empty frames."""
    pong = json.dumps({"type": "pong"}).encode("utf-8")
    _, ws = _socket(
        [
            (ABNF.OPCODE_PONG, SimpleNamespace(data=b"")),
            (ABNF.OPCODE_TEXT, SimpleNamespace(data=b"")),
            (ABNF.OPCODE_TEXT, SimpleNamespace(data=b"not json")),
            (ABNF.OPCODE_TEXT, SimpleNamespace(data=pong)),
        ]
    )
    assert recv_json(ws, timeout_s=5.0) == {"type": "pong"}


def test_a_socket_that_answers_nothing_reads_as_none() -> None:
    """The reconnect assertion must fail on silence, not hang or invent a frame."""
    _, ws = _socket([])
    assert recv_json(ws, timeout_s=1.0) is None
