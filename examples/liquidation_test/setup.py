"""Operator-run account creation and equal rUSD split; never opens positions."""

from typing import Any

import argparse
import fcntl
import getpass
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.logs import DISCARD

from examples.liquidation_test.readiness import OWNER, Readiness, emit, signer_from_env, view_abi


class State:
    """One process per state directory; atomic writes, no private keys or signed payloads."""

    def __init__(self, directory: str, owner: str, source: int):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock = (self.directory / "lock").open("a")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.path = self.directory / "state.json"
        identity = {"chain_id": 1729, "owner": owner, "source_account": source}
        self.data: dict[str, Any] = (
            json.loads(self.path.read_text()) if self.path.exists() else {**identity, "transactions": {}}
        )
        if any(self.data.get(key) != value for key, value in identity.items()):
            raise ValueError("State directory belongs to a different owner, source or chain")
        self.save()

    def save(self) -> None:
        temp = self.directory / "state.tmp"
        with temp.open("w", encoding="utf-8") as file:
            os.chmod(temp, 0o600)
            json.dump(self.data, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.path)
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def close(self) -> None:
        self.lock.close()


def rusd_units(read: Readiness, source: int) -> int:
    """Read actual token-denominated cash, not USD margin or net deposits."""
    abi = view_abi(
        "getCollateralInfo",
        ["uint128", "address"],
        [
            {
                "type": "tuple",
                "components": [
                    {"name": "netDeposits", "type": "int256"},
                    {"name": "marginBalance", "type": "int256"},
                    {"name": "realBalance", "type": "int256"},
                ],
            }
        ],
    )
    core = read.web3.eth.contract(address=read.core.address, abi=[abi])
    _, margin, balance = core.functions.getCollateralInfo(source, read.rusd.address).call()
    if margin != balance:
        raise ValueError("rUSD margin and cash differ; source must be flat and settled")
    return int(balance)


def split_units(balance: int) -> tuple[int, int]:
    if balance < 2:
        raise ValueError("Need at least 0.000002 rUSD to fund two accounts")
    return balance // 2, balance % 2


def funding_signer(read: Readiness, source: int, key: str) -> Any:
    try:
        signer = Account.from_key(key)
    except Exception:
        raise ValueError("Invalid funding key; value redacted") from None
    if signer.address.lower() != read.owner.lower():
        raise ValueError("Use the owner wallet key: fresh-account activation requires owner authority")
    if read.web3.eth.get_balance(signer.address) <= 0:
        raise ValueError("Funding signer has no native gas balance")
    return signer


def receipt(read: Readiness, state: State, name: str, timeout: int) -> Any:
    entry = state.data["transactions"][name]
    # A saved hash is authoritative even if the previous send timed out. Never resend.
    result = read.web3.eth.wait_for_transaction_receipt(entry["hash"], timeout=timeout)
    if result["status"] != 1:
        raise ValueError(f"{name} reverted; inspect {entry['hash']} before manual recovery")
    entry["confirmed"] = True
    state.save()
    return result


def transact(read: Readiness, state: State, name: str, function: Any, signer: Any, timeout: int) -> Any:
    if name in state.data["transactions"]:
        return receipt(read, state, name, timeout)
    sender = signer.address
    function.call({"from": sender})
    nonce = read.web3.eth.get_transaction_count(sender, "pending")
    if nonce != read.web3.eth.get_transaction_count(sender, "latest"):
        raise ValueError("Funding signer already has pending transactions; wait before continuing")
    gas = (function.estimate_gas({"from": sender}) * 120 + 99) // 100
    price = read.web3.eth.gas_price
    if read.web3.eth.get_balance(sender) < gas * price:
        raise ValueError(f"Insufficient native gas for {name}")
    transaction = function.build_transaction(
        {
            "from": sender,
            "chainId": 1729,
            "nonce": nonce,
            "gas": gas,
            "gasPrice": price,
            "value": 0,
        }
    )
    signed = signer.sign_transaction(transaction)
    tx_hash = Web3.to_hex(Web3.keccak(signed.raw_transaction))
    state.data["transactions"][name] = {"hash": tx_hash, "nonce": nonce, "sender": sender}
    state.save()  # Persist BEFORE sending, including before an ambiguous transport error.
    emit({"step": name, "transaction_hash": tx_hash})
    read.web3.eth.send_raw_transaction(signed.raw_transaction)
    return receipt(read, state, name, timeout)


def account_from_receipt(read: Readiness, result: Any) -> int:
    events = read.core.events.AccountCreated().process_receipt(result, errors=DISCARD)
    ids = [
        int(event["args"]["accountId"])
        for event in events
        if event["address"].lower() == read.core.address.lower()
        and event["args"]["owner"].lower() == read.owner.lower()
        and 0 < int(event["args"]["accountId"]) < 10**10
    ]
    if len(ids) != 1:
        raise ValueError("Expected exactly one owned perp AccountCreated event; inspect the saved transaction")
    read.assert_owned_perp(ids[0])
    return ids[0]


