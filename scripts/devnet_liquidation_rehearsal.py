"""Controlled Keyrock devnet rehearsal. Read-only unless --execute is supplied.

Run from the SDK worktree with: .venv/bin/python -m scripts.devnet_liquidation_rehearsal status
Credentials come from the shared .env and the devnet liquidator's RPC secret.
Never prints keys, signed order payloads, or RPC credentials.
"""

import argparse
import asyncio
import json
import subprocess
import time
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import requests
from dotenv import dotenv_values
from eth_abi import decode, encode
from web3 import Web3

from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.client import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters

ROOT = Path(__file__).resolve().parents[1]
API = "https://api-devnet.reya-cronos.network/v2"
CONTEXT = "gke_testnet-473109_europe-west3_devnet1-euwest3-gke01"
NAMESPACE = "reya-devnet1"
CORE = Web3.to_checksum_address("0xC33D0A4FC05aF98447126f1680cA7316de29e5d4")
PERP = Web3.to_checksum_address("0x6f42DB6d75Da0B85bDd386b96Cbfb73416AB37A4")
RUSD = Web3.to_checksum_address("0x9de724e7b3facf87ce39465d3d712717182e3e55")
WALLETS = {
    9: "0x6C51275FD01d5DbD2DA194E92f920f8598306dF2",
    11: "0x869d6494fe32B96F93F78F9c4B7aAf30eeC01C1F",
    372: "0xB89F0700dc6D92f715325d460aAF87a1640F629B",
}
SYMBOL = "ETHRUSDPERP"
WAD = Decimal(10**18)
MARGIN_NAMES = (
    "margin",
    "real_balance",
    "initial_delta",
    "maintenance_delta",
    "liquidation_delta",
    "dutch_delta",
    "adl_delta",
    "initial_buffer_delta",
    "lmr",
)
POSITION_TYPE = "(int256,int256,(uint256,uint256),(int256,uint256,uint256),int256,uint128)"
EXECUTION_PREFIX = "(int256,(uint256,uint256,uint256,uint256),uint256,uint128,uint8,"
EXECUTION_FIELDS = ",".join([POSITION_TYPE] * 4) + ",address,address,uint64,uint64,uint128,uint256"
EXECUTION_TYPES = [EXECUTION_PREFIX + EXECUTION_FIELDS + tail for tail in (")", ",bytes)")]
EXECUTION_TOPICS = {
    Web3.keccak(
        text="PassivePerpExecutionV3(uint128,uint128,uint128,uint128,uint128,uint256," + payload + ")"
    ).hex(): payload
    for payload in EXECUTION_TYPES
}


def emit(event, **values):
    print(json.dumps({"event": event, **values}, default=str), flush=True)


def kube(*args):
    return subprocess.check_output(
        ["kubectl", "--context", CONTEXT, "-n", NAMESPACE, *args], text=True, timeout=20
    ).strip()


def api(path):
    response = requests.get(API + path, timeout=15)
    response.raise_for_status()
    return response.json()


def target_price(base, margin, lmr, mark, ratio):
    """Solve single-market margin / LMR at the requested health ratio."""
    if base == 0 or lmr <= 0 or mark <= 0 or not Decimal(0) < ratio < 1:
        raise ValueError("Expected a non-flat single-market position and a valid target health")
    delta = (ratio * lmr - margin) / (base - ratio * lmr / mark)
    return (mark + delta).quantize(Decimal("0.001"))


