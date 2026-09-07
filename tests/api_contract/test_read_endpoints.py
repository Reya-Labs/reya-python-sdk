"""Read-endpoint contract: the snapshot paths a UI hits on load.

These broke twice on devnet in one day and the suite stayed green both
times:

  * ``/v2/assetDefinitions`` 500'd for every caller, because a collateral
    registered on-chain had no asset identity off-chain.
  * the WS ``accountBalances`` snapshot failed for a wallet holding that
    collateral, while REST was already fixed -- the websocket builds the
    snapshot itself rather than proxying REST.

Neither was caught, for two independent reasons this module addresses:

1. Nothing asserted the WS snapshot SUCCEEDS. The harness subscribes to
   the wallet channels and only ever reads the data stores, so a server
   error frame was dropped silently for the whole session.
2. The test wallets hold rUSD and wETH only. The failure needs a wallet
   holding the collateral that cannot be resolved, so the path was never
   executed regardless of assertions.

(1) is fixed here and in the WS harness. (2) cannot be fixed by asserting
harder -- it needs a wallet that holds the asset -- so the per-asset test
below SKIPS with the asset named, making the hole visible in the report
instead of leaving a green run that proves less than it appears to.
"""

import pytest

from tests.helpers import ReyaTester

pytestmark = [pytest.mark.rest_api, pytest.mark.balance]


async def _asset_definitions(tester: ReyaTester):
    return await tester.client.reference.get_asset_definitions()


async def test_asset_definitions_resolves(reya_tester: ReyaTester):
    """Every configured collateral must render. A collateral with no asset
    identity used to take the whole endpoint down with it."""
    definitions = await _asset_definitions(reya_tester)
    assert definitions, "assetDefinitions returned nothing"
    for d in definitions:
        assert d.asset, f"asset definition without an asset: {d}"
        assert d.decimals is not None, f"{d.asset} has no decimals"


async def test_wallet_balances_rest_resolves(reya_tester: ReyaTester):
    """REST balances must resolve every collateral the wallet holds."""
    balances = await reya_tester.client.get_account_balances()
    for b in balances:
        assert b.asset, f"balance row without an asset: {b}"
        assert b.real_balance is not None, f"{b.asset} balance did not resolve"


async def test_wallet_balances_ws_snapshot_has_no_error_frame(reya_tester: ReyaTester):
    """The WS wallet channels must not answer a subscribe with an error.

    The session fixture already subscribes to the wallet channels, so by
    the time this runs any snapshot failure has already been reported --
    and, before the harness captured error frames, silently discarded.
    """
    errors = [e for e in reya_tester.ws.errors if "/wallet/" in (getattr(e, "channel", None) or "")]
    assert not errors, "WS wallet channels returned error frames: " + "; ".join(
        f"{e.channel}: {e.message}" for e in errors
    )


async def test_every_configured_asset_is_exercised_by_some_balance(
    reya_tester: ReyaTester,
):
    """Name the assets this suite cannot vouch for.

    The balance transform runs per held collateral, so an asset the test
    wallet does not hold is never exercised no matter how many assertions
    run. Rather than pass quietly, report which assets are uncovered --
    that list is exactly the blind spot that let both incidents through.
    """
    definitions = await _asset_definitions(reya_tester)
    configured = {str(d.asset).upper() for d in definitions}
    balances = await reya_tester.client.get_account_balances()
    held = {str(b.asset).upper() for b in balances}

    uncovered = sorted(configured - held)
    if uncovered:
        pytest.skip(
            "test wallet holds none of: "
            + ", ".join(uncovered)
            + " -- their balance/snapshot path is NOT exercised by this suite. "
            "Fund a test account with them to close the gap."
        )
