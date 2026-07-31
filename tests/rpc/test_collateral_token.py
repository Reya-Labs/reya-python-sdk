"""Tests for collateral-token selection in deposit/withdraw."""

from importlib import import_module
from unittest.mock import MagicMock

import pytest
from eth_abi import encode

from sdk.reya_rpc.actions.deposit import DepositParams, deposit
from sdk.reya_rpc.actions.withdraw import WithdrawParams, withdraw
from sdk.reya_rpc.exceptions import InvalidTokenAddressError
from sdk.reya_rpc.utils.collateral_token import resolve_collateral_token

pytestmark = pytest.mark.offline

# actions/__init__.py re-exports these names, shadowing the submodules, so string-path
# monkeypatching would resolve to the functions. Grab the module objects instead.
deposit_module = import_module("sdk.reya_rpc.actions.deposit")
withdraw_module = import_module("sdk.reya_rpc.actions.withdraw")

RUSD_ADDRESS = "0x9DE724e7b3facF87Ce39465D3D712717182e3e55"
LOCAL_COLLATERAL_ADDRESS = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
CORE_ADDRESS = "0xC6fB022962e1426F4e0ec9D2F8861c57926E9f72"
ACCOUNT_ID = 42
MALFORMED_ADDRESS = "not-an-address"
AMOUNT = 100_000_000


def _token_contract(address: str) -> MagicMock:
    """An ERC-20 contract stub."""
    token = MagicMock()
    token.address = address
    return token


class _Harness:
    """A config dict plus handles on every token stub the code under test can reach."""

    def __init__(self):
        self.rusd = _token_contract(RUSD_ADDRESS)
        self.tokens = {RUSD_ADDRESS: self.rusd}

        core = MagicMock()
        core.address = CORE_ADDRESS

        self.w3 = MagicMock()
        self.w3.eth.contract.side_effect = self._mint

        self.config: dict = {
            "w3": self.w3,
            "w3account": MagicMock(address="0x000000000000000000000000000000000000dEaD"),
            "w3contracts": {"core": core, "rusd": self.rusd},
        }
        self.captured: dict = {}

    def _mint(self, address: str, **_kwargs) -> MagicMock:
        return self.tokens.setdefault(address, _token_contract(address))

    def token(self, address: str) -> MagicMock:
        """The stub for an address, minting it so a test can assert it was never touched."""
        return self.tokens.setdefault(address, _token_contract(address))

    def recorder(self):
        """Stand in for execute_core_commands: record commands, return a receipt-shaped stub."""

        def execute(_config: dict, _account_id: int, commands: list):
            self.captured["commands"] = commands
            return {"transactionHash": MagicMock()}

        return execute

    def explode(self):
        """Stand in for execute_core_commands when the test asserts it is never reached."""

        def execute(_config: dict, _account_id: int, _commands: list):
            pytest.fail("execute_core_commands must not run when the token address is rejected")

        return execute

    def encoded(self, address: str, amount: int = AMOUNT) -> bytes:
        return encode(["(address,uint256)"], [[address, amount]])


class TestResolveCollateralToken:
    """Token selection and address validation."""

    def test_defaults_to_configured_rusd(self):
        harness = _Harness()

        token = resolve_collateral_token(harness.config)

        assert token is harness.rusd
        harness.w3.eth.contract.assert_not_called()

    def test_explicit_address_overrides_the_default(self):
        harness = _Harness()

        token = resolve_collateral_token(harness.config, LOCAL_COLLATERAL_ADDRESS)

        assert token.address == LOCAL_COLLATERAL_ADDRESS
        assert token is not harness.rusd

    def test_lowercase_address_is_checksummed(self):
        harness = _Harness()

        token = resolve_collateral_token(harness.config, LOCAL_COLLATERAL_ADDRESS.lower())

        assert token.address == LOCAL_COLLATERAL_ADDRESS

    def test_explicit_token_is_built_with_the_erc20_abi(self):
        harness = _Harness()

        resolve_collateral_token(harness.config, LOCAL_COLLATERAL_ADDRESS)

        abi = harness.w3.eth.contract.call_args.kwargs["abi"]
        assert {entry.get("name") for entry in abi} >= {"approve", "decimals", "balanceOf"}

    @pytest.mark.parametrize("bad", [MALFORMED_ADDRESS, "0x1234", ""])
    def test_malformed_address_raises_the_named_error(self, bad):
        harness = _Harness()

        with pytest.raises(InvalidTokenAddressError, match="not a valid token address"):
            resolve_collateral_token(harness.config, bad)


