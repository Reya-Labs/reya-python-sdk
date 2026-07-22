"""T3 — matching-engine resync (1012) close handling.

With live depth + orderChanges subscriptions, restarts the matching engine the
way the localnet infra does (``kubectl rollout restart`` against the k3d stack,
following the existing ``tests/helpers/localnet_fee_v3.py`` kubectl pattern) and
asserts the resync contract:

* the market-data feed signals a reset — via a **1012** close on the stateful
  channels ("feed resync … (reset)") and/or the API-layer **FEED_RESET** path
  documented on ``Order.cancelReason`` (resting orders re-delivered as CANCELLED
  then re-published),
* after reconnect+resubscribe the fresh snapshots are internally consistent
  (orderChanges snapshot == REST openOrders; depth snapshot == REST depth), and
* subsequent diffs resume on the fresh connection.

Requires ``LOCALNET_KUBECONFIG`` (skip-by-default otherwise): only a localnet run
that owns the stack should bounce the ME, so this stays opt-in and never fires
against a shared/remote deployment. The test records which resync mechanism the
live stack actually used and asserts on it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess  # nosec B404
import time

import pytest

from sdk.open_api.exceptions import ApiException
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.market_config import SpotTestConfig
from tests.market_data.local_book import LocalBook, depth_sides
from tests.market_data.md_ws_recorder import MarketDataRecorder
from tests.market_data.poll import wait_until

logger = logging.getLogger("reya.integration_tests")

_ME_DEPLOYMENT = "reya-localnet-matching-engine"
_NAMESPACE = "reya-localnet"
_CONTEXT = "k3d-reya-localnet"


def _kubeconfig() -> str | None:
    return os.environ.get("LOCALNET_KUBECONFIG")


def _feed_reset_seen(recorder: MarketDataRecorder) -> bool:
    for frame in recorder.order_change_frames:
        for order in frame:
            reason = getattr(order, "cancel_reason", None)
            if reason is None:
                continue
            reason_text = reason.value if hasattr(reason, "value") else str(reason)
            if "FEED_RESET" in reason_text.upper():
                return True
    return False


def _restart_matching_engine(kubeconfig: str) -> None:
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise RuntimeError("kubectl is required to restart the matching engine")
    common = [kubectl, "--kubeconfig", kubeconfig, "--context", _CONTEXT, "-n", _NAMESPACE]
    logger.info("Restarting matching engine via kubectl rollout restart ...")
    restart = subprocess.run(  # nosec B603
        [*common, "rollout", "restart", f"deployment/{_ME_DEPLOYMENT}"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if restart.returncode != 0:
        raise RuntimeError(f"rollout restart failed: {restart.stderr.strip()}")
    status = subprocess.run(  # nosec B603
        [*common, "rollout", "status", f"deployment/{_ME_DEPLOYMENT}", "--timeout=240s"],
        check=False,
        capture_output=True,
        text=True,
        timeout=260,
    )
    if status.returncode != 0:
        raise RuntimeError(f"rollout status did not converge: {status.stderr.strip() or status.stdout.strip()}")
    logger.info("Matching engine rollout complete: %s", status.stdout.strip())


async def _wait_for_me_stable(tester: ReyaTester, config: SpotTestConfig, timeout: float = 150.0) -> bool:
    """Confirm the ME write path actually serves orders after the restart.

    ``kubectl rollout status`` returning success is not sufficient: for a short
    window after the new ME pod is Ready, the API/bun-socket are still
    re-establishing their TCP connection to it, so writes transiently return
    ``UNAVAILABLE_MATCHING_ENGINE_ERROR`` (and on this localnet the Finding-B
    snapshot-GET race can re-crash the pod once or twice). Land two probe
    create+cancel cycles a few seconds apart before relying on the ME.
    """
    deadline = time.monotonic() + timeout
    probe_px = str(int(config.get_safe_no_match_buy_price()) + 7)
    consecutive = 0
    while time.monotonic() < deadline:
        try:
            params = OrderBuilder.from_config(config).buy().price(probe_px).gtc().build()
            oid = await tester.orders.create_limit(params)
            if oid:
                await tester.client.cancel_order(order_id=oid, symbol=config.symbol, account_id=tester.account_id)
                consecutive += 1
                if consecutive >= 2:
                    logger.info("ME write path stable after restart")
                    return True
        except (ApiException, OSError, RuntimeError) as exc:
            consecutive = 0
            logger.info("ME not yet serving writes (%s); retrying", type(exc).__name__)
        await asyncio.sleep(3.0)
    return False


async def _assert_snapshot_consistent_with_rest(
    recorder: MarketDataRecorder, tester: ReyaTester, symbol: str, label: str
) -> None:
    """Fresh subscribe feed is internally consistent with REST:

    - orderChanges snapshot == REST openOrders (exact), and
    - the depth snapshot reconciled with the diffs that immediately follow it
      converges to REST ``/depth``. The snapshot is a point-in-time cursor, so a
      diff can land between it and the REST read (an order enters the depth
      stream just after the snapshot); reconciling snapshot+diffs (the same
      continuity contract T2 checks) is the robust equality.
    """

    async def _order_changes_consistent() -> bool:
        if recorder.order_changes_snapshot is None:
            return False
        snap_ids = {o.order_id for o in recorder.order_changes_snapshot_orders()}
        rest_ids = {o.order_id for o in await tester.client.get_open_orders()}
        return snap_ids == rest_ids

    async def _depth_consistent() -> bool:
        snap = recorder.depth_snapshot_copy()
        if snap is None:
            return False
        book = LocalBook.from_snapshot(snap)
        for frame in recorder.depth_updates():
            book.apply(frame)
        rest = await tester.data.market_depth(symbol)
        rest_bids, rest_asks = depth_sides(rest)
        return book.bids_sorted() == rest_bids and book.asks_sorted() == rest_asks

    assert await wait_until(
        _order_changes_consistent, timeout=20.0
    ), f"[{label}] orderChanges snapshot != REST openOrders"
    assert await wait_until(
        _depth_consistent, timeout=20.0
    ), f"[{label}] depth snapshot+diffs did not reconcile to REST depth"
    logger.info("[%s] fresh snapshots consistent with REST", label)


@pytest.mark.localnet
@pytest.mark.spot
@pytest.mark.market_data
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_resync_close(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    kubeconfig = _kubeconfig()
    if not kubeconfig or shutil.which("kubectl") is None:
        pytest.skip(
            "T3 needs LOCALNET_KUBECONFIG + kubectl to restart the matching engine; "
            "opt-in so it never bounces a shared/remote stack."
        )

    logger.info("=" * 80)
    logger.info("T3 RESYNC (1012) CLOSE HANDLING: %s", spot_config.symbol)
    logger.info("=" * 80)

    symbol = spot_config.symbol
    address = spot_tester.owner_wallet_address
    assert address is not None
    ws_url = os.environ.get("REYA_WS_URL", "wss://ws.reya.xyz/")

    await spot_tester.orders.close_all(fail_if_none=False)

    # Pre-restart resting state so the snapshot is non-trivial.
    px = int(spot_config.get_safe_no_match_buy_price())
    pre_params = OrderBuilder.from_config(spot_config).buy().price(str(px)).gtc().build()
    pre_id = await spot_tester.orders.create_limit(pre_params)
    assert pre_id, "pre-restart order must be created"

    recorder = MarketDataRecorder(
        ws_url, address=address, symbol=symbol, subscribe_order_changes=True, subscribe_depth=True
    )
    recorder.connect()
    try:
        assert await wait_until(
            lambda: recorder.order_changes_snapshot is not None and recorder.depth_snapshot_copy() is not None,
            timeout=15.0,
        ), "did not receive both subscribe snapshots before restart"
        await _assert_snapshot_consistent_with_rest(recorder, spot_tester, symbol, "pre-restart")

        # --- bounce the matching engine ---
        await asyncio.to_thread(_restart_matching_engine, kubeconfig)

        # --- observe the resync signal on the live subscriptions ---
        def _resync_signalled() -> bool:
            return recorder.has_close_code(1012) or _feed_reset_seen(recorder)

        got_signal = await wait_until(_resync_signalled, timeout=180.0, interval=0.5)
        closes = recorder.close_events()
        feed_reset = _feed_reset_seen(recorder)
        logger.info("post-restart: closes=%s feed_reset_inline=%s", closes, feed_reset)
        assert got_signal, (
            "no resync signal after ME restart: expected a 1012 close on the stateful "
            f"channels or inline FEED_RESET cancellations; closes={closes}"
        )

        if recorder.has_close_code(1012):
            code, reason = next((c, r) for c, r in closes if c == 1012)
            reason_l = (reason or "").lower()
            assert (
                reason is None or "resync" in reason_l or "reset" in reason_l
            ), f"1012 close reason did not describe a resync: {reason!r}"
            logger.info("observed 1012 resync close: reason=%r", reason)
    finally:
        recorder.close()

    # Gate on the ME write path actually recovering before asserting resumption:
    # the API/bun-socket reconnect to the new ME lags rollout-status success.
    assert await _wait_for_me_stable(spot_tester, spot_config), (
        "matching engine did not resume serving writes after restart within budget "
        "(localnet Finding-B snapshot-GET race — ping e2e-localnet to bounce it)"
    )

    # --- reconnect + resubscribe: server must deliver a fresh consistent snapshot ---
    fresh = MarketDataRecorder(
        ws_url, address=address, symbol=symbol, subscribe_order_changes=True, subscribe_depth=True
    )
    fresh.connect()
    try:
        assert await wait_until(
            lambda: fresh.order_changes_snapshot is not None and fresh.depth_snapshot_copy() is not None,
            timeout=30.0,
        ), "did not receive fresh snapshots after reconnect+resubscribe"
        await _assert_snapshot_consistent_with_rest(fresh, spot_tester, symbol, "post-resync")

        # --- diffs resume on the fresh connection ---
        base_frames = len(fresh.depth_updates())
        resume_px = int(spot_config.get_safe_no_match_buy_price()) + 3
        resume_params = OrderBuilder.from_config(spot_config).buy().price(str(resume_px)).gtc().build()
        resume_holder: dict[str, str] = {}

        async def _place_resume() -> bool:
            try:
                oid = await spot_tester.orders.create_limit(resume_params)
            except (ApiException, OSError, RuntimeError):
                return False
            if oid:
                resume_holder["id"] = oid
                return True
            return False

        assert await wait_until(
            _place_resume, timeout=60.0, interval=2.0
        ), "could not place post-resync order (ME write path unstable)"
        resume_id = resume_holder["id"]

        def _order_change_resumed() -> bool:
            return any(o.order_id == resume_id for o in fresh.order_change_events())

        def _depth_resumed() -> bool:
            return len(fresh.depth_updates()) > base_frames

        assert await wait_until(_order_change_resumed, timeout=20.0), "orderChanges diffs did not resume post-resync"
        assert await wait_until(_depth_resumed, timeout=20.0), "depth diffs did not resume post-resync"
        logger.info("T3 PASS: resync signalled, fresh snapshots consistent, diffs resumed")
    finally:
        fresh.close()
        await spot_tester.orders.close_all(fail_if_none=False)
