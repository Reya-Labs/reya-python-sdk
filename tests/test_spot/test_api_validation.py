"""
Tests for API server validation checks in the matching-engine controller.

These tests verify that the API properly validates:
- Signature validity (EIP-712 signature verification)
- Nonce validity (monotonically increasing)
- Deadline validity (not expired)
- Balance validity (IOC orders)
- Price/Qty step size validity

High and Medium priority validation tests for spot market orders.
"""

import asyncio
import time
from decimal import Decimal

import aiohttp
import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.cancel_order_request import CancelOrderRequest
from sdk.open_api.models.create_order_request import CreateOrderRequest
from sdk.open_api.models.mass_cancel_request import MassCancelRequest
from sdk.open_api.models.order_type import OrderType
from sdk.open_api.models.time_in_force import TimeInForce
from sdk.reya_rest_api.auth.signatures import SignatureGenerator
from sdk.reya_rest_api.config import TradingConfig
from tests.helpers import ReyaTester
from tests.helpers.builders import OrderBuilder
from tests.helpers.reya_tester import logger
from tests.test_spot.spot_config import SpotTestConfig

# SIGNATURE VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_invalid_signature(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with an invalid signature is rejected.

    The API should verify the EIP-712 signature and reject orders
    where the signature doesn't match the order data.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER INVALID SIGNATURE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Create a valid order request but with a tampered signature
    order_price = spot_config.price(0.96)
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    # Use a completely fake signature (valid format but wrong data)
    fake_signature = "0x" + "ab" * 65  # 65 bytes = r(32) + s(32) + v(1)

    order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=deadline,
        reduceOnly=None,
        signature=fake_signature,
        nonce=str(nonce),
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info("Sending order with invalid signature...")

    try:
        response = await spot_tester.client.orders.create_order(create_order_request=order_request)
        pytest.fail(f"Order with invalid signature should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CREATE_ORDER_OTHER_ERROR message='Invalid signature'
        assert "CREATE_ORDER_OTHER_ERROR" in error_msg, f"Expected CREATE_ORDER_OTHER_ERROR, got: {e}"
        assert "Invalid signature" in error_msg, f"Expected 'Invalid signature' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER INVALID SIGNATURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_wrong_signer(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order signed by a different wallet is rejected.

    The API should verify that the signer has permission to trade
    on the specified account.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER WRONG SIGNER TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Create a different private key for signing
    wrong_private_key = "0x" + "12" * 32  # A different private key

    # Create config with different private key
    # Use the same config values but with a different private key
    wrong_config = TradingConfig(
        api_url=spot_tester.client.config.api_url,
        chain_id=spot_tester.chain_id,
        owner_wallet_address=spot_tester.client.config.owner_wallet_address,
        private_key=wrong_private_key,
        account_id=spot_tester.account_id,
    )
    wrong_signer = SignatureGenerator(wrong_config)

    order_price = spot_config.price(0.96)
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    # Sign with the wrong private key
    inputs = wrong_signer.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(str(order_price)),
        qty=Decimal(spot_config.min_qty),
    )

    signature = wrong_signer.sign_raw_order(
        account_id=spot_tester.account_id,  # Same account
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,  # LIMIT_ORDER_SPOT
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=deadline,
        reduceOnly=None,
        signature=signature,
        nonce=str(nonce),
        signerWallet=wrong_signer.signer_wallet_address,  # Wrong signer
    )

    logger.info(f"Sending order signed by wrong wallet: {wrong_signer.signer_wallet_address}")

    try:
        response = await spot_tester.client.orders.create_order(create_order_request=order_request)
        pytest.fail(f"Order from unauthorized signer should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # Expect either:
        # - CREATE_ORDER_OTHER_ERROR with 'Invalid signature' (signature validation fails), or
        # - CANCEL_ORDER_OTHER_ERROR with 'Unauthorized: signer does not have permission' (permission check fails)
        has_valid_error = ("CREATE_ORDER_OTHER_ERROR" in error_msg and "Invalid signature" in error_msg) or (
            "Unauthorized: signer does not have permission" in error_msg
        )
        assert has_valid_error, f"Expected signature or permission error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER WRONG SIGNER TEST COMPLETED")


