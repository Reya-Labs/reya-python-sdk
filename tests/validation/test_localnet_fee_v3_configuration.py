"""Offline checks that referral configuration is restored on success and failure."""

from unittest.mock import MagicMock

import pytest

from tests.helpers import localnet_fee_v3 as fees

pytestmark = pytest.mark.offline

TAKER = "0x0000000000000000000000000000000000000001"
REFERRER = "0x0000000000000000000000000000000000000002"
ZERO = "0x0000000000000000000000000000000000000000"


@pytest.mark.parametrize("failure", [None, "body", "resolution", "mapping"])
def test_referral_configuration_restores_state(monkeypatch, failure):
    for name, value in {
        "CHAIN_ID": "31337",
        "NEXT_PUBLIC_LOCALNET_RPC_URL": "http://127.0.0.1:8545",
        "PASSIVE_PERP_PROXY_ADDRESS": REFERRER,
        "PERP_PRIVATE_KEY_2": "test-only-mocked-key",
    }.items():
        monkeypatch.setenv(name, value)
    w3 = MagicMock()
    w3.eth.chain_id = 31337
    w3.eth.account.from_key.return_value.address = TAKER
    functions = w3.eth.contract.return_value.functions
    functions.getAccountOwnerFeeConfiguration.return_value.call.return_value = (0, False, False, False, False, 123)
    functions.getFeeTierParameters.return_value.call.return_value = (10**15, 0, 0)
    functions.getGlobalFeeParameters.return_value.call.return_value = (2 * 10**17, 0, 0, 0, 0, 0, 25 * 10**16, 1)
    functions.getReferrerAccountOwner.return_value.call.return_value = ZERO
    functions.getAccountOwnerFeeParameters.return_value.call.return_value = (10**15, 2 * 10**17)
    functions.getReferrerRebateParameter.return_value.call.return_value = (3 * 10**17, 10**10)
    if failure == "resolution":
        functions.getReferrerRebateParameter.return_value.call.return_value = (3 * 10**17, 99)

    real_web3 = fees.Web3
    mock_web3 = MagicMock(return_value=w3)
    mock_web3.to_checksum_address = real_web3.to_checksum_address
    monkeypatch.setattr(fees, "Web3", mock_web3)
    sent = []

    def send(_w3, _key, function):
        sent.append(function)
        if failure == "mapping" and function is functions.setReferralMapping.return_value and len(sent) == 4:
            raise RuntimeError("mapping reverted")

    monkeypatch.setattr(fees, "_send_transaction", send)

    def run():
        with fees.configured_localnet_fee_v3(
            taker_owner=TAKER, pool_account_id=1, referrer_owner=REFERRER, referrer_account_id=10**10
        ) as scenario:
            assert scenario is not None and scenario.referrer_rebate_rate == 3 * 10**17
            if failure == "body":
                raise RuntimeError("fill assertion failed")

    if failure:
        with pytest.raises(RuntimeError):
            run()
    else:
        run()
    functions.setAccountOwnerCustomAffiliateRateFeeConfig.assert_called_with(REFERRER, 123)
    functions.setAccountOwnerOgStatusFeeConfig.assert_called_with(TAKER, False)
    functions.setAccountOwnerTierIdFeeConfig.assert_called_with(TAKER, 0)
    if failure != "mapping":
        functions.setReferralMapping.assert_called_with(TAKER, ZERO)
    # These checks also prove the restoration calls were actually submitted.
    assert sent[-3:] == [
        functions.setAccountOwnerCustomAffiliateRateFeeConfig.return_value,
        functions.setAccountOwnerOgStatusFeeConfig.return_value,
        functions.setAccountOwnerTierIdFeeConfig.return_value,
    ]
    assert len(sent) == (7 if failure == "mapping" else 8)