class Rehearsal:
    def __init__(self):
        import base64

        self.env = dotenv_values(ROOT / ".env")
        rpc = base64.b64decode(
            kube("get", "secret", "devnet1-liquidator-secret", "-o", "jsonpath={.data.RPC_PROVIDER}")
        ).decode()
        self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 15}))
        if self.w3.eth.chain_id != 89346162:
            raise RuntimeError("Refusing non-devnet chain")
        self.core = self.w3.eth.contract(
            address=CORE, abi=json.loads((ROOT / "sdk/reya_rpc/abis/CoreProxy.json").read_text())
        )
        for account_id, owner in WALLETS.items():
            actual = self.call(CORE, "getAccountOwner(uint128)", ["uint128"], [account_id], ["address"])[0]
            if actual.lower() != owner.lower():
                raise RuntimeError(f"Owner mismatch for account {account_id}")
        for account_id in (9, 372):
            pool = self.call(CORE, "getCollateralPoolIdOfAccount(uint128)", ["uint128"], [account_id], ["uint128"])[0]
            if pool != 1:
                raise RuntimeError(f"Account {account_id} is not active in collateral pool 1")
        backstop = self.call(CORE, "getBackstopLPConfig(uint128)", ["uint128"], [1], ["uint256"] * 5)
        self.risk = self.call(CORE, "getRiskMultipliers(uint128)", ["uint128"], [1], ["uint256"] * 5)
        if backstop[0] != 372 or self.risk[1:4] != (10**18, 10**18, 650000000000000000):
            raise RuntimeError("Liquidation routing or tier thresholds changed; inspect before proceeding")

    def call(self, address, signature, types, args, outputs, block="latest"):
        data = Web3.keccak(text=signature)[:4] + encode(types, args)
        return decode(outputs, self.w3.eth.call({"to": address, "data": data}, block_identifier=block))

    def base(self, account_id, block="latest"):
        # base is the first static return word; later position fields can evolve.
        return (
            Decimal(
                self.call(
                    PERP,
                    "getUpdatedPositionInfo(uint128,uint128)",
                    ["uint128", "uint128"],
                    [1, account_id],
                    ["int256"],
                    block,
                )[0]
            )
            / WAD
        )

    def margin(self, account_id, block="latest"):
        row = self.core.functions.getUsdNodeMarginInfo(account_id).call(block_identifier=block)
        return dict(zip(MARGIN_NAMES, (Decimal(v) / WAD for v in row[1:]), strict=True))

    def prices(self):
        # Market.Data is an all-static tuple. Slot 21/22 are markPrice/timestamp
        # in perpOB; following words are funding, lock price and block metadata.
        data = self.call(PERP, "getMarketData(uint128)", ["uint128"], [1], ["uint256"] * 28)
        oracle = next(p for p in api("/assetOraclePrices") if p["asset"] == "ETH")
        if time.time() * 1000 - min(data[22] * 1000, oracle["updatedAt"]) > 15000:
            raise RuntimeError("Price feeds are stale")
        return Decimal(data[21]) / WAD, Decimal(oracle["oraclePrice"])

    def executions(self, from_block):
        logs = self.w3.eth.get_logs(
            {
                "address": PERP,
                "fromBlock": from_block,
                "toBlock": "latest",
                "topics": [
                    ["0x" + t.removeprefix("0x") for t in EXECUTION_TOPICS],
                    "0x" + encode(["uint128"], [1]).hex(),
                    "0x" + encode(["uint128"], [9]).hex(),
                ],
            }
        )
        result = []
        for log in logs:
            payload_type = EXECUTION_TOPICS[log.topics[0].hex()]
            _, counterparty, _, payload = decode(["uint128", "uint128", "uint256", payload_type], log.data)
            result.append(
                {
                    "type": payload[4],
                    "counterparty": counterparty,
                    "qty": Decimal(payload[0]) / WAD,
                    "price": Decimal(payload[2]) / WAD,
                    "sequence": int.from_bytes(log.topics[3]),
                    "transaction_hash": "0x" + log.transactionHash.hex().removeprefix("0x"),
                }
            )
        return result

    def status(self):
        block = self.w3.eth.block_number
        mark, index = self.prices()
        accounts = {a: {"base": self.base(a, block), **self.margin(a, block)} for a in WALLETS}
        emit("status", block=block, mark=mark, index=index, accounts=accounts)
        return accounts

    def client(self, slot):
        account_id = int(self.env[f"PERP_ACCOUNT_ID_{slot}"])
        if account_id != {1: 11, 2: 9}[slot]:
            raise RuntimeError("Shared env account slots changed")
        client = ReyaTradingClient(
            TradingConfig(
                api_url=API,
                chain_id=89346162,
                account_id=account_id,
                owner_wallet_address=WALLETS[account_id],
                private_key=self.env[f"PERP_PRIVATE_KEY_{slot}"],
                orders_gateway_address="0x7Ec89E555c771D2B5939aBE5C4E4291852633D4D",
                dex_id_override=1,
            )
        )
        if client.signer_wallet_address.lower() != WALLETS[account_id].lower():
            raise RuntimeError("Expected the account owner signer")
        return client

    def fund(self, execute):
        if self.base(9) != 0:
            raise RuntimeError("Funding reset requires a flat victim")
        source = 10000000006
        owner = self.call(CORE, "getAccountOwner(uint128)", ["uint128"], [source], ["address"])[0]
        if owner.lower() != WALLETS[9].lower():
            raise RuntimeError("Spot source owner mismatch")
        current = self.margin(9)["margin"]
        amount = int((Decimal(400) - current) * 10**6)
        emit("fund_plan", source=source, destination=9, current=current, amount_rusd=Decimal(amount) / 10**6)
        if amount < 0:
            raise RuntimeError("Victim already exceeds 400; inspect before withdrawing")
        if not execute or amount == 0:
            return
        signer = self.w3.eth.account.from_key(self.env["PERP_PRIVATE_KEY_2"])
        if signer.address.lower() != WALLETS[9].lower():
            raise RuntimeError("Funding signer mismatch")
        inputs = encode(["uint128", "address", "uint256"], [9, RUSD, amount])
        tx = self.core.functions.execute(source, [(4, inputs, 0, 0)]).build_transaction(
            {
                "from": signer.address,
                "nonce": self.w3.eth.get_transaction_count(signer.address, "pending"),
                "chainId": 89346162,
            }
        )
        receipt = self.w3.eth.wait_for_transaction_receipt(
            self.w3.eth.send_raw_transaction(signer.sign_transaction(tx).raw_transaction), timeout=90
        )
        emit("fund_receipt", transaction_hash=receipt.transactionHash.hex(), status=receipt.status)
        if receipt.status != 1 or abs(self.margin(9)["margin"] - 400) > Decimal("0.00001"):
            raise RuntimeError("Funding did not produce the expected 400 rUSD")

    async def open(self, qty, side, execute):
        if self.base(9) != 0:
            raise RuntimeError("Victim must be flat before opening")
        if not Decimal(0) < qty <= Decimal(4):
            raise RuntimeError("Rehearsal size must be in (0, 4] ETH")
        mark, _ = self.prices()
        definition = next(d for d in api("/perpMarketDefinitions") if d["marketId"] == 1)
        im = Decimal(definition["initialMarginParameter"]) * mark * qty
        if self.margin(9)["initial_delta"] < im + 5:
            raise RuntimeError("Insufficient victim entry margin with 5 USD headroom")
        depth = api(f"/market/{SYMBOL}/depth")
        tick = Decimal(definition["tickSize"])
        bid = max((Decimal(b["px"]) for b in depth["bids"]), default=mark - 1)
        ask = min((Decimal(a["px"]) for a in depth["asks"]), default=mark + 1)
        victim_buy = side == "long"
        candidate = max(bid + tick, min(mark, ask - tick))
        price = (candidate / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        if (
            (victim_buy and price <= bid)
            or (not victim_buy and price >= ask)
            or abs(price / mark - 1) > Decimal("0.002")
        ):
            raise RuntimeError("No controlled maker price near mark; pause competing quotes and inspect book")
        expected_base = qty if victim_buy else -qty
        emit("open_plan", qty=qty, side=side, price=price, initial_margin=im, maker=11, victim=9)
        if not execute:
            return
        clients = [self.client(1), self.client(2)]
        maker, victim = clients
        original_maker = self.base(11)
        resting_id = None
        try:
            for client in clients:
                await client.start()
                if any(
                    o.account_id == client.config.account_id and o.symbol == SYMBOL
                    for o in await client.get_open_orders()
                ):
                    raise RuntimeError("Rehearsal accounts have existing ETH orders")
            resting = await maker.create_limit_order(
                LimitOrderParameters(SYMBOL, not victim_buy, str(price), str(qty), TimeInForce.GTC, post_only=True)
            )
            resting_id = resting.order_id
            emit("maker_order", response=resting.to_dict())
            fill = await victim.create_limit_order(
                LimitOrderParameters(SYMBOL, victim_buy, str(price), str(qty), TimeInForce.IOC)
            )
            emit("victim_order", response=fill.to_dict())
            for _ in range(60):
                if self.base(9) == expected_base and self.base(11) == original_maker - expected_base:
                    emit("open_settled", victim_base=self.base(9), maker_base=self.base(11))
                    return
                await asyncio.sleep(1)
            raise RuntimeError("Opening fill not fully settled; inspect, do not blindly retry")
        finally:
            if resting_id is not None:
                for order in await maker.get_open_orders():
                    if str(order.order_id) == str(resting_id):
                        await maker.cancel_order(symbol=SYMBOL, order_id=str(resting_id))
            for client in clients:
                await client.close()

    def mark_command(self, action, price=None):
        args = [
            "exec",
            "deployment/devnet1-matching-engine",
            "--",
            "/app/bin/matching-engine-cli",
            "mark-price",
            action,
            "--market-id",
            "1",
        ]
        if price is not None:
            args += ["--price", str(price), "--ttl-seconds", "60"]
        emit("mark_control", output=kube(*args))

    def liquidate(self, stage, execute):
        q = self.base(9)
        if q == 0:
            raise RuntimeError("Expected an existing victim position")
        mark, index = self.prices()
        margin = self.margin(9)
        ratio = Decimal("0.85") if stage == "dutch" else Decimal("0.35")
        # Solve margin(p) / LMR(p) = target, keeping backstop solvent after fees.
        price = target_price(q, margin["margin"], margin["lmr"], mark, ratio)
        delta = price - mark
        deviation = abs(price / index - 1)
        emit(
            "liquidation_plan",
            stage=stage,
            price=price,
            index=index,
            deviation=deviation,
            victim_base=q,
            target_margin_lmr_ratio=ratio,
            current_margin=margin,
        )
        if deviation > Decimal("0.043") or q * delta >= 0:
            raise RuntimeError("Target outside safe override band, or victim already past target; inspect")
        lp = self.margin(372)
        if lp["initial_delta"] < abs(q) * price * Decimal("0.05"):
            raise RuntimeError("Insufficient Keyrock headroom for full inventory")
        if stage == "backstop":
            lp_base = self.base(372)
            projected_lp_margin = lp["margin"] + lp_base * delta
            projected_combined_lmr = margin["lmr"] / abs(q) * abs(lp_base + q) * price / mark
            buffer_cap = 2 * projected_lp_margin / (Decimal(self.risk[0] + self.risk[4]) / WAD)
            if projected_combined_lmr + 5 > buffer_cap:
                raise RuntimeError("Liquidator would not choose a full backstop at the projected price")
        if not execute:
            return
        before_block = self.w3.eth.block_number
        before_keyrock = self.base(372)
        try:
            self.mark_command("set", price)
            for _ in range(50):
                time.sleep(1)
                after = self.base(9)
                received = self.base(372) - before_keyrock
                if abs(after) < abs(q) and received == q - after:
                    if stage == "backstop" and after != 0:
                        continue
                    executions = self.executions(before_block)
                    wanted = 1 if stage == "dutch" else 3
                    if not executions or any(e["type"] != wanted or e["counterparty"] != 372 for e in executions):
                        emit("unexpected_executions", executions=executions)
                        raise RuntimeError("Position changed but exact liquidation tier was not proved")
                    emit(
                        "liquidation_settled",
                        stage=stage,
                        from_block=before_block,
                        to_block=self.w3.eth.block_number,
                        victim_before=q,
                        victim_after=after,
                        keyrock_received=received,
                        executions=executions,
                    )
                    return
            raise RuntimeError("No expected liquidation in 50 seconds; inspect receipts and liquidator logs")
        finally:
            self.mark_command("clear")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["status", "fund", "open", "dutch", "backstop", "clear"])
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--qty", type=Decimal, default=Decimal("3.8"))
    parser.add_argument("--side", choices=["long", "short"], default="long")
    args = parser.parse_args()
    rehearsal = Rehearsal()
    if args.action == "status":
        rehearsal.status()
    elif args.action == "fund":
        rehearsal.fund(args.execute)
    elif args.action == "open":
        await rehearsal.open(args.qty, args.side, args.execute)
    elif args.action == "clear":
        if args.execute:
            rehearsal.mark_command("clear")
    else:
        rehearsal.liquidate(args.action, args.execute)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as error:
        emit("stopped", reason=str(error))
        raise SystemExit(1) from None
    except Exception as error:
        # Network exception text can contain a credential-bearing RPC URL.
        emit("stopped", error_type=type(error).__name__)
        raise SystemExit(1) from None