# ============================================================================
# DEADLINE VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_expired_deadline(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with an expired deadline is rejected.

    The API should check that deadline > current time.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER EXPIRED DEADLINE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    order_price = spot_config.price(0.96)
    # Set deadline in the past
    expired_deadline = int(time.time()) - 60  # 1 minute ago (in seconds)
    nonce = spot_tester.get_next_nonce()

    # Get signature generator from client
    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(str(order_price)),
        qty=Decimal(spot_config.min_qty),
    )

    signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,  # LIMIT_ORDER_SPOT
        inputs=inputs,
        deadline=expired_deadline,
        nonce=nonce,
    )

    order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=expired_deadline,
        reduceOnly=None,
        signature=signature,
        nonce=str(nonce),
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info(f"Sending order with expired deadline: {expired_deadline}")

    try:
        response = await spot_tester.client.orders.create_order(create_order_request=order_request)
        pytest.fail(f"Order with expired deadline should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # API should reject with 400 error - check for deadline-related keywords
        assert (
            "400" in error_msg or "deadline" in error_msg.lower() or "expired" in error_msg.lower()
        ), f"Expected deadline rejection error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER EXPIRED DEADLINE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_cancel_expired_deadline(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a cancel request with an expired deadline is rejected.
    """
    logger.info("=" * 80)
    logger.info("SPOT CANCEL EXPIRED DEADLINE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # First create a valid order
    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(spot_config.price(0.96)))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    order_id = await spot_tester.orders.create_limit(order_params)
    assert order_id is not None, "Order creation should return order_id for GTC orders"
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"Created order: {order_id}")

    # Now try to cancel with expired deadline
    expired_deadline = int(time.time()) - 60  # 1 minute ago (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator
    signature = sig_gen.sign_cancel_order_spot(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        order_id=int(order_id),
        client_order_id=0,
        nonce=nonce,
        deadline=expired_deadline,
    )

    cancel_request = CancelOrderRequest(
        orderId=order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=expired_deadline,
    )

    logger.info(f"Sending cancel with expired deadline: {expired_deadline}")

    try:
        response = await spot_tester.client.orders.cancel_order(cancel_order_request=cancel_request)
        pytest.fail(f"Cancel with expired deadline should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # API should reject with 400 error - check for deadline-related keywords
        assert (
            "400" in error_msg or "deadline" in error_msg.lower() or "expired" in error_msg.lower()
        ), f"Expected deadline rejection error, got: {e}"
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    # Clean up - cancel with valid request
    await spot_tester.client.cancel_order(
        order_id=order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT CANCEL EXPIRED DEADLINE TEST COMPLETED")


# ============================================================================
# NONCE VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_reused_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that reusing a nonce is rejected.

    Nonces must be monotonically increasing to prevent replay attacks.
    First sends a valid order with a specific nonce, then tries to reuse that nonce.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER REUSED NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    order_price = spot_config.price(0.96)

    # Step 1: Create a valid order with a specific nonce to establish it
    first_nonce = spot_tester.get_next_nonce()
    first_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(str(order_price)),
        qty=Decimal(spot_config.min_qty),
    )

    first_signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=first_deadline,
        nonce=first_nonce,
    )

    first_order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=first_deadline,
        reduceOnly=None,
        signature=first_signature,
        nonce=str(first_nonce),
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info(f"Step 1: Sending first order with nonce: {first_nonce}")
    first_response = await spot_tester.client.orders.create_order(create_order_request=first_order_request)
    logger.info(f"✅ First order created: {first_response.order_id}")

    # Cancel the first order to clean up
    await spot_tester.client.cancel_order(
        order_id=first_response.order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)

    # Step 2: Try to reuse the same nonce - should fail
    reused_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    reused_signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=reused_deadline,
        nonce=first_nonce,  # Reuse the same nonce
    )

    reused_order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=reused_deadline,
        reduceOnly=None,
        signature=reused_signature,
        nonce=str(first_nonce),  # Reuse the same nonce
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info(f"Step 2: Sending order with reused nonce: {first_nonce}")

    try:
        response = await spot_tester.client.orders.create_order(create_order_request=reused_order_request)
        pytest.fail(f"Order with reused nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for nonce-related keywords
        assert (
            "400" in str(e) or "nonce" in error_msg or "invalid" in error_msg
        ), f"Expected nonce rejection error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER REUSED NONCE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_old_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that using an old nonce (nonce - 1) is rejected.

    Nonces must be monotonically increasing to prevent replay attacks.
    First sends a valid order with a specific nonce, then tries nonce-1.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER OLD NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    order_price = spot_config.price(0.96)

    # Step 1: Create a valid order with a specific nonce to establish it
    first_nonce = spot_tester.get_next_nonce()
    first_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(str(order_price)),
        qty=Decimal(spot_config.min_qty),
    )

    first_signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=first_deadline,
        nonce=first_nonce,
    )

    first_order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=first_deadline,
        reduceOnly=None,
        signature=first_signature,
        nonce=str(first_nonce),
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info(f"Step 1: Sending first order with nonce: {first_nonce}")
    first_response = await spot_tester.client.orders.create_order(create_order_request=first_order_request)
    logger.info(f"✅ First order created: {first_response.order_id}")

    # Cancel the first order to clean up
    await spot_tester.client.cancel_order(
        order_id=first_response.order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)

    # Step 2: Try to use nonce - 1 - should fail
    old_nonce = first_nonce - 1
    old_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    old_signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=old_deadline,
        nonce=old_nonce,  # Use nonce - 1
    )

    old_order_request = CreateOrderRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        exchangeId=spot_tester.client.config.dex_id,
        isBuy=True,
        limitPx=str(order_price),
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        expiresAfter=old_deadline,
        reduceOnly=None,
        signature=old_signature,
        nonce=str(old_nonce),  # Use nonce - 1
        signerWallet=spot_tester.client.signer_wallet_address,
    )

    logger.info(f"Step 2: Sending order with old nonce (nonce-1): {old_nonce}")

    try:
        response = await spot_tester.client.orders.create_order(create_order_request=old_order_request)
        pytest.fail(f"Order with old nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for nonce-related keywords
        assert (
            "400" in str(e) or "nonce" in error_msg or "invalid" in error_msg
        ), f"Expected nonce rejection error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER OLD NONCE TEST COMPLETED")


# ============================================================================
# BALANCE VALIDATION TESTS (IOC ORDERS)
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_ioc_insufficient_balance_buy(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an IOC buy order exceeding RUSD balance is rejected.

    IOC orders have pre-trade balance validation to prevent failed executions.
    Gets the actual RUSD balance and tries to exceed it by a small amount.
    """
    logger.info("=" * 80)
    logger.info("SPOT IOC INSUFFICIENT BALANCE (BUY) TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Get the actual RUSD balance for this account
    balances = await spot_tester.client.get_account_balances()
    rusd_balance = None
    for b in balances:
        if b.account_id == spot_tester.account_id and b.asset == "RUSD":
            rusd_balance = Decimal(b.real_balance)
            break

    if rusd_balance is None or rusd_balance <= 0:
        pytest.skip("No RUSD balance available for this test")

    logger.info(f"Current RUSD balance: {rusd_balance}")

    # Calculate qty that would require slightly more RUSD than available
    # At spot_config.oracle_price, we need (rusd_balance / price) + small_extra ETH
    order_price = Decimal(str(spot_config.oracle_price))
    max_qty_at_price = rusd_balance / order_price
    # Request 10% more than we can afford
    exceeding_qty = str((max_qty_at_price * Decimal("1.1")).quantize(Decimal("0.01")))

    order_params = (
        OrderBuilder().symbol(spot_config.symbol).buy().price(str(order_price)).qty(exceeding_qty).ioc().build()
    )

    required_rusd = Decimal(exceeding_qty) * order_price
    logger.info(f"Sending IOC buy for {exceeding_qty} ETH @ ${order_price}")
    logger.info(f"Required RUSD: {required_rusd}, Available: {rusd_balance}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        pytest.fail(f"Order exceeding balance should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CREATE_ORDER_OTHER_ERROR message='Insufficient balance: required X, available Y'
        assert "CREATE_ORDER_OTHER_ERROR" in error_msg, f"Expected CREATE_ORDER_OTHER_ERROR, got: {e}"
        assert "Insufficient balance" in error_msg, f"Expected 'Insufficient balance' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT IOC INSUFFICIENT BALANCE (BUY) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_ioc_insufficient_balance_sell(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an IOC sell order exceeding base asset balance is rejected.

    IOC orders have pre-trade balance validation to prevent failed executions.
    Gets the actual base asset balance and tries to exceed it by a small amount.
    """
    logger.info("=" * 80)
    logger.info("SPOT IOC INSUFFICIENT BALANCE (SELL) TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Get the actual base asset balance for this account
    base_asset = spot_config.base_asset
    balances = await spot_tester.client.get_account_balances()
    asset_balance = None
    for b in balances:
        if b.account_id == spot_tester.account_id and b.asset == base_asset:
            asset_balance = Decimal(b.real_balance)
            break

    if asset_balance is None or asset_balance <= 0:
        pytest.skip(f"No {base_asset} balance available for this test")

    logger.info(f"Current {base_asset} balance: {asset_balance}")

    # Request 10% more than we have, quantized to qty_step_size
    qty_step = Decimal(spot_config.qty_step_size) if hasattr(spot_config, "qty_step_size") else Decimal("0.01")
    exceeding_qty = str((asset_balance * Decimal("1.1")).quantize(qty_step))
    # Round price to tick size
    order_price = str(spot_config.price(1.0))

    order_params = OrderBuilder().symbol(spot_config.symbol).sell().price(order_price).qty(exceeding_qty).ioc().build()

    logger.info(f"Sending IOC sell for {exceeding_qty} {base_asset} @ ${order_price}")
    logger.info(f"Required {base_asset}: {exceeding_qty}, Available: {asset_balance}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        pytest.fail(f"Order exceeding balance should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CREATE_ORDER_OTHER_ERROR message='Insufficient balance: required X, available Y'
        assert "CREATE_ORDER_OTHER_ERROR" in error_msg, f"Expected CREATE_ORDER_OTHER_ERROR, got: {e}"
        assert "Insufficient balance" in error_msg, f"Expected 'Insufficient balance' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT IOC INSUFFICIENT BALANCE (SELL) TEST COMPLETED")


# ============================================================================
# PRICE/QTY STEP SIZE VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_qty_below_minimum(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with quantity below minimum is rejected.

    Each market has a minimum order base (e.g., 0.001 for WETHRUSD, 0.0001 for WBTCRUSD).
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER QTY BELOW MINIMUM TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Calculate a quantity below the minimum by using half of min_qty
    min_qty = Decimal(spot_config.min_qty)
    tiny_qty = str(min_qty / Decimal("2"))
    order_price = str(spot_config.price(0.96))

    logger.info(f"Market min_qty: {spot_config.min_qty}, using qty: {tiny_qty}")

    order_params = OrderBuilder().symbol(spot_config.symbol).buy().price(order_price).qty(tiny_qty).gtc().build()

    logger.info(f"Sending order with qty below minimum: {tiny_qty}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        # If accepted, clean up
        await spot_tester.client.cancel_order(
            order_id=order_id,
            symbol=spot_config.symbol,
            account_id=spot_tester.account_id,
        )
        await asyncio.sleep(0.05)
        pytest.fail(f"Order with qty below minimum should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CREATE_ORDER_OTHER_ERROR message='Order quantity X is below minimum order base Y'
        assert "CREATE_ORDER_OTHER_ERROR" in error_msg, f"Expected CREATE_ORDER_OTHER_ERROR, got: {e}"
        assert "is below minimum order base" in error_msg, f"Expected 'is below minimum order base' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER QTY BELOW MINIMUM TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_qty_not_step_multiple(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with quantity not a multiple of step size is rejected.

    Quantities must be multiples of the market's base step size.
    Note: This test uses a quantity with excessive decimal places (0.0123456789)
    that is unlikely to be a valid step multiple for any market.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER QTY NOT STEP MULTIPLE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Use a quantity with excessive decimal places that won't be a step multiple
    non_step_qty = "0.0123456789"
    order_price = str(spot_config.price(0.96))

    order_params = OrderBuilder().symbol(spot_config.symbol).buy().price(order_price).qty(non_step_qty).gtc().build()

    logger.info(f"Sending order with non-step-multiple qty: {non_step_qty}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        # If accepted, clean up
        await spot_tester.client.cancel_order(
            order_id=order_id,
            symbol=spot_config.symbol,
            account_id=spot_tester.account_id,
        )
        await asyncio.sleep(0.05)
        pytest.fail(f"Order with non-step qty should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CREATE_ORDER_OTHER_ERROR message='Order quantity X does not conform to base spacing Y'
        assert "CREATE_ORDER_OTHER_ERROR" in error_msg, f"Expected CREATE_ORDER_OTHER_ERROR, got: {e}"
        assert (
            "does not conform to base spacing" in error_msg
        ), f"Expected 'does not conform to base spacing' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER QTY NOT STEP MULTIPLE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_price_not_tick_multiple(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with price not a multiple of tick size is rejected.

    Prices must be multiples of the market's tick size.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER PRICE NOT TICK MULTIPLE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Use a price with too many decimal places (beyond tick precision)
    # Most markets have tick size like 0.01, so 0.001 precision would be invalid
    non_tick_price = "250.123456789"

    order_params = (
        OrderBuilder().symbol(spot_config.symbol).buy().price(non_tick_price).qty(spot_config.min_qty).gtc().build()
    )

    logger.info(f"Sending order with non-tick-multiple price: {non_tick_price}")

    try:
        order_id = await spot_tester.orders.create_limit(order_params)
        # If accepted, clean up
        await spot_tester.client.cancel_order(
            order_id=order_id,
            symbol=spot_config.symbol,
            account_id=spot_tester.account_id,
        )
        await asyncio.sleep(0.05)
        pytest.fail(f"Order with non-tick-multiple price should have been rejected, got: {order_id}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CREATE_ORDER_OTHER_ERROR message='Order price X does not conform to price spacing Y'
        assert "CREATE_ORDER_OTHER_ERROR" in error_msg, f"Expected CREATE_ORDER_OTHER_ERROR, got: {e}"
        assert (
            "does not conform to price spacing" in error_msg
        ), f"Expected 'does not conform to price spacing' message, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT ORDER PRICE NOT TICK MULTIPLE TEST COMPLETED")


# ============================================================================
# CANCEL ORDER VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_cancel_invalid_signature(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a cancel request with an invalid signature is rejected.
    """
    logger.info("=" * 80)
    logger.info("SPOT CANCEL INVALID SIGNATURE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # First create a valid order
    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(spot_config.price(0.96)))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"Created order: {order_id}")

    # Try to cancel with invalid signature
    fake_signature = "0x" + "cd" * 65
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    cancel_request = CancelOrderRequest(
        orderId=order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=fake_signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )

    logger.info("Sending cancel with invalid signature...")

    try:
        response = await spot_tester.client.orders.cancel_order(cancel_order_request=cancel_request)
        pytest.fail(f"Cancel with invalid signature should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: error=CANCEL_ORDER_OTHER_ERROR message='Invalid signature: unable to recover signer from signature'
        assert "CANCEL_ORDER_OTHER_ERROR" in error_msg, f"Expected CANCEL_ORDER_OTHER_ERROR, got: {e}"
        assert (
            "Invalid signature: unable to recover signer from signature" in error_msg
        ), f"Expected 'Invalid signature: unable to recover signer from signature' message, got: {e}"
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    # Clean up - cancel with valid request
    await spot_tester.client.cancel_order(
        order_id=order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT CANCEL INVALID SIGNATURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_cancel_reused_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a cancel request with a reused nonce is rejected.

    First creates and cancels an order with a specific nonce, then tries to
    reuse that nonce for another cancel.
    """
    logger.info("=" * 80)
    logger.info("SPOT CANCEL REUSED NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Step 1: Create first order and cancel it with a specific nonce
    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(spot_config.price(0.96)))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    first_order_id = await spot_tester.orders.create_limit(order_params)
    assert first_order_id is not None, "Order creation should return order_id for GTC orders"
    await spot_tester.wait.for_order_creation(first_order_id)
    logger.info(f"Step 1: Created first order: {first_order_id}")

    first_nonce = spot_tester.get_next_nonce()
    first_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    sig_gen = spot_tester.client.signature_generator
    first_signature = sig_gen.sign_cancel_order_spot(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        order_id=int(first_order_id),
        client_order_id=0,
        nonce=first_nonce,
        deadline=first_deadline,
    )

    first_cancel_request = CancelOrderRequest(
        orderId=first_order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=first_signature,
        nonce=str(first_nonce),
        expiresAfter=first_deadline,
    )

    logger.info(f"Step 1: Cancelling first order with nonce: {first_nonce}")
    await spot_tester.client.orders.cancel_order(cancel_order_request=first_cancel_request)
    await asyncio.sleep(0.1)
    logger.info("✅ First cancel succeeded")

    # Step 2: Create second order and try to cancel with reused nonce
    second_order_id = await spot_tester.orders.create_limit(order_params)
    assert second_order_id is not None, "Order creation should return order_id for GTC orders"
    await spot_tester.wait.for_order_creation(second_order_id)
    logger.info(f"Step 2: Created second order: {second_order_id}")

    reused_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    reused_signature = sig_gen.sign_cancel_order_spot(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        order_id=int(second_order_id),
        client_order_id=0,
        nonce=first_nonce,  # Reuse the same nonce
        deadline=reused_deadline,
    )

    reused_cancel_request = CancelOrderRequest(
        orderId=second_order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=reused_signature,
        nonce=str(first_nonce),  # Reuse the same nonce
        expiresAfter=reused_deadline,
    )

    logger.info(f"Step 2: Trying to cancel with reused nonce: {first_nonce}")

    try:
        response = await spot_tester.client.orders.cancel_order(cancel_order_request=reused_cancel_request)
        pytest.fail(f"Cancel with reused nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for nonce-related keywords
        assert (
            "400" in str(e) or "nonce" in error_msg or "invalid" in error_msg
        ), f"Expected nonce rejection error, got: {e}"
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    # Clean up - cancel with valid request
    await spot_tester.client.cancel_order(
        order_id=second_order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT CANCEL REUSED NONCE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_cancel_old_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a cancel request with an old nonce (nonce - 1) is rejected.

    First creates and cancels an order with a specific nonce, then tries to
    use nonce-1 for another cancel.
    """
    logger.info("=" * 80)
    logger.info("SPOT CANCEL OLD NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Step 1: Create first order and cancel it with a specific nonce
    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(spot_config.price(0.96)))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    first_order_id = await spot_tester.orders.create_limit(order_params)
    assert first_order_id is not None, "Order creation should return order_id for GTC orders"
    await spot_tester.wait.for_order_creation(first_order_id)
    logger.info(f"Step 1: Created first order: {first_order_id}")

    first_nonce = spot_tester.get_next_nonce()
    first_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    sig_gen = spot_tester.client.signature_generator
    first_signature = sig_gen.sign_cancel_order_spot(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        order_id=int(first_order_id),
        client_order_id=0,
        nonce=first_nonce,
        deadline=first_deadline,
    )

    first_cancel_request = CancelOrderRequest(
        orderId=first_order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=first_signature,
        nonce=str(first_nonce),
        expiresAfter=first_deadline,
    )

    logger.info(f"Step 1: Cancelling first order with nonce: {first_nonce}")
    await spot_tester.client.orders.cancel_order(cancel_order_request=first_cancel_request)
    await asyncio.sleep(0.1)
    logger.info("✅ First cancel succeeded")

    # Step 2: Create second order and try to cancel with nonce - 1
    second_order_id = await spot_tester.orders.create_limit(order_params)
    assert second_order_id is not None, "Order creation should return order_id for GTC orders"
    await spot_tester.wait.for_order_creation(second_order_id)
    logger.info(f"Step 2: Created second order: {second_order_id}")

    old_nonce = first_nonce - 1
    old_deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    old_signature = sig_gen.sign_cancel_order_spot(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        order_id=int(second_order_id),
        client_order_id=0,
        nonce=old_nonce,  # Use nonce - 1
        deadline=old_deadline,
    )

    old_cancel_request = CancelOrderRequest(
        orderId=second_order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=old_signature,
        nonce=str(old_nonce),  # Use nonce - 1
        expiresAfter=old_deadline,
    )

    logger.info(f"Step 2: Trying to cancel with old nonce (nonce-1): {old_nonce}")

    try:
        response = await spot_tester.client.orders.cancel_order(cancel_order_request=old_cancel_request)
        pytest.fail(f"Cancel with old nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for nonce-related keywords
        assert (
            "400" in str(e) or "nonce" in error_msg or "invalid" in error_msg
        ), f"Expected nonce rejection error, got: {e}"
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    # Clean up - cancel with valid request
    await spot_tester.client.cancel_order(
        order_id=second_order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT CANCEL OLD NONCE TEST COMPLETED")


# ============================================================================
# MASS CANCEL VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_mass_cancel_invalid_signature(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a mass cancel with an invalid signature is rejected.
    """
    logger.info("=" * 80)
    logger.info("SPOT MASS CANCEL INVALID SIGNATURE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Create a fake signature
    fake_signature = "0x" + "ab" * 65
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=fake_signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )

    logger.info("Sending mass cancel with invalid signature...")

    try:
        response = await spot_tester.client.orders.cancel_all(mass_cancel_request)
        pytest.fail(f"Mass cancel with invalid signature should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        # Expect: Invalid signature error
        assert "Invalid signature" in error_msg or "CANCEL" in error_msg, f"Expected signature error, got: {e}"
        logger.info(f"✅ Mass cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT MASS CANCEL INVALID SIGNATURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_mass_cancel_expired_deadline(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a mass cancel with an expired deadline is rejected.
    """
    logger.info("=" * 80)
    logger.info("SPOT MASS CANCEL EXPIRED DEADLINE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Use an expired deadline (1 hour in the past)
    expired_deadline = int(time.time()) - 3600  # 1 hour ago (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator
    signature = sig_gen.sign_mass_cancel(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        nonce=nonce,
        deadline=expired_deadline,
    )

    mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=expired_deadline,
    )

    logger.info(f"Sending mass cancel with expired deadline: {expired_deadline}")

    try:
        response = await spot_tester.client.orders.cancel_all(mass_cancel_request)
        pytest.fail(f"Mass cancel with expired deadline should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for deadline-related keywords
        assert (
            "400" in str(e) or "deadline" in error_msg or "expired" in error_msg
        ), f"Expected deadline rejection error, got: {e}"
        logger.info(f"✅ Mass cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT MASS CANCEL EXPIRED DEADLINE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_mass_cancel_reused_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a mass cancel with a reused nonce is rejected.

    First performs a successful mass cancel with a specific nonce, then
    tries to reuse that nonce.
    """
    logger.info("=" * 80)
    logger.info("SPOT MASS CANCEL REUSED NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Step 1: Perform a valid mass cancel using the SDK's nonce generator
    # This establishes a nonce baseline in the API's nonce tracker
    first_nonce = spot_tester.client.get_next_nonce()
    first_deadline = int(time.time()) + 60  # 1 minute from now

    sig_gen = spot_tester.client.signature_generator
    first_signature = sig_gen.sign_mass_cancel(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        nonce=first_nonce,
        deadline=first_deadline,
    )

    first_mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=first_signature,
        nonce=str(first_nonce),
        expiresAfter=first_deadline,
    )

    logger.info(f"Step 1: Performing mass cancel with nonce: {first_nonce}")
    await spot_tester.client.orders.cancel_all(first_mass_cancel_request)
    logger.info("✅ First mass cancel succeeded (established nonce baseline)")

    # Step 2: Try to reuse the SAME nonce - this should be rejected
    reused_deadline = int(time.time()) + 60  # 1 minute from now
    reused_signature = sig_gen.sign_mass_cancel(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        nonce=first_nonce,  # Reuse the same nonce
        deadline=reused_deadline,
    )

    reused_mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=reused_signature,
        nonce=str(first_nonce),  # Reuse the same nonce
        expiresAfter=reused_deadline,
    )

    logger.info(f"Step 2: Trying mass cancel with reused nonce: {first_nonce}")

    try:
        response = await spot_tester.client.orders.cancel_all(reused_mass_cancel_request)
        pytest.fail(f"Mass cancel with reused nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for nonce-related keywords
        assert (
            "400" in str(e) or "nonce" in error_msg or "invalid" in error_msg
        ), f"Expected nonce rejection error, got: {e}"
        logger.info(f"✅ Mass cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT MASS CANCEL REUSED NONCE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_mass_cancel_old_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a mass cancel with an old nonce (nonce - 1) is rejected.

    First performs a successful mass cancel with a specific nonce, then
    tries to use nonce-1.
    """
    logger.info("=" * 80)
    logger.info("SPOT MASS CANCEL OLD NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Step 1: Perform a valid mass cancel using the SDK's nonce generator
    # This establishes a nonce baseline in the API's nonce tracker
    first_nonce = spot_tester.client.get_next_nonce()
    first_deadline = int(time.time()) + 60  # 1 minute from now

    sig_gen = spot_tester.client.signature_generator
    first_signature = sig_gen.sign_mass_cancel(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        nonce=first_nonce,
        deadline=first_deadline,
    )

    first_mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=first_signature,
        nonce=str(first_nonce),
        expiresAfter=first_deadline,
    )

    logger.info(f"Step 1: Performing mass cancel with nonce: {first_nonce}")
    await spot_tester.client.orders.cancel_all(first_mass_cancel_request)
    logger.info("✅ First mass cancel succeeded (established nonce baseline)")

    # Step 2: Try to use nonce - 1 (which is definitely old now)
    old_nonce = first_nonce - 1
    old_deadline = int(time.time()) + 60  # 1 minute from now
    old_signature = sig_gen.sign_mass_cancel(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        nonce=old_nonce,
        deadline=old_deadline,
    )

    old_mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=old_signature,
        nonce=str(old_nonce),
        expiresAfter=old_deadline,
    )

    logger.info(f"Step 2: Trying mass cancel with old nonce (nonce-1): {old_nonce}")

    try:
        response = await spot_tester.client.orders.cancel_all(old_mass_cancel_request)
        pytest.fail(f"Mass cancel with old nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for nonce-related keywords
        assert (
            "400" in str(e) or "nonce" in error_msg or "invalid" in error_msg
        ), f"Expected nonce rejection error, got: {e}"
        logger.info(f"✅ Mass cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT MASS CANCEL OLD NONCE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_cancel_wrong_signer(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a cancel request signed by a different wallet is rejected.

    The API should verify that the signer has permission to cancel orders
    on the specified account.
    """
    logger.info("=" * 80)
    logger.info("SPOT CANCEL WRONG SIGNER TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # First create a valid order
    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(spot_config.price(0.96)))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    order_id = await spot_tester.orders.create_limit(order_params)
    assert order_id is not None, "Order creation should return order_id for GTC orders"
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"Created order: {order_id}")

    # Create a different signer (wrong wallet)
    wrong_private_key = "0x" + "ab" * 32  # Random private key
    wrong_config = TradingConfig(
        api_url=spot_tester.client.config.api_url,
        chain_id=spot_tester.client.config.chain_id,
        owner_wallet_address=spot_tester.client.config.owner_wallet_address,
        private_key=wrong_private_key,
        account_id=spot_tester.account_id,
    )
    wrong_signer = SignatureGenerator(wrong_config)

    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    # Sign cancel request with the wrong private key
    wrong_signature = wrong_signer.sign_cancel_order_spot(
        account_id=spot_tester.account_id,  # Same account
        market_id=spot_config.market_id,
        order_id=int(order_id),
        client_order_id=0,
        nonce=nonce,
        deadline=deadline,
    )

    cancel_request = CancelOrderRequest(
        orderId=order_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        signature=wrong_signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )

    logger.info(f"Sending cancel signed by wrong wallet: {wrong_signer.signer_wallet_address}")

    try:
        response = await spot_tester.client.orders.cancel_order(cancel_order_request=cancel_request)
        pytest.fail(f"Cancel from unauthorized signer should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for unauthorized/signature-related keywords
        assert (
            "400" in str(e) or "unauthorized" in error_msg or "signature" in error_msg or "permission" in error_msg
        ), f"Expected unauthorized signer rejection error, got: {e}"
        logger.info(f"✅ Cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    # Clean up - cancel with valid request
    await spot_tester.client.cancel_order(
        order_id=order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT CANCEL WRONG SIGNER TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_mass_cancel_wrong_signer(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a mass cancel request signed by a different wallet is rejected.

    The API should verify that the signer has permission to cancel orders
    on the specified account.
    """
    logger.info("=" * 80)
    logger.info("SPOT MASS CANCEL WRONG SIGNER TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # First create a valid order
    order_params = (
        OrderBuilder()
        .symbol(spot_config.symbol)
        .buy()
        .price(str(spot_config.price(0.96)))
        .qty(spot_config.min_qty)
        .gtc()
        .build()
    )

    order_id = await spot_tester.orders.create_limit(order_params)
    await spot_tester.wait.for_order_creation(order_id)
    logger.info(f"Created order: {order_id}")

    # Create a different signer (wrong wallet)
    wrong_private_key = "0x" + "cd" * 32  # Random private key
    wrong_config = TradingConfig(
        api_url=spot_tester.client.config.api_url,
        chain_id=spot_tester.client.config.chain_id,
        owner_wallet_address=spot_tester.client.config.owner_wallet_address,
        private_key=wrong_private_key,
        account_id=spot_tester.account_id,
    )
    wrong_signer = SignatureGenerator(wrong_config)

    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    # Sign mass cancel request with the wrong private key
    wrong_signature = wrong_signer.sign_mass_cancel(
        account_id=spot_tester.account_id,  # Same account
        market_id=spot_config.market_id,
        nonce=nonce,
        deadline=deadline,
    )

    mass_cancel_request = MassCancelRequest(
        accountId=spot_tester.account_id,
        symbol=spot_config.symbol,
        signature=wrong_signature,
        nonce=str(nonce),
        expiresAfter=deadline,
    )

    logger.info(f"Sending mass cancel signed by wrong wallet: {wrong_signer.signer_wallet_address}")

    try:
        response = await spot_tester.client.orders.cancel_all(mass_cancel_request)
        pytest.fail(f"Mass cancel from unauthorized signer should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        # API should reject with 400 error - check for unauthorized/signature-related keywords
        assert (
            "400" in str(e) or "unauthorized" in error_msg or "signature" in error_msg or "permission" in error_msg
        ), f"Expected unauthorized signer rejection error, got: {e}"
        logger.info(f"✅ Mass cancel rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    # Clean up - cancel with valid request
    await spot_tester.client.cancel_order(
        order_id=order_id,
        symbol=spot_config.symbol,
        account_id=spot_tester.account_id,
    )
    await asyncio.sleep(0.1)
    await spot_tester.check.no_open_orders()
    logger.info("✅ SPOT MASS CANCEL WRONG SIGNER TEST COMPLETED")


# ============================================================================
# REQUEST FIELD VALIDATION TESTS
# ============================================================================


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_invalid_exchange_id(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with an invalid exchangeId is rejected.

    The API should validate that exchangeId is a positive number.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER INVALID EXCHANGE ID TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    # Build order request with invalid exchangeId
    price = str(spot_config.price(0.96))
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator

    # Create inputs for signing
    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(price),
        qty=Decimal(spot_config.min_qty),
    )

    signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    # Create request with invalid exchangeId (0 or negative)
    order_request = CreateOrderRequest(
        exchangeId=0,  # Invalid - must be positive
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        isBuy=True,
        limitPx=price,
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=deadline,
        signerWallet=sig_gen.signer_wallet_address,
    )

    logger.info("Sending order with exchangeId=0...")

    try:
        response = await spot_tester.client.orders.create_order(order_request)
        pytest.fail(f"Order with invalid exchangeId should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e)
        assert (
            "exchangeId must be a positive integer" in error_msg
        ), f"Expected 'exchangeId must be a positive integer' error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    logger.info("✅ SPOT ORDER INVALID EXCHANGE ID TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_invalid_symbol(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with an unrecognized symbol is rejected.

    The API should validate that the symbol maps to a valid market.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER INVALID SYMBOL TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    price = str(spot_config.price(0.96))
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(price),
        qty=Decimal(spot_config.min_qty),
    )

    signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    # Create request with invalid symbol
    order_request = CreateOrderRequest(
        exchangeId=spot_tester.client.config.dex_id,
        symbol="INVALIDXYZ",  # Non-existent symbol
        accountId=spot_tester.account_id,
        isBuy=True,
        limitPx=price,
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        signature=signature,
        nonce=str(nonce),
        expiresAfter=deadline,
        signerWallet=sig_gen.signer_wallet_address,
    )

    logger.info("Sending order with invalid symbol 'INVALIDXYZ'...")

    try:
        response = await spot_tester.client.orders.create_order(order_request)
        pytest.fail(f"Order with invalid symbol should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        assert (
            "symbol" in error_msg or "unrecognized" in error_msg or "CREATE_ORDER_OTHER_ERROR" in str(e)
        ), f"Expected symbol validation error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    logger.info("✅ SPOT ORDER INVALID SYMBOL TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_missing_signature(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order without a signature is rejected.

    The API should validate that signature is required.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER MISSING SIGNATURE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    price = str(spot_config.price(0.96))
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator

    # Create request without signature (empty string)
    order_request = CreateOrderRequest(
        exchangeId=spot_tester.client.config.dex_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        isBuy=True,
        limitPx=price,
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        signature="",  # Empty signature
        nonce=str(nonce),
        expiresAfter=deadline,
        signerWallet=sig_gen.signer_wallet_address,
    )

    logger.info("Sending order with empty signature...")

    try:
        response = await spot_tester.client.orders.create_order(order_request)
        pytest.fail(f"Order without signature should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        assert "signature" in error_msg or "CREATE_ORDER_OTHER_ERROR" in str(
            e
        ), f"Expected signature validation error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    logger.info("✅ SPOT ORDER MISSING SIGNATURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_missing_nonce(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order without a nonce is rejected.

    The API should validate that nonce is required.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER MISSING NONCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    price = str(spot_config.price(0.96))
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(price),
        qty=Decimal(spot_config.min_qty),
    )

    signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    # Create request without nonce (empty string)
    order_request = CreateOrderRequest(
        exchangeId=spot_tester.client.config.dex_id,
        symbol=spot_config.symbol,
        accountId=spot_tester.account_id,
        isBuy=True,
        limitPx=price,
        qty=spot_config.min_qty,
        orderType=OrderType.LIMIT,
        timeInForce=TimeInForce.GTC,
        signature=signature,
        nonce="",  # Empty nonce
        expiresAfter=deadline,
        signerWallet=sig_gen.signer_wallet_address,
    )

    logger.info("Sending order with empty nonce...")

    try:
        response = await spot_tester.client.orders.create_order(order_request)
        pytest.fail(f"Order without nonce should have been rejected, got: {response}")
    except ApiException as e:
        error_msg = str(e).lower()
        assert "nonce" in error_msg or "CREATE_ORDER_OTHER_ERROR" in str(
            e
        ), f"Expected nonce validation error, got: {e}"
        logger.info(f"✅ Order rejected as expected: {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    logger.info("✅ SPOT ORDER MISSING NONCE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_invalid_time_in_force(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that an order with an invalid timeInForce is rejected.

    The API should validate that timeInForce is one of: IOC, GTC.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER INVALID TIME IN FORCE TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    price = str(spot_config.price(0.96))
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)
    nonce = spot_tester.get_next_nonce()

    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(price),
        qty=Decimal(spot_config.min_qty),
    )

    signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    # Create request dict with invalid timeInForce (bypass enum validation)
    order_dict = {
        "exchangeId": spot_tester.client.config.dex_id,
        "symbol": spot_config.symbol,
        "accountId": spot_tester.account_id,
        "isBuy": True,
        "limitPx": price,
        "qty": spot_config.min_qty,
        "orderType": "LIMIT",
        "timeInForce": "INVALID_TIF",  # Invalid value
        "signature": signature,
        "nonce": str(nonce),
        "expiresAfter": deadline,
        "signerWallet": sig_gen.signer_wallet_address,
    }

    logger.info("Sending order with invalid timeInForce 'INVALID_TIF'...")

    try:
        # Use raw HTTP request to bypass SDK validation
        async with aiohttp.ClientSession() as session:
            url = f"{spot_tester.client.config.api_url}/createOrder"
            async with session.post(url, json=order_dict) as resp:
                if resp.status == 200:
                    pytest.fail("Order with invalid timeInForce should have been rejected")
                response_text = await resp.text()
                assert (
                    "timeInForce" in response_text.lower() or resp.status == 400
                ), f"Expected timeInForce validation error, got: {response_text}"
                logger.info(f"✅ Order rejected as expected: HTTP {resp.status}")
                logger.info(f"   Error: {response_text[:150]}")
    except ApiException as e:
        # If we can't make raw request, the SDK validation caught it
        logger.info(f"✅ Order rejected (SDK or API validation): {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    logger.info("✅ SPOT ORDER INVALID TIME IN FORCE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.validation
@pytest.mark.asyncio
async def test_spot_order_missing_expiration(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that a spot order without expiresAfter is rejected.

    The API should validate that expiresAfter is required for spot orders.
    """
    logger.info("=" * 80)
    logger.info("SPOT ORDER MISSING EXPIRATION TEST")
    logger.info("=" * 80)

    await spot_tester.orders.close_all(fail_if_none=False)

    price = str(spot_config.price(0.96))
    nonce = spot_tester.get_next_nonce()
    deadline = int(time.time()) + 60  # 1 minute from now (in seconds)

    sig_gen = spot_tester.client.signature_generator

    inputs = sig_gen.encode_inputs_limit_order(
        is_buy=True,
        limit_px=Decimal(price),
        qty=Decimal(spot_config.min_qty),
    )

    signature = sig_gen.sign_raw_order(
        account_id=spot_tester.account_id,
        market_id=spot_config.market_id,
        exchange_id=spot_tester.client.config.dex_id,
        counterparty_account_ids=[],
        order_type=6,
        inputs=inputs,
        deadline=deadline,
        nonce=nonce,
    )

    # Create request dict without expiresAfter
    order_dict = {
        "exchangeId": spot_tester.client.config.dex_id,
        "symbol": spot_config.symbol,
        "accountId": spot_tester.account_id,
        "isBuy": True,
        "limitPx": price,
        "qty": spot_config.min_qty,
        "orderType": "LIMIT",
        "timeInForce": "GTC",
        "signature": signature,
        "nonce": str(nonce),
        # expiresAfter intentionally omitted
        "signerWallet": sig_gen.signer_wallet_address,
    }

    logger.info("Sending order without expiresAfter...")

    try:
        async with aiohttp.ClientSession() as session:
            url = f"{spot_tester.client.config.api_url}/createOrder"
            async with session.post(url, json=order_dict) as resp:
                if resp.status == 200:
                    pytest.fail("Order without expiresAfter should have been rejected")
                response_text = await resp.text()
                assert (
                    "expiresAfter" in response_text.lower() or "expires" in response_text.lower() or resp.status == 400
                ), f"Expected expiresAfter validation error, got: {response_text}"
                logger.info(f"✅ Order rejected as expected: HTTP {resp.status}")
                logger.info(f"   Error: {response_text[:150]}")
    except ApiException as e:
        logger.info(f"✅ Order rejected (SDK or API validation): {type(e).__name__}")
        logger.info(f"   Error: {str(e)[:150]}")

    logger.info("✅ SPOT ORDER MISSING EXPIRATION TEST COMPLETED")
