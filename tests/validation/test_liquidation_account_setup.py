"""All setup tests are offline; no production signing or RPC submission."""

from types import SimpleNamespace

from decimal import Decimal
from unittest.mock import Mock

import pytest
from eth_account import Account

from examples.liquidation_test import setup as module
from examples.liquidation_test.readiness import OWNER, Readiness


def state(tmp_path):
    return module.State(str(tmp_path / "run"), OWNER, 135145)


@pytest.mark.parametrize("balance,expected", [(112010129, (56005064, 1)), (100000000, (50000000, 0)), (3, (1, 1))])
def test_equal_native_unit_split(balance, expected):
    assert module.split_units(balance) == expected


@pytest.mark.parametrize("balance", [-1, 0, 1])
def test_split_rejects_insufficient_cash(balance):
    with pytest.raises(ValueError):
        module.split_units(balance)


def test_raw_balance_uses_cash_not_net_deposits():
    read = Mock()
    read.web3.eth.contract.return_value.functions.getCollateralInfo.return_value.call.return_value = (
        209376950,
        112010129,
        112010129,
    )
    assert module.rusd_units(read, 135145) == 112010129


def test_state_persists_identity_and_locks_against_duplicate_run(tmp_path):
    first = state(tmp_path)
    first.data["long_account"] = 2
    first.save()
    with pytest.raises(BlockingIOError):
        state(tmp_path)
    first.close()
    resumed = state(tmp_path)
    assert resumed.data["long_account"] == 2
    resumed.close()
    with pytest.raises(ValueError, match="different owner"):
        module.State(str(tmp_path / "run"), OWNER, 9)


def test_setup_requires_owner_key_even_for_a_core_delegate():
    key = "01" * 32  # Synthetic test key.
    signer = Account.from_key(key)
    read = Mock(owner=OWNER)
    read.web3.eth.get_balance.return_value = 1
    with pytest.raises(ValueError, match="owner wallet key"):
        module.funding_signer(read, 135145, key)
    read.owner = signer.address
    assert module.funding_signer(read, 135145, key).address == signer.address


def test_transact_persists_hash_before_send_and_does_not_rebroadcast_after_timeout(tmp_path):
    saved = state(tmp_path)
    read, function, signer = Mock(), Mock(), Mock(address=OWNER)
    read.web3.eth.get_transaction_count.return_value = 2
    function.estimate_gas.return_value = 100000
    read.web3.eth.gas_price = 1
    read.web3.eth.get_balance.return_value = 1000000
    signer.sign_transaction.return_value.raw_transaction = b"synthetic-raw-transaction"

    def ambiguous_send(_):
        assert saved.data["transactions"]["create_long"]["hash"] in saved.path.read_text()
        raise TimeoutError("ambiguous send")

    read.web3.eth.send_raw_transaction.side_effect = ambiguous_send
    with pytest.raises(TimeoutError):
        module.transact(read, saved, "create_long", function, signer, 1)
    read.web3.eth.wait_for_transaction_receipt.return_value = {"status": 1}
    assert module.transact(read, saved, "create_long", function, signer, 1)["status"] == 1
    read.web3.eth.send_raw_transaction.assert_called_once()
    signer.sign_transaction.assert_called_once()
    assert "synthetic-raw" not in saved.path.read_text()
    saved.close()


def test_reverted_transaction_stops(tmp_path):
    saved = state(tmp_path)
    saved.data["transactions"]["create_long"] = {"hash": "0x123"}
    read = Mock()
    read.web3.eth.wait_for_transaction_receipt.return_value = {"status": 0}
    with pytest.raises(ValueError, match="reverted"):
        module.transact(read, saved, "create_long", Mock(), Mock(), 1)
    read.web3.eth.send_raw_transaction.assert_not_called()
    saved.close()


def test_create_event_filters_spot_and_other_owners():
    read = Mock(owner=OWNER)
    read.core.address = "0x" + "11" * 20
    read.core.events.AccountCreated.return_value.process_receipt.return_value = [
        {"address": read.core.address, "args": {"owner": OWNER, "accountId": 2}},
        {"address": read.core.address, "args": {"owner": OWNER, "accountId": 10**10 + 2}},
        {"address": read.core.address, "args": {"owner": "0x" + "22" * 20, "accountId": 3}},
    ]
    read.assert_owned_perp = Mock()
    assert module.account_from_receipt(read, {}) == 2


def test_setup_creates_two_and_funds_once_then_resumes_without_mutation(tmp_path, monkeypatch):
    saved = state(tmp_path)
    read = Mock(spec=Readiness)
    read.owner = OWNER
    read.core = Mock()
    read.web3 = Mock()
    read.web3.eth.contract.return_value.functions.getCollateralPoolIdOfAccount.return_value.call.return_value = 1
    read.core.decode_function_input.return_value = (None, {"accountId": 135145, "commands": []})
    read.funding_plan.return_value = {"data": "0x1234"}
    args = SimpleNamespace(source_account=135145, timeout=1, activation_market_id=1)
    monkeypatch.setattr(module, "rusd_units", lambda _, account: 112010129 if account == 135145 else 56005064)
    monkeypatch.setattr(module, "account_from_receipt", Mock(side_effect=[2, 3]))
    tx = Mock(return_value={"status": 1})
    monkeypatch.setattr(module, "transact", tx)
    module.setup(read, args, saved, Mock())
    assert [call.args[2] for call in tx.call_args_list] == [
        "create_long",
        "activate_long",
        "create_short",
        "activate_short",
        "fund",
    ]
    read.funding_plan.assert_called_once_with(135145, [2, 3], Decimal("56.005064"))
    assert saved.data["complete"] and saved.data["source_dust_rusd"] == "0.000001"
    saved.data["transactions"]["fund"] = {"hash": "0x123"}
    monkeypatch.setattr(module, "receipt", Mock(return_value={"status": 1}))
    tx.reset_mock()
    module.setup(read, args, saved, None)
    tx.assert_not_called()
    saved.close()


def test_default_setup_never_prompts_or_sends(tmp_path, monkeypatch):
    read = Mock(spec=Readiness)
    read.owner = OWNER
    read.gateway = Mock()
    monkeypatch.setattr(module, "Readiness", Mock(return_value=read))
    monkeypatch.setattr(module, "signer_from_env", Mock(return_value=("secret", OWNER)))
    monkeypatch.setattr(module, "rusd_units", Mock(return_value=100000000))
    prompt, run = Mock(), Mock()
    monkeypatch.setattr(module.getpass, "getpass", prompt)
    monkeypatch.setattr(module, "setup", run)
    args = module.parser().parse_args(["--trade-env-file", "unused.env", "--state-dir", str(tmp_path / "run")])
    module.main(args)
    prompt.assert_not_called()
    run.assert_not_called()
    assert not (tmp_path / "run").exists()