def setup(read: Readiness, args: argparse.Namespace, state: State, signer: Any) -> None:
    source = args.source_account
    if state.data.get("activation_market_id", args.activation_market_id) != args.activation_market_id:
        raise ValueError("Resume with the original activation market")
    state.data["activation_market_id"] = args.activation_market_id
    state.save()
    activation_abi = view_abi("activateFirstMarketForAccount", ["uint128", "uint128"], [])
    activation_abi["stateMutability"] = "nonpayable"
    pool_abi = view_abi("getCollateralPoolIdOfAccount", ["uint128"], [{"type": "uint128"}])
    activation = read.web3.eth.contract(address=read.core.address, abi=[activation_abi, pool_abi])
    if "fund" not in state.data["transactions"]:
        read.assert_empty([source])
        split_units(rusd_units(read, source))
        for side in ("long", "short"):
            name = f"create_{side}"
            result = transact(read, state, name, read.core.functions.createAccount(read.owner), signer, args.timeout)
            state.data[f"{side}_account"] = account_from_receipt(read, result)
            state.save()
            account_id = state.data[f"{side}_account"]
            transact(
                read,
                state,
                f"activate_{side}",
                activation.functions.activateFirstMarketForAccount(account_id, args.activation_market_id),
                signer,
                args.timeout,
            )
            pool = activation.functions.getCollateralPoolIdOfAccount(account_id).call()
            if pool == 0 or pool != activation.functions.getCollateralPoolIdOfAccount(source).call():
                raise ValueError("New account collateral pool does not match the source; stop before funding")
        targets = [state.data["long_account"], state.data["short_account"]]
        units, remainder = split_units(rusd_units(read, source))
        amount = Decimal(units) / 10**6
        plan = read.funding_plan(source, targets, amount)
        _, arguments = read.core.decode_function_input(plan["data"])
        function = read.core.functions.execute(arguments["accountId"], arguments["commands"])
        state.data.update(amount_per_account=str(amount), source_dust_rusd=str(Decimal(remainder) / 10**6))
        state.save()
        transact(read, state, "fund", function, signer, args.timeout)
    else:
        receipt(read, state, "fund", args.timeout)
    targets = [state.data["long_account"], state.data["short_account"]]
    read.assert_empty(targets)
    expected = int(Decimal(state.data["amount_per_account"]) * 10**6)
    if any(rusd_units(read, account) != expected for account in targets):
        raise ValueError("Destination balances differ from the confirmed split; inspect state before trading")
    state.data["complete"] = True
    state.save()
    emit(
        {
            "setup_complete": True,
            "long_account": targets[0],
            "short_account": targets[1],
            "rusd_per_account": state.data["amount_per_account"],
            "source_dust_rusd": state.data["source_dust_rusd"],
            "positions_opened": False,
            "state_file": str(state.path),
        }
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--owner", default=OWNER)
    root.add_argument("--source-account", type=int, default=135145)
    root.add_argument(
        "--trade-env-file", required=True, help="Existing OrdersGateway delegate env; never used for funding"
    )
    root.add_argument("--trade-key-var", default="PERP_PRIVATE_KEY_1")
    root.add_argument("--state-dir", required=True, help="Reuse this directory when resuming")
    root.add_argument(
        "--activation-market-id",
        type=int,
        default=1,
        help="Core market used to initialize the collateral pool (1 = ETH); no trade",
    )
    root.add_argument("--timeout", type=int, default=180)
    root.add_argument("--execute", action="store_true", help="Create accounts and fund them on mainnet")
    return root


def main(args: argparse.Namespace) -> None:
    if args.timeout <= 0 or args.activation_market_id <= 0:
        raise ValueError("Timeout and activation market ID must be positive")
    read = Readiness(args.owner)
    read.assert_owned_perp(args.source_account)
    _, delegate = signer_from_env(args.trade_env_file, args.trade_key_var, read.owner)
    if not read.gateway.functions.hasPermission(read.owner, delegate).call():
        raise ValueError("Trading delegate lacks OrdersGateway permission for this owner")
    if not args.execute:
        read.assert_empty([args.source_account])
        units, dust = split_units(rusd_units(read, args.source_account))
        emit(
            {
                "execute": False,
                "owner": read.owner,
                "source_account": args.source_account,
                "new_perp_accounts": 2,
                "activation_market_id": args.activation_market_id,
                "rusd_per_account": str(Decimal(units) / 10**6),
                "source_dust_rusd": str(Decimal(dust) / 10**6),
                "trading_delegate": delegate,
            }
        )
        return
    state = State(args.state_dir, read.owner, args.source_account)
    try:
        signer = None
        if "fund" not in state.data["transactions"]:
            # Validate read-only readiness before asking for sensitive local input.
            read.assert_empty([args.source_account])
            split_units(rusd_units(read, args.source_account))
            if not sys.stdin.isatty():
                raise ValueError("Run in an interactive terminal for hidden funding-key input")
            key = getpass.getpass("Owner wallet private key (hidden; never saved): ")
            signer = funding_signer(read, args.source_account, key)
            del key
        setup(read, args, state, signer)
    finally:
        state.close()


if __name__ == "__main__":
    try:
        main(parser().parse_args())
    except ValueError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except Exception as error:
        print(
            f"Stopped: {type(error).__name__}. Rerun with the SAME state directory to reconcile saved hashes; "
            "do not delete state or start a second setup.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
