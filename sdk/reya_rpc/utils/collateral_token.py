"""Resolution of the collateral token used by margin-account actions.

There is deliberately no on-chain check that the token is the one the account actually uses as
collateral. Neither of the reads core offers can express that safely today, both observed
against mainnet core 0xA763B6a5E09378434406C003daE6487FbbDc1a80:

- ``getUsdNodeMarginInfo(accountId).collateral`` came back as the zero address for accounts 2
  and 1000, both of which hold balances, so a comparison against it would never fire. It reads
  as an aggregate folded from a zero-initialised accumulator whose ``collateral`` is never
  assigned, which would make the zero universal rather than particular to those two accounts.
- ``getNodeMarginInfo(accountId, token)`` does discriminate - on account 1000 it returned rUSD
  for rUSD and reverted 0x96f103cd for USDC and for an unrelated token - but that selector is
  ``CollateralIsNotQuote``, raised for any collateral that is not the pool's *quote*. Supporting
  collaterals are legitimate, margin-contributing, and non-quote, so they revert identically to
  a wholly wrong asset and rejecting on the revert would block valid deposits. This function is
  also absent from the trimmed ``abis/CoreProxy.json``; it was called out-of-band.

Until a read separates those cases, the caller names the token and owns the choice.
"""

from typing import Optional, cast

import json
import os

from web3 import Web3
from web3.contract import Contract

from sdk.reya_rpc.exceptions import InvalidTokenAddressError

_ABIS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "abis")

with open(os.path.join(_ABIS_DIR, "Erc20.json"), encoding="utf-8") as _f:
    erc20_abi = json.load(_f)


def resolve_collateral_token(config: dict, token_address: Optional[str] = None) -> Contract:
    """
    Resolve the ERC-20 contract a margin-account action should move.

    Defaults to the rUSD instance in ``config``, which is what every caller got before
    ``token_address`` existed. Deployments whose collateral is a different address pass it
    explicitly rather than silently moving the wrong asset.

    Args:
        config (dict): Configuration dictionary containing Web3 contract instances.
        token_address (Optional[str]): Token to use. Defaults to the configured rUSD.

    Returns:
        Contract: The ERC-20 contract instance to deposit or withdraw.

    Raises:
        InvalidTokenAddressError: If ``token_address`` is not a valid Ethereum address.
    """
    if token_address is None:
        return cast(Contract, config["w3contracts"]["rusd"])

    try:
        checksummed = Web3.to_checksum_address(token_address)
    except (ValueError, TypeError) as error:
        raise InvalidTokenAddressError(f"'{token_address}' is not a valid token address.") from error

    return cast(Contract, config["w3"].eth.contract(address=checksummed, abi=erc20_abi))
