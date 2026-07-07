#!/usr/bin/env python3
"""devnet1 PERP bust-test harness — recreates the old spot cronos_bust_test.py
fill patterns (IDEA 1 + IDEA 2) on perps, gate-free.

No matching-engine / api deadline gate and no redeploy. Two on-chain reverts do
the work (both reachable because nothing off-chain blocks them):
  - `ReduceOnlyConditionFailed` — a reduceOnly order that can't actually reduce.
  - `AccountBelowIM` ("Account below required margin") — there is no off-chain
    pre-trade margin check on perp orders, so an over-margin fill reverts on-chain.

IDEA 1 — taker-defective, one maker, partial fills (old IDEA 1):
  A rests ONE GTC sell. B hits it with N clean IOC buys (settle, B goes long),
  then N reduceOnly IOC buys while long (can't reduce -> bust). A's single resting
  order is partially filled N+N times: N clean + N busted.

IDEA 2 — maker-defective, mixed sweep, one batch (old IDEA 2):
  A rests M oversized GTC sells while flat; ONE taker IOC sweeps all M. The fills
  settle until A (and the taker) cross AccountBelowIM mid-sweep, so the early fills
  settle and the later fills bust — a single sweep with a MIX of settled + busted
  fills.

NB: the bust REASON differs from the old script (ReduceOnly / AccountBelowIM vs
OrderExpired). The fill *patterns* match; reproducing OrderExpired specifically
would need the DISABLE_ORDER_DEADLINE_CHECKS gate on the api + ME.

Built on tomdevman's post-only SDK (reya-python-sdk feat/perpOB-cod-modify-tests,
PR #58), which signs the current OrderDetails incl. postOnly. Setup + run:

    cp .env.example .env        # then fill in two funded devnet1 perp accounts
    poetry install              # first time (or reuse an existing reya-python-sdk venv)
    python scripts/devnet1_perp_bust.py --mode idea1

Accounts come from .env: PERP_*_1 = maker, PERP_*_2 = taker (each a funded devnet1
MAINPERP account whose owner wallet is an authorized signer). Run from the repo
root so the #58 sdk/ is importable.
Market: ETHRUSDPERP (marketId 1).
Modes: idea1, idea2 (fully scripted), idea2-seed (you fire the sweep from the UI),
       baseline, cleanup.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from decimal import Decimal

# pylint: disable=wrong-import-position


sys.path.insert(0, ".")  # this worktree's #58 sdk/ wins over any editable install

from dotenv import load_dotenv  # noqa: E402

from sdk.open_api.models.time_in_force import TimeInForce  # noqa: E402
from sdk.reya_rest_api.client import ReyaTradingClient  # noqa: E402
from sdk.reya_rest_api.config import TradingConfig  # noqa: E402
from sdk.reya_rest_api.models.orders import LimitOrderParameters  # noqa: E402

# Keys/accounts come ONLY from .env (copy .env.example). Never hardcode them here.
load_dotenv()

API = os.environ.get("REYA_API_URL", "https://api-devnet.reya-cronos.network/v2")
GATEWAY = os.environ.get("REYA_ORDERS_GATEWAY", "0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D")
CHAIN = int(os.environ.get("CHAIN_ID", "89346162"))
DEX_ID = int(os.environ.get("REYA_DEX_ID", "1"))
SYMBOL = os.environ.get("PERP_SYMBOL", "ETHRUSDPERP")
TICK = Decimal("0.001")


def _acct(n: int) -> dict:
    """A funded devnet1 perp account from env (PERP_*_1 = maker, PERP_*_2 = taker).
    The owner wallet must be an authorized signer for the account."""
    try:
        acct = int(os.environ[f"PERP_ACCOUNT_ID_{n}"])
        return {
            "key": os.environ[f"PERP_PRIVATE_KEY_{n}"],
            "addr": os.environ[f"PERP_WALLET_ADDRESS_{n}"],
            "acct": acct,
            "name": f"acct{acct}",
        }
    except KeyError as e:
        raise SystemExit(
            f"Missing env var {e}. Copy .env.example to .env and set two funded devnet1 "
            f"perp accounts (PERP_*_1 = maker, PERP_*_2 = taker)."
        )


A = _acct(1)  # maker
B = _acct(2)  # taker


def mk_config(w: dict) -> TradingConfig:
    return TradingConfig(
        api_url=API,
        chain_id=CHAIN,
        owner_wallet_address=w["addr"],
        private_key=w["key"],
        account_id=w["acct"],
        orders_gateway_address=GATEWAY,
        dex_id_override=DEX_ID,
    )


async def client(w: dict) -> ReyaTradingClient:
    c = ReyaTradingClient(mk_config(w))
    await c.start()
    return c


# ---- reads (plain urllib; Cloudflare blocks the default UA) ----
def _get(path: str):
    req = urllib.request.Request(API + path, headers={"User-Agent": "Mozilla/5.0 (devnet1-bust)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # nosec B310 - devnet harness read from configured API.
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {"httpError": e.code}


def _data(r):
    return r.get("data", r) if isinstance(r, dict) else r


def positions(w):
    return _get(f"/wallet/{w['addr']}/positions") or []


def position_qty(w) -> Decimal:
    ps = positions(w)
    if not ps:
        return Decimal(0)
    p = ps[0]
    q = Decimal(str(p["qty"]))
    return q if str(p.get("side", "")).upper().startswith("B") else -q


def perp_execs(w):
    return _data(_get(f"/wallet/{w['addr']}/perpExecutions")) or []


def execution_busts(w):
    return _data(_get(f"/wallet/{w['addr']}/executionBusts")) or []


def counts(w):
    """(settled perp executions, execution busts) — used as before/after markers."""
    return len(perp_execs(w)), len(execution_busts(w))


def market():
    summary = _get(f"/perpMarket/{SYMBOL}/summary")
    return Decimal(summary["throttledMidPrice"]), Decimal(summary["markPrice"])


def round_tick(x: Decimal) -> Decimal:
    return (x / TICK).to_integral_value() * TICK


def collateral(w) -> Decimal:
    """The account's rUSD real balance — sizes idea2's margin cross point per-account."""
    for b in _get(f"/wallet/{w['addr']}/accountBalances") or []:
        if b.get("accountId") == w["acct"] and b.get("asset") == "RUSD":
            return Decimal(str(b["realBalance"]))
    return Decimal("0")


def market_imr() -> Decimal:
    for m in _get("/perpMarketDefinitions"):
        if m.get("symbol") == SYMBOL:
            return Decimal(str(m["initialMarginParameter"]))
    return Decimal("0.04")


async def _limit(c, *, is_buy, px, qty, tif, reduce_only=None):
    return await c.create_limit_order(
        LimitOrderParameters(
            symbol=SYMBOL, is_buy=is_buy, limit_px=str(px), qty=str(qty), time_in_force=tif, reduce_only=reduce_only
        )
    )


async def settle_down(ca, cb):
    """Flatten both accounts. idea1/idea2 leave A and B with ~equal-and-opposite
    positions; cross the overlap A<->B (the thin live book can't absorb size), then
    best-effort reduce any residual against the book. A net residual only arises when
    a stray third-party order got matched during the run — an EMPTY book makes A/B
    offset exactly and this flatten exact."""
    for _ in range(3):
        await ca.mass_cancel(symbol=SYMBOL, account_id=A["acct"])
        await cb.mass_cancel(symbol=SYMBOL, account_id=B["acct"])
        time.sleep(2)
        qa, qb = position_qty(A), position_qty(B)
        if qa == 0 and qb == 0:
            break
        _, oracle = market()
        if qa * qb < 0:  # opposite signs -> cross the overlap A<->B
            s = min(abs(qa), abs(qb))
            long_c = ca if qa > 0 else cb
            short_c = cb if qa > 0 else ca
            await _limit(long_c, is_buy=False, px=round_tick(oracle), qty=s, tif=TimeInForce.GTC)
            time.sleep(1)
            await _limit(
                short_c,
                is_buy=True,
                px=round_tick(oracle * Decimal("1.05")),
                qty=s,
                tif=TimeInForce.IOC,
                reduce_only=True,
            )
            time.sleep(4)
        else:  # residual on one side only -> reduce against the book (best effort)
            for c, w in ((ca, A), (cb, B)):
                q = position_qty(w)
                if q == 0:
                    continue
                mult = Decimal("1.05") if q < 0 else Decimal("0.95")
                await _limit(
                    c, is_buy=(q < 0), px=round_tick(oracle * mult), qty=abs(q), tif=TimeInForce.IOC, reduce_only=True
                )
            time.sleep(4)
    await ca.mass_cancel(symbol=SYMBOL, account_id=A["acct"])
    await cb.mass_cancel(symbol=SYMBOL, account_id=B["acct"])
    a, b = positions(A), positions(B)
    tail = "" if not (a or b) else "  (residual — empty the book for an exact flatten)"
    print(f"  flattened: A={a} B={b}{tail}")


async def mode_baseline():
    """Normal IOC trade that settles — sanity-checks signing + on-chain settlement."""
    ca, cb = await client(A), await client(B)
    try:
        await settle_down(ca, cb)
        pool, _ = market()
        sell_px = round_tick(pool)
        print(f"A rests GTC SELL 0.01 @ {sell_px}; B IOC BUYs 0.01")
        await _limit(ca, is_buy=False, px=sell_px, qty="0.01", tif=TimeInForce.GTC)
        time.sleep(1)
        pe0, _ = counts(B)
        r = await _limit(
            cb, is_buy=True, px=round_tick(sell_px + TICK), qty="0.01", tif=TimeInForce.IOC, reduce_only=False
        )
        print(f"  B IOC BUY -> {r.status}")
        time.sleep(5)
        pe1, _ = counts(B)
        print(f"  settled fills (B perpExecutions delta) = {pe1 - pe0}; B pos = {positions(B)}")
        await settle_down(ca, cb)
    finally:
        await ca.close()
        await cb.close()


async def mode_idea1(n_clean=3, n_bust=3, take_qty=Decimal("0.01")):
    """IDEA 1 — one resting maker, N clean + N busted partial fills (taker-defective).

    A rests ONE GTC sell of (n_clean+n_bust)*take_qty. B buys it down with n_clean
    clean IOCs (settle; B goes long), then n_bust reduceOnly IOCs while long (a buy
    can't reduce a long -> ReduceOnlyConditionFailed). A is the innocent maker."""
    ca, cb = await client(A), await client(B)
    try:
        await settle_down(ca, cb)
        pool, _ = market()
        sell_px = round_tick(pool)
        total = take_qty * (n_clean + n_bust)
        print(f"A rests ONE GTC SELL {total} @ {sell_px} (the single maker)")
        await _limit(ca, is_buy=False, px=sell_px, qty=total, tif=TimeInForce.GTC)
        time.sleep(1)
        take_px = round_tick(sell_px + TICK)
        _, eb0 = counts(B)

        for i in range(n_clean):
            r = await _limit(cb, is_buy=True, px=take_px, qty=take_qty, tif=TimeInForce.IOC, reduce_only=False)
            print(f"  clean IOC BUY #{i + 1} ({take_qty}) -> {r.status}")
            time.sleep(1)
        for i in range(n_bust):
            r = await _limit(
                cb, is_buy=True, px=take_px, qty=take_qty, tif=TimeInForce.IOC, reduce_only=True
            )  # B is long -> can't reduce -> bust
            print(f"  reduceOnly IOC BUY #{i + 1} ({take_qty}) -> {r.status} (expect on-chain bust)")
            time.sleep(1)

        time.sleep(5)
        _, eb1 = counts(B)
        n_b = eb1 - eb0
        print(
            f"\n>>> IDEA 1: {(n_clean + n_bust) - n_b} settled fills + {n_b} busts on A's one resting order "
            f"(expected {n_clean} + {n_bust})."
        )
        for b in [x for x in execution_busts(B) if x.get("qty") == str(take_qty)][:n_bust]:
            print(f"    bust: qty={b.get('qty')} reason={b.get('reason')!r}")
        await settle_down(ca, cb)
    finally:
        await ca.close()
        await cb.close()


async def mode_idea2(n_orders=6, settle_target=3):
    """IDEA 2 — many resting makers, one sweep, MIXED settle/bust batch (maker-defective).

    A rests n_orders equal GTC sells while flat, each sized so that after
    `settle_target` of them fill, A (and the sweeping taker) cross AccountBelowIM.
    ONE taker IOC sweeps all n_orders -> the first `settle_target` settle and the
    rest bust in a single sweep. The defective ones are the later fills (margin),
    not a per-order property."""
    ca, cb = await client(A), await client(B)
    try:
        await settle_down(ca, cb)
        _, oracle = market()
        # Price at the MARK (oracle): filling at the mark keeps uPnL ~0 so the IM
        # check reflects notional only, making the cross-margin point predictable.
        capacity = collateral(A) / (oracle * market_imr())  # ~ETH the maker holds before IM breach
        per_qty = (capacity / (Decimal(settle_target) + Decimal("0.5"))).quantize(Decimal("0.001"))
        total = per_qty * n_orders
        sell_px = round_tick(oracle)
        print(
            f"capacity~{capacity:.1f} ETH; A rests {n_orders} GTC SELLs of {per_qty} @ {sell_px} "
            f"(cum crosses IM after ~{settle_target})"
        )
        for i in range(n_orders):
            r = await _limit(ca, is_buy=False, px=sell_px, qty=per_qty, tif=TimeInForce.GTC)
            print(f"  maker SELL #{i + 1} ({per_qty}) -> {r.status}")
        time.sleep(1)
        _, eb0 = counts(B)
        r = await _limit(
            cb, is_buy=True, px=round_tick(oracle * Decimal("1.05")), qty=total, tif=TimeInForce.IOC, reduce_only=False
        )
        print(f"\nB ONE IOC BUY {total} (sweeps all {n_orders}) -> {r.status} cumQty={r.cum_qty}")
        time.sleep(7)
        _, eb1 = counts(B)
        n_bust = eb1 - eb0
        print(
            f"\n>>> IDEA 2: {n_orders - n_bust} settled fills + {n_bust} busts in one sweep "
            f"(target {settle_target} + {n_orders - settle_target})."
        )
        for b in [x for x in execution_busts(B) if x.get("qty") == str(per_qty)][:n_orders]:
            print(f"    bust: qty={b.get('qty')} reason={b.get('reason')!r}")
        print(f"  positions after sweep: A={positions(A)} B={positions(B)}")
        await settle_down(ca, cb)
    finally:
        await ca.close()
        await cb.close()


async def mode_idea2_seed(n_orders=6, settle_target=3):
    """Seed IDEA 2's resting makers and STOP — fire the aggressive sweep from the UI
    yourself (the old IDEA 2 workflow). A rests n_orders oversized GTC sells sized so
    A crosses AccountBelowIM after ~settle_target fills. Send ONE big BUY from the dapp
    to sweep them -> the first ~settle_target settle, the rest bust. Fire the UI sweep
    from the TAKER wallet (PERP_*_2) so `--mode cleanup` can flatten, else close your
    UI position by hand afterwards."""
    ca = await client(A)
    try:
        await ca.mass_cancel(symbol=SYMBOL, account_id=A["acct"])
        time.sleep(1)
        if positions(A):
            print(f"maker {A['name']} not flat ({positions(A)}); run --mode cleanup first.")
            return
        _, oracle = market()
        capacity = collateral(A) / (oracle * market_imr())
        per_qty = (capacity / (Decimal(settle_target) + Decimal("0.5"))).quantize(Decimal("0.001"))
        total = per_qty * n_orders
        sell_px = round_tick(oracle)
        for i in range(n_orders):
            r = await _limit(ca, is_buy=False, px=sell_px, qty=per_qty, tif=TimeInForce.GTC)
            print(f"  maker SELL #{i + 1} ({per_qty}) @ {sell_px} -> {r.status}")
        print(f"\n>>> Seeded {n_orders} resting SELLs (~{per_qty} each) on {SYMBOL} from acct {A['acct']}.")
        print(f"    Now fire ONE aggressive BUY of ~{total} on {SYMBOL} from the dapp to sweep them.")
        print(f"    Expect ~{settle_target} settled + {n_orders - settle_target} busted (AccountBelowIM).")
        print("    Then flatten:  python scripts/devnet1_perp_bust.py --mode cleanup")
    finally:
        await ca.close()


async def mode_cleanup():
    ca, cb = await client(A), await client(B)
    try:
        await settle_down(ca, cb)
    finally:
        await ca.close()
        await cb.close()


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True, choices=["idea1", "idea2", "idea2-seed", "baseline", "cleanup"])
    args = ap.parse_args()
    if args.mode == "idea1":
        await mode_idea1()
    elif args.mode == "idea2":
        await mode_idea2()
    elif args.mode == "idea2-seed":
        await mode_idea2_seed()
    elif args.mode == "baseline":
        await mode_baseline()
    else:
        await mode_cleanup()


if __name__ == "__main__":
    asyncio.run(main())
