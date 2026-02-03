"""
Wallet Spot Executions REST API Tests

Tests for the GET /v2/wallet/:address/spotExecutions endpoint:
- Fetching wallet-specific spot execution history
- Empty executions list handling
- Pagination support
"""

import asyncio
import logging

import pytest

from sdk.open_api.models import OrderStatus
from sdk.open_api.models.spot_execution import SpotExecution
from sdk.open_api.models.spot_execution_list import SpotExecutionList
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_wallet_spot_executions_structure(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test wallet spot executions REST endpoint returns correct structure.

    Supports both empty and non-empty order books:
    - If external liquidity exists, taker trades against it
    - If no external liquidity, maker provides liquidity first

    Flow:
    1. Execute a trade to ensure execution history exists
    2. Fetch wallet spot executions via REST
    3. Verify response structure and data types
    """
    logger.info("=" * 80)
    logger.info("WALLET SPOT EXECUTIONS STRUCTURE TEST")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Refresh order book state
    await spot_config.refresh_order_book(taker_tester.data)

    # Execute a trade to create execution history
    usable_bid = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    maker_order_id = None

    if usable_bid is not None:
        logger.info(f"Using external bid liquidity at ${usable_bid}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(usable_bid)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)
    elif usable_ask is not None:
        logger.info(f"Using external ask liquidity at ${usable_ask}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(usable_ask)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)
    else:
        logger.info("No external liquidity - creating maker order")
        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.3)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    logger.info("✅ Trade executed - execution history created")

    # Fetch wallet spot executions via REST
    wallet_address = taker_tester.owner_wallet_address
    assert wallet_address is not None, "Wallet address required"

    executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(address=wallet_address)

    # Verify response structure
    assert executions is not None, "Response should not be None"
    assert isinstance(executions, SpotExecutionList), f"Expected SpotExecutionList, got {type(executions)}"
    assert hasattr(executions, "data"), "Response should have 'data' attribute"
    assert isinstance(executions.data, list), "data should be a list"

    logger.info(f"Wallet spot executions returned: {len(executions.data)}")

    # Verify we have at least one execution
    assert len(executions.data) > 0, "Should have at least one execution after trade"

    # Verify execution data structure
    latest: SpotExecution = executions.data[0]
    assert isinstance(latest, SpotExecution), f"Expected SpotExecution, got {type(latest)}"

    logger.info("✅ Response structure validated")

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ WALLET SPOT EXECUTIONS STRUCTURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_wallet_spot_executions_pagination(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test wallet spot executions REST endpoint pagination.

    This test requires a controlled environment to verify multiple trades.
    When external liquidity exists, we skip to avoid unpredictable matching.

    Flow:
    1. Check for external liquidity - skip if present
    2. Execute multiple trades
    3. Fetch executions with pagination parameters
    4. Verify pagination works correctly
    """
    logger.info("=" * 80)
    logger.info("WALLET SPOT EXECUTIONS PAGINATION TEST")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Refresh order book state
    await spot_config.refresh_order_book(maker_tester.data)

    # Skip if external liquidity exists
    if spot_config.has_any_external_liquidity:
        pytest.skip("Skipping pagination test: external liquidity exists. This test requires a controlled environment.")

    # Execute multiple trades to have pagination data
    num_trades = 3
    for i in range(num_trades):
        maker_price = round(spot_config.oracle_price * (0.96 + i * 0.01), 2)

        maker_params = OrderBuilder.from_config(spot_config).buy().price(str(maker_price)).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(maker_price)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.1)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)
        logger.info(f"✅ Trade {i + 1}/{num_trades} executed")

    await asyncio.sleep(0.5)

    # Fetch all executions
    wallet_address = taker_tester.owner_wallet_address
    assert wallet_address is not None

    all_executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=wallet_address
    )

    assert len(all_executions.data) >= num_trades, f"Expected at least {num_trades} executions"
    logger.info(f"Total executions: {len(all_executions.data)}")

    # Test with time-based pagination (start_time/end_time)
    if len(all_executions.data) >= 2:
        # Use the timestamp of the second execution as end_time to get only older executions
        second_execution = all_executions.data[1]
        end_time = second_execution.timestamp

        filtered_executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
            address=wallet_address, end_time=end_time
        )

        # Should return executions up to and including the end_time
        assert len(filtered_executions.data) >= 1, "Should have at least one execution before end_time"
        logger.info(f"Filtered executions (end_time={end_time}): {len(filtered_executions.data)}")

    logger.info("✅ Pagination parameters work correctly")

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ WALLET SPOT EXECUTIONS PAGINATION TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_wallet_spot_executions_filters_by_wallet(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test wallet spot executions are filtered by wallet address.

    Flow:
    1. Execute a trade between maker and taker
    2. Fetch executions for taker wallet
    3. Verify executions belong to taker's account
    """
    logger.info("=" * 80)
    logger.info("WALLET SPOT EXECUTIONS FILTER BY WALLET TEST")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Refresh order book state
    await spot_config.refresh_order_book(taker_tester.data)

    # Execute a trade
    usable_bid = spot_config.get_usable_bid_price_for_qty(spot_config.min_qty)
    usable_ask = spot_config.get_usable_ask_price_for_qty(spot_config.min_qty)

    maker_order_id = None

    if usable_bid is not None:
        logger.info(f"Using external bid liquidity at ${usable_bid}")
        taker_params = OrderBuilder.from_config(spot_config).sell().price(str(usable_bid)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)
    elif usable_ask is not None:
        logger.info(f"Using external ask liquidity at ${usable_ask}")
        taker_params = OrderBuilder.from_config(spot_config).buy().price(str(usable_ask)).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.5)
    else:
        logger.info("No external liquidity - creating maker order")
        maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
        maker_order_id = await maker_tester.orders.create_limit(maker_params)
        await maker_tester.wait.for_order_creation(maker_order_id)

        taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()
        await taker_tester.orders.create_limit(taker_params)
        await asyncio.sleep(0.3)
        await maker_tester.wait.for_order_state(maker_order_id, OrderStatus.FILLED, timeout=5)

    logger.info("✅ Trade executed")

    # Fetch executions for taker wallet
    taker_wallet = taker_tester.owner_wallet_address
    assert taker_wallet is not None

    taker_executions: SpotExecutionList = await taker_tester.client.wallet.get_wallet_spot_executions(
        address=taker_wallet
    )

    assert len(taker_executions.data) > 0, "Taker should have executions"

    # Verify executions belong to taker's account
    taker_account_id = taker_tester.account_id
    for execution in taker_executions.data[:5]:  # Check first 5
        # Execution should involve taker's account (as account_id or maker_account_id)
        is_taker_execution = execution.account_id == taker_account_id or execution.maker_account_id == taker_account_id
        assert is_taker_execution, (
            f"Execution should involve taker account {taker_account_id}, "
            f"got account_id={execution.account_id}, maker_account_id={execution.maker_account_id}"
        )

    logger.info(f"✅ All executions involve taker account {taker_account_id}")

    # Verify no open orders remain
    await maker_tester.check.no_open_orders()
    await taker_tester.check.no_open_orders()

    logger.info("✅ WALLET SPOT EXECUTIONS FILTER BY WALLET TEST COMPLETED")