class TestDeposit:
    """deposit() approves and encodes the resolved token."""

    def test_default_path_is_unchanged(self, monkeypatch):
        harness = _Harness()
        monkeypatch.setattr(deposit_module, "execute_core_commands", harness.recorder())

        deposit(harness.config, DepositParams(account_id=ACCOUNT_ID, amount=AMOUNT))

        harness.rusd.functions.approve.assert_called_once_with(CORE_ADDRESS, AMOUNT)
        assert harness.captured["commands"][0][1] == harness.encoded(RUSD_ADDRESS)

    def test_explicit_token_is_approved_and_encoded(self, monkeypatch):
        harness = _Harness()
        monkeypatch.setattr(deposit_module, "execute_core_commands", harness.recorder())

        deposit(
            harness.config,
            DepositParams(account_id=ACCOUNT_ID, amount=AMOUNT, token_address=LOCAL_COLLATERAL_ADDRESS),
        )

        harness.token(LOCAL_COLLATERAL_ADDRESS).functions.approve.assert_called_once_with(CORE_ADDRESS, AMOUNT)
        harness.rusd.functions.approve.assert_not_called()
        assert harness.captured["commands"][0][1] == harness.encoded(LOCAL_COLLATERAL_ADDRESS)

    def test_bad_address_approves_nothing_and_executes_nothing(self, monkeypatch):
        """Fails if resolution is ever reordered after the approve."""
        harness = _Harness()
        monkeypatch.setattr(deposit_module, "execute_core_commands", harness.explode())

        with pytest.raises(InvalidTokenAddressError):
            deposit(
                harness.config, DepositParams(account_id=ACCOUNT_ID, amount=AMOUNT, token_address=MALFORMED_ADDRESS)
            )

        harness.rusd.functions.approve.assert_not_called()


class TestWithdraw:
    """withdraw() mirrors deposit(), and is the recovery path for a non-default token."""

    def test_default_path_is_unchanged(self, monkeypatch):
        harness = _Harness()
        monkeypatch.setattr(withdraw_module, "execute_core_commands", harness.recorder())

        withdraw(harness.config, WithdrawParams(account_id=ACCOUNT_ID, amount=AMOUNT))

        assert harness.captured["commands"][0][1] == harness.encoded(RUSD_ADDRESS)
        harness.rusd.functions.approve.assert_not_called()

    def test_explicit_token_is_encoded(self, monkeypatch):
        harness = _Harness()
        monkeypatch.setattr(withdraw_module, "execute_core_commands", harness.recorder())

        withdraw(
            harness.config,
            WithdrawParams(account_id=ACCOUNT_ID, amount=AMOUNT, token_address=LOCAL_COLLATERAL_ADDRESS),
        )

        assert harness.captured["commands"][0][1] == harness.encoded(LOCAL_COLLATERAL_ADDRESS)

    def test_bad_address_executes_nothing(self, monkeypatch):
        harness = _Harness()
        monkeypatch.setattr(withdraw_module, "execute_core_commands", harness.explode())

        with pytest.raises(InvalidTokenAddressError):
            withdraw(
                harness.config, WithdrawParams(account_id=ACCOUNT_ID, amount=AMOUNT, token_address=MALFORMED_ADDRESS)
            )
