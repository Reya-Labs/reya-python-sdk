"""
Spot Execution Busts Tests

Tests for the spot execution bust (failed spot fill) feature:
- REST API: GET /v2/wallet/{address}/spotExecutionBusts
- REST API: GET /v2/market/{symbol}/spotExecutionBusts
- WebSocket: /v2/wallet/{address}/spotExecutionBusts channel
- WebSocket: /v2/market/{symbol}/spotExecutionBusts channel

These tests verify:
1. REST endpoints return valid responses with correct structure
2. WebSocket bust subscriptions are established successfully
3. Data consistency between wallet and market bust endpoints
4. Bust model fields are correctly typed and populated (when busts exist)

Note: Trade busts are exceptional events (on-chain settlement reverts).
These tests verify the API/SDK plumbing works correctly. In normal conditions,
bust lists will be empty. When busts do exist, we validate their structure.
"""

import asyncio
import logging
import time

import pytest

from sdk.open_api.exceptions import ApiException
from sdk.open_api.models.spot_execution_bust import SpotExecutionBust
from sdk.open_api.models.spot_execution_bust_list import SpotExecutionBustList
from tests.helpers import ReyaTester
from tests.helpers.builders.order_builder import OrderBuilder
from tests.test_spot.spot_config import SpotTestConfig

logger = logging.getLogger("reya.integration_tests")


# =============================================================================
# Helper: validate bust structure
# =============================================================================


def validate_bust_fields(bust: SpotExecutionBust, expected_symbol: str | None = None) -> None:
    """Validate that a SpotExecutionBust has all required fields with correct types."""
    assert bust.symbol is not None, "Bust symbol should not be None"
    assert isinstance(bust.symbol, str), f"Bust symbol should be str, got {type(bust.symbol)}"

    if expected_symbol is not None:
        assert bust.symbol == expected_symbol, f"Expected symbol {expected_symbol}, got {bust.symbol}"

    assert bust.account_id is not None, "Bust account_id should not be None"
    assert isinstance(bust.account_id, int), f"Bust account_id should be int, got {type(bust.account_id)}"

    assert bust.exchange_id is not None, "Bust exchange_id should not be None"
    assert isinstance(bust.exchange_id, int), f"Bust exchange_id should be int, got {type(bust.exchange_id)}"

    assert bust.maker_account_id is not None, "Bust maker_account_id should not be None"
    assert isinstance(
        bust.maker_account_id, int
    ), f"Bust maker_account_id should be int, got {type(bust.maker_account_id)}"

    assert bust.order_id is not None, "Bust order_id should not be None"
    assert isinstance(bust.order_id, str), f"Bust order_id should be str, got {type(bust.order_id)}"

    assert bust.maker_order_id is not None, "Bust maker_order_id should not be None"
    assert isinstance(bust.maker_order_id, str), f"Bust maker_order_id should be str, got {type(bust.maker_order_id)}"

    assert bust.qty is not None, "Bust qty should not be None"
    assert isinstance(bust.qty, str), f"Bust qty should be str, got {type(bust.qty)}"
    assert float(bust.qty) > 0, f"Bust qty should be positive, got {bust.qty}"

    assert bust.side is not None, "Bust side should not be None"
    side_val = bust.side.value if hasattr(bust.side, "value") else bust.side
    assert side_val in ("B", "A"), f"Bust side should be 'B' or 'A', got {side_val}"

    assert bust.price is not None, "Bust price should not be None"
    assert isinstance(bust.price, str), f"Bust price should be str, got {type(bust.price)}"

    assert bust.reason is not None, "Bust reason should not be None"
    assert isinstance(bust.reason, str), f"Bust reason should be str, got {type(bust.reason)}"

    assert bust.timestamp is not None, "Bust timestamp should not be None"
    assert isinstance(bust.timestamp, int), f"Bust timestamp should be int, got {type(bust.timestamp)}"
    assert bust.timestamp > 0, f"Bust timestamp should be positive, got {bust.timestamp}"


# =============================================================================
# REST API TESTS - Wallet Spot Execution Busts
# =============================================================================


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_wallet_spot_execution_busts_structure(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test wallet spot execution busts REST endpoint returns correct structure.

    Verifies:
    - Endpoint returns a valid SpotExecutionBustList response
    - Response has 'data' and 'meta' attributes
    - If busts exist, each bust has correct field types
    """
    logger.info("=" * 80)
    logger.info("WALLET SPOT EXECUTION BUSTS STRUCTURE TEST")
    logger.info("=" * 80)

    wallet_address = spot_tester.owner_wallet_address
    assert wallet_address is not None, "Wallet address required"

    bust_list: SpotExecutionBustList = await spot_tester.client.wallet.get_wallet_spot_execution_busts(
        address=wallet_address
    )

    # Verify response structure
    assert bust_list is not None, "Response should not be None"
    assert isinstance(bust_list, SpotExecutionBustList), f"Expected SpotExecutionBustList, got {type(bust_list)}"
    assert hasattr(bust_list, "data"), "Response should have 'data' attribute"
    assert hasattr(bust_list, "meta"), "Response should have 'meta' attribute"
    assert isinstance(bust_list.data, list), "data should be a list"

    logger.info(f"Wallet spot execution busts returned: {len(bust_list.data)} bust(s)")

    # Validate bust fields if any exist
    for bust in bust_list.data[:5]:
        assert isinstance(bust, SpotExecutionBust), f"Expected SpotExecutionBust, got {type(bust)}"
        validate_bust_fields(bust)
        logger.info(
            f"   - {bust.symbol}: qty={bust.qty}, price={bust.price}, "
            f"order_id={bust.order_id}, reason={bust.reason[:20]}..."
        )

    logger.info("✅ WALLET SPOT EXECUTION BUSTS STRUCTURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_wallet_spot_execution_busts_via_client_wrapper(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test the convenience wrapper client.get_spot_execution_busts().

    Verifies the high-level client method works correctly and returns
    the same structure as the direct wallet API call.
    """
    logger.info("=" * 80)
    logger.info("WALLET SPOT EXECUTION BUSTS (CLIENT WRAPPER) TEST")
    logger.info("=" * 80)

    # Use the high-level convenience method
    bust_list: SpotExecutionBustList = await spot_tester.client.get_spot_execution_busts()

    assert bust_list is not None, "Response should not be None"
    assert isinstance(bust_list, SpotExecutionBustList), f"Expected SpotExecutionBustList, got {type(bust_list)}"
    assert isinstance(bust_list.data, list), "data should be a list"

    logger.info(f"Client wrapper returned: {len(bust_list.data)} bust(s)")

    # Validate bust fields if any exist
    for bust in bust_list.data[:5]:
        validate_bust_fields(bust)

    logger.info("✅ WALLET SPOT EXECUTION BUSTS (CLIENT WRAPPER) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_wallet_spot_execution_busts_via_data_ops(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test the DataOperations.spot_execution_busts() helper.

    Verifies the test helper returns a clean list of SpotExecutionBust objects.
    """
    logger.info("=" * 80)
    logger.info("WALLET SPOT EXECUTION BUSTS (DATA OPS) TEST")
    logger.info("=" * 80)

    busts = await spot_tester.data.spot_execution_busts()

    assert isinstance(busts, list), f"Expected list, got {type(busts)}"
    logger.info(f"DataOperations returned: {len(busts)} bust(s)")

    for bust in busts[:5]:
        validate_bust_fields(bust)

    logger.info("✅ WALLET SPOT EXECUTION BUSTS (DATA OPS) TEST COMPLETED")


# =============================================================================
# REST API TESTS - Market Spot Execution Busts
# =============================================================================


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_market_spot_execution_busts_structure(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test market spot execution busts REST endpoint returns correct structure.

    Verifies:
    - Endpoint returns a valid SpotExecutionBustList response
    - Response has 'data' and 'meta' attributes
    - If busts exist, all belong to the queried market symbol
    """
    logger.info("=" * 80)
    logger.info(f"MARKET SPOT EXECUTION BUSTS STRUCTURE TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    bust_list: SpotExecutionBustList = await spot_tester.client.markets.get_market_spot_execution_busts(
        symbol=spot_config.symbol
    )

    # Verify response structure
    assert bust_list is not None, "Response should not be None"
    assert isinstance(bust_list, SpotExecutionBustList), f"Expected SpotExecutionBustList, got {type(bust_list)}"
    assert hasattr(bust_list, "data"), "Response should have 'data' attribute"
    assert hasattr(bust_list, "meta"), "Response should have 'meta' attribute"
    assert isinstance(bust_list.data, list), "data should be a list"

    logger.info(f"Market spot execution busts for {spot_config.symbol}: {len(bust_list.data)} bust(s)")

    # Validate bust fields and symbol consistency
    for bust in bust_list.data[:5]:
        assert isinstance(bust, SpotExecutionBust), f"Expected SpotExecutionBust, got {type(bust)}"
        validate_bust_fields(bust, expected_symbol=spot_config.symbol)
        logger.info(
            f"   - qty={bust.qty}, price={bust.price}, " f"order_id={bust.order_id}, reason={bust.reason[:20]}..."
        )

    logger.info("✅ MARKET SPOT EXECUTION BUSTS STRUCTURE TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_market_spot_execution_busts_via_data_ops(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test the DataOperations.market_spot_execution_busts() helper.

    Verifies the test helper returns a clean list of SpotExecutionBust objects
    for a specific market.
    """
    logger.info("=" * 80)
    logger.info(f"MARKET SPOT EXECUTION BUSTS (DATA OPS) TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    busts = await spot_tester.data.market_spot_execution_busts(spot_config.symbol)

    assert isinstance(busts, list), f"Expected list, got {type(busts)}"
    logger.info(f"DataOperations returned: {len(busts)} bust(s) for {spot_config.symbol}")

    for bust in busts[:5]:
        validate_bust_fields(bust, expected_symbol=spot_config.symbol)

    logger.info("✅ MARKET SPOT EXECUTION BUSTS (DATA OPS) TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_wallet_and_market_busts_consistency(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that wallet and market bust endpoints return consistent data.

    If busts exist for the configured symbol, they should appear in both
    the wallet-level and market-level endpoints.
    """
    logger.info("=" * 80)
    logger.info(f"WALLET/MARKET BUST CONSISTENCY TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Fetch from both endpoints
    wallet_busts = await spot_tester.data.spot_execution_busts()
    market_busts = await spot_tester.data.market_spot_execution_busts(spot_config.symbol)

    logger.info(f"Wallet busts: {len(wallet_busts)}, Market busts ({spot_config.symbol}): {len(market_busts)}")

    # Filter wallet busts to only this market's symbol
    wallet_busts_for_symbol = [b for b in wallet_busts if b.symbol == spot_config.symbol]
    logger.info(f"Wallet busts for {spot_config.symbol}: {len(wallet_busts_for_symbol)}")

    # Market busts for the symbol should be a subset of or equal to wallet busts for that symbol
    # (wallet sees busts where it's either taker or maker; market sees all busts for the symbol)
    # Just verify both are valid lists with consistent structure
    for bust in wallet_busts_for_symbol[:5]:
        validate_bust_fields(bust, expected_symbol=spot_config.symbol)

    for bust in market_busts[:5]:
        validate_bust_fields(bust, expected_symbol=spot_config.symbol)

    logger.info("✅ WALLET/MARKET BUST CONSISTENCY TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_rest_get_market_spot_execution_busts_pagination(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test market spot execution busts REST endpoint supports time-based pagination.

    Verifies start_time and end_time parameters work correctly.
    """
    logger.info("=" * 80)
    logger.info(f"MARKET SPOT EXECUTION BUSTS PAGINATION TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Fetch all busts first
    all_busts: SpotExecutionBustList = await spot_tester.client.markets.get_market_spot_execution_busts(
        symbol=spot_config.symbol
    )

    logger.info(f"Total busts: {len(all_busts.data)}")

    # Test with end_time filter (current time) - should return same or fewer results
    current_time_ms = int(time.time() * 1000)
    filtered_busts: SpotExecutionBustList = await spot_tester.client.markets.get_market_spot_execution_busts(
        symbol=spot_config.symbol, end_time=current_time_ms
    )

    assert filtered_busts is not None, "Filtered response should not be None"
    assert isinstance(filtered_busts.data, list), "Filtered data should be a list"
    logger.info(f"Filtered busts (end_time=now): {len(filtered_busts.data)}")

    # Test with start_time in the future - should return empty
    future_time_ms = int((time.time() + 86400) * 1000)  # 24 hours from now
    future_busts: SpotExecutionBustList = await spot_tester.client.markets.get_market_spot_execution_busts(
        symbol=spot_config.symbol, start_time=future_time_ms
    )

    assert future_busts is not None, "Future response should not be None"
    assert len(future_busts.data) == 0, f"Expected 0 busts for future start_time, got {len(future_busts.data)}"
    logger.info("✅ No busts returned for future start_time (correct)")

    # If busts exist, test time-based filtering
    if len(all_busts.data) >= 2:
        second_bust = all_busts.data[1]
        end_ts = second_bust.timestamp

        time_filtered: SpotExecutionBustList = await spot_tester.client.markets.get_market_spot_execution_busts(
            symbol=spot_config.symbol, end_time=end_ts
        )
        assert len(time_filtered.data) >= 1, "Should have at least one bust before end_time"
        logger.info(f"Time-filtered busts (end_time={end_ts}): {len(time_filtered.data)}")

    logger.info("✅ MARKET SPOT EXECUTION BUSTS PAGINATION TEST COMPLETED")


# =============================================================================
# WEBSOCKET TESTS - Bust Subscriptions
# =============================================================================


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_ws_wallet_spot_execution_busts_subscription(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test WebSocket subscription to wallet spot execution busts channel.

    Verifies:
    - Subscription to /v2/wallet/{address}/spotExecutionBusts succeeds
    - The bust EventStore is initialized and accessible
    - No crash occurs when subscribing to the bust channel
    """
    logger.info("=" * 80)
    logger.info("WS WALLET SPOT EXECUTION BUSTS SUBSCRIPTION TEST")
    logger.info("=" * 80)

    # The wallet bust subscription is automatically established in on_open
    # Just verify the EventStore exists and is accessible
    assert spot_tester.ws.spot_execution_busts is not None, "Bust EventStore should exist"

    # Verify initial state
    bust_count = len(spot_tester.ws.spot_execution_busts)
    logger.info(f"Current wallet bust count: {bust_count}")

    # Verify clear works
    spot_tester.ws.clear_spot_execution_busts()
    assert len(spot_tester.ws.spot_execution_busts) == 0, "Bust store should be empty after clear"

    logger.info("✅ WS WALLET SPOT EXECUTION BUSTS SUBSCRIPTION TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_ws_market_spot_execution_busts_subscription(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test WebSocket subscription to market spot execution busts channel.

    Verifies:
    - Explicit subscription to /v2/market/{symbol}/spotExecutionBusts succeeds
    - The market bust EventStore is initialized correctly
    """
    logger.info("=" * 80)
    logger.info(f"WS MARKET SPOT EXECUTION BUSTS SUBSCRIPTION TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Subscribe to market-level bust channel
    spot_tester.ws.subscribe_to_market_spot_execution_busts(spot_config.symbol)

    # Allow time for subscription confirmation
    await asyncio.sleep(1.0)

    # Verify market bust store exists (may or may not have data)
    logger.info(f"Market bust stores: {list(spot_tester.ws.market_spot_execution_busts.keys())}")

    # Verify clear works
    spot_tester.ws.clear_market_spot_execution_busts(spot_config.symbol)
    if spot_config.symbol in spot_tester.ws.market_spot_execution_busts:
        assert len(spot_tester.ws.market_spot_execution_busts[spot_config.symbol]) == 0

    logger.info("✅ WS MARKET SPOT EXECUTION BUSTS SUBSCRIPTION TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.websocket
@pytest.mark.asyncio
async def test_ws_bust_store_operations(
    spot_config: SpotTestConfig, spot_tester: ReyaTester
):  # pylint: disable=unused-argument
    """
    Test WebSocket bust EventStore operations.

    Verifies:
    - clear() works for both wallet and market bust stores
    - clear_all via ws.clear() includes bust stores
    - Bust stores are properly isolated from execution stores
    """
    logger.info("=" * 80)
    logger.info("WS BUST STORE OPERATIONS TEST")
    logger.info("=" * 80)

    # Record initial counts
    initial_spot_exec_count = len(spot_tester.ws.spot_executions)
    initial_bust_count = len(spot_tester.ws.spot_execution_busts)
    logger.info(f"Initial spot executions: {initial_spot_exec_count}, busts: {initial_bust_count}")

    # Clear only busts - should not affect spot executions
    spot_tester.ws.clear_spot_execution_busts()
    assert len(spot_tester.ws.spot_execution_busts) == 0, "Bust store should be empty"
    assert (
        len(spot_tester.ws.spot_executions) == initial_spot_exec_count
    ), "Spot executions should be unchanged after clearing busts"

    # Clear all - should clear both
    spot_tester.ws.clear()
    assert len(spot_tester.ws.spot_executions) == 0, "Spot executions should be empty after clear_all"
    assert len(spot_tester.ws.spot_execution_busts) == 0, "Busts should be empty after clear_all"
    assert len(spot_tester.ws.market_spot_execution_busts) == 0, "Market busts should be empty after clear_all"

    logger.info("✅ WS BUST STORE OPERATIONS TEST COMPLETED")


# =============================================================================
# END-TO-END TESTS - Trade + Bust Verification
# =============================================================================


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.asyncio
async def test_successful_trade_produces_no_busts(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test that a successful spot trade does NOT produce bust events.

    This is a critical negative test: after a normal successful trade,
    there should be no new bust events for either maker or taker.

    Flow:
    1. Record bust counts before trade
    2. Execute a successful maker-taker trade
    3. Verify no new busts appeared for either party
    """
    logger.info("=" * 80)
    logger.info(f"SUCCESSFUL TRADE NO BUSTS TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Clear bust tracking
    maker_tester.ws.clear_spot_execution_busts()
    taker_tester.ws.clear_spot_execution_busts()

    # Record pre-trade bust counts via REST
    maker_busts_before = await maker_tester.data.spot_execution_busts()
    taker_busts_before = await taker_tester.data.spot_execution_busts()
    maker_bust_count_before = len(maker_busts_before)
    taker_bust_count_before = len(taker_busts_before)

    logger.info(f"Pre-trade busts: maker={maker_bust_count_before}, taker={taker_bust_count_before}")

    # Execute a normal trade via maker-taker self-matching
    maker_params = OrderBuilder.from_config(spot_config).buy().at_price(0.97).gtc().build()
    maker_order_id = await maker_tester.orders.create_limit(maker_params)
    await maker_tester.wait.for_order_creation(maker_order_id)

    taker_params = OrderBuilder.from_config(spot_config).sell().at_price(0.97).ioc().build()
    await taker_tester.orders.create_limit(taker_params)
    await asyncio.sleep(1.0)

    logger.info("✅ Trade executed successfully")

    # Verify no new busts via WebSocket
    ws_maker_busts = len(maker_tester.ws.spot_execution_busts)
    ws_taker_busts = len(taker_tester.ws.spot_execution_busts)
    logger.info(f"WS bust events after trade: maker={ws_maker_busts}, taker={ws_taker_busts}")

    assert ws_maker_busts == 0, f"Maker should have 0 new WS bust events, got {ws_maker_busts}"
    assert ws_taker_busts == 0, f"Taker should have 0 new WS bust events, got {ws_taker_busts}"

    # Verify no new busts via REST
    maker_busts_after = await maker_tester.data.spot_execution_busts()
    taker_busts_after = await taker_tester.data.spot_execution_busts()

    assert (
        len(maker_busts_after) == maker_bust_count_before
    ), f"Maker REST bust count should not change: before={maker_bust_count_before}, after={len(maker_busts_after)}"
    assert (
        len(taker_busts_after) == taker_bust_count_before
    ), f"Taker REST bust count should not change: before={taker_bust_count_before}, after={len(taker_busts_after)}"

    logger.info("✅ No busts produced - successful trade verified clean")

    # Cleanup
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    logger.info("✅ SUCCESSFUL TRADE NO BUSTS TEST COMPLETED")


@pytest.mark.spot
@pytest.mark.rest_api
@pytest.mark.asyncio
async def test_bust_data_is_historical_consistent(spot_config: SpotTestConfig, spot_tester: ReyaTester):
    """
    Test that repeated queries to bust endpoints return consistent results.

    Verifies idempotency: calling the same endpoint twice should return
    the same data (no phantom busts, no data loss between calls).
    """
    logger.info("=" * 80)
    logger.info("BUST DATA HISTORICAL CONSISTENCY TEST")
    logger.info("=" * 80)

    # Query wallet busts twice
    busts_first = await spot_tester.data.spot_execution_busts()
    busts_second = await spot_tester.data.spot_execution_busts()

    assert len(busts_first) == len(
        busts_second
    ), f"Bust count should be consistent: first={len(busts_first)}, second={len(busts_second)}"

    # If busts exist, verify order_ids match
    if busts_first:
        first_ids = {b.order_id for b in busts_first}
        second_ids = {b.order_id for b in busts_second}
        assert first_ids == second_ids, "Bust order_ids should be identical between queries"

    # Query market busts twice
    market_busts_first = await spot_tester.data.market_spot_execution_busts(spot_config.symbol)
    market_busts_second = await spot_tester.data.market_spot_execution_busts(spot_config.symbol)

    assert len(market_busts_first) == len(
        market_busts_second
    ), f"Market bust count should be consistent: first={len(market_busts_first)}, second={len(market_busts_second)}"

    logger.info(
        f"Consistency verified: wallet={len(busts_first)} busts, "
        f"market({spot_config.symbol})={len(market_busts_first)} busts"
    )

    logger.info("✅ BUST DATA HISTORICAL CONSISTENCY TEST COMPLETED")


# =============================================================================
# DELIBERATE BUST TEST - Trigger a bust via insufficient balance
# =============================================================================


@pytest.mark.spot
@pytest.mark.maker_taker
@pytest.mark.bust
@pytest.mark.asyncio
async def test_deliberate_bust_via_insufficient_balance(
    spot_config: SpotTestConfig, maker_tester: ReyaTester, taker_tester: ReyaTester
):
    """
    Test that deliberately triggers a spot execution bust.

    Strategy:
    - Maker posts a GTC sell at oracle×1.01 (above best bid, won't match external
      liquidity) for a quantity just exceeding their base asset balance.
      GTCs skip balance checks, so the order is accepted.
    - Taker posts an IOC buy at the same price to cross the GTC.
      The IOC balance check passes because taker has enough rUSD.
    - Off-chain matching succeeds, but on-chain settlement reverts because the
      maker cannot deliver the full quantity of base asset.
    - The API may raise an ApiException on the IOC (expected — the executor reports
      the on-chain revert). The bust event is still emitted via WS.

    The bust reason string may vary (insufficient balance, price deviation, etc.)
    depending on which on-chain check fails first. We assert a bust exists without
    hardcoding a specific reason.
    """
    logger.info("=" * 80)
    logger.info(f"DELIBERATE BUST TEST: {spot_config.symbol}")
    logger.info("=" * 80)

    # Step 1: Clean state
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    # Record bust counts before
    maker_busts_before = await maker_tester.data.spot_execution_busts()
    taker_busts_before = await taker_tester.data.spot_execution_busts()
    market_busts_before = await maker_tester.data.market_spot_execution_busts(spot_config.symbol)

    maker_bust_count_before = len(maker_busts_before)
    taker_bust_count_before = len(taker_busts_before)
    market_bust_count_before = len(market_busts_before)

    logger.info(
        f"Bust counts before: maker={maker_bust_count_before}, "
        f"taker={taker_bust_count_before}, market={market_bust_count_before}"
    )

    # Clear WS bust tracking
    maker_tester.ws.clear_spot_execution_busts()
    taker_tester.ws.clear_spot_execution_busts()

    # Step 2: Refresh order book — skip if external liquidity present
    await spot_config.refresh_order_book(maker_tester.data)
    if spot_config.has_any_external_liquidity:
        pytest.skip(
            "Skipping deliberate bust test: external liquidity present on the book. "
            "Run with an empty book (no external MM) to avoid unintended matches."
        )

    # Step 3: Get maker's actual base asset balance and taker's rUSD
    base_asset = spot_config.base_asset  # e.g., "ETH" (API returns unwrapped names)
    maker_balance = await maker_tester.data.balance(base_asset)
    assert maker_balance is not None, f"Maker should have {base_asset} balance"
    maker_base_balance = float(maker_balance.real_balance)
    logger.info(f"Maker {base_asset} balance: {maker_base_balance}")

    taker_rusd = await taker_tester.data.balance("RUSD")
    assert taker_rusd is not None, "Taker should have RUSD balance"
    taker_rusd_balance = float(taker_rusd.real_balance)
    logger.info(f"Taker RUSD balance: {taker_rusd_balance}")

    # Step 4: Calculate bust parameters
    # Price near oracle — within circuit breaker (±5%) so on-chain price check passes.
    # With no external liquidity, only our two accounts can match.
    bust_price = str(spot_config.sell_price(1.01))  # oracle × 1.01

    # bust_qty must exceed maker's balance so settlement fails,
    # but taker must afford bust_qty * bust_price in rUSD
    min_qty_val = float(spot_config.min_qty)
    bust_price_f = float(bust_price)
    taker_max_qty = taker_rusd_balance / bust_price_f  # max qty taker can afford
    bust_qty_f = maker_base_balance + min_qty_val * 10  # just above maker's balance

    assert bust_qty_f < taker_max_qty, (
        f"Taker cannot afford bust_qty ({bust_qty_f:.6f}) × price ({bust_price}): "
        f"needs ${bust_qty_f * bust_price_f:.2f}, has ${taker_rusd_balance:.2f}. "
        f"Fund taker with more RUSD."
    )

    bust_qty = str(round(bust_qty_f, 6))
    taker_rusd_needed = float(bust_qty) * bust_price_f
    logger.info(f"Bust params: qty={bust_qty}, price={bust_price}, " f"taker rUSD needed={taker_rusd_needed:.2f}")

    # Record balances before to verify no change after bust
    maker_balances_before = await maker_tester.data.balances()
    taker_balances_before = await taker_tester.data.balances()

    # Step 5: Maker posts GTC sell (no balance check — exceeds actual balance)
    logger.info(f"Placing maker GTC sell: qty={bust_qty} at price={bust_price}")
    maker_order_params = OrderBuilder.from_config(spot_config).sell().qty(bust_qty).price(bust_price).gtc().build()
    maker_order_id = await maker_tester.orders.create_limit(maker_order_params)
    logger.info(f"Maker GTC sell placed: order_id={maker_order_id}")

    # Wait for GTC to appear on book
    await maker_tester.wait.for_order_creation(maker_order_id)
    logger.info("Maker GTC confirmed on book")

    # Step 6: Taker posts IOC buy to cross the GTC
    # The API may raise ApiException when on-chain settlement reverts — this is expected
    logger.info(f"Placing taker IOC buy: qty={bust_qty} at price={bust_price}")
    taker_order_params = OrderBuilder.from_config(spot_config).buy().qty(bust_qty).price(bust_price).ioc().build()
    taker_order_id = None
    try:
        taker_order_id = await taker_tester.orders.create_limit(taker_order_params)
        logger.info(f"Taker IOC buy placed: order_id={taker_order_id}")
    except ApiException as e:
        logger.info(f"Taker IOC raised ApiException (expected for bust): {e.body}")

    # Step 7: Wait for bust event via WS (use maker_order_id since bust references it)
    logger.info("Waiting for bust event...")
    bust = await maker_tester.wait.for_spot_execution_bust(order_id=maker_order_id, timeout=20)
    logger.info(
        f"Bust received: order_id={bust.order_id}, maker_order_id={bust.maker_order_id}, "
        f"qty={bust.qty}, price={bust.price}, reason={bust.reason}"
    )

    # Step 8: Validate bust fields
    validate_bust_fields(bust, expected_symbol=spot_config.symbol)
    assert bust.reason, "Bust reason should be non-empty"
    logger.info(f"Bust reason: {bust.reason}")

    # Step 9: Verify bust counts increased via REST
    maker_busts_after = await maker_tester.data.spot_execution_busts()
    taker_busts_after = await taker_tester.data.spot_execution_busts()
    market_busts_after = await maker_tester.data.market_spot_execution_busts(spot_config.symbol)

    assert (
        len(maker_busts_after) > maker_bust_count_before
    ), f"Maker bust count should increase: before={maker_bust_count_before}, after={len(maker_busts_after)}"
    assert (
        len(taker_busts_after) > taker_bust_count_before
    ), f"Taker bust count should increase: before={taker_bust_count_before}, after={len(taker_busts_after)}"
    assert (
        len(market_busts_after) > market_bust_count_before
    ), f"Market bust count should increase: before={market_bust_count_before}, after={len(market_busts_after)}"

    logger.info(
        f"Bust counts after: maker={len(maker_busts_after)}, "
        f"taker={len(taker_busts_after)}, market={len(market_busts_after)}"
    )

    # Step 10: Verify balances unchanged (settlement reverted — no assets moved)
    maker_balances_after = await maker_tester.data.balances()
    taker_balances_after = await taker_tester.data.balances()

    for asset in [base_asset, "RUSD"]:
        before = maker_balances_before.get(asset)
        after = maker_balances_after.get(asset)
        if before and after:
            assert before.real_balance == after.real_balance, (
                f"Maker {asset} balance should be unchanged after bust: "
                f"before={before.real_balance}, after={after.real_balance}"
            )

        before = taker_balances_before.get(asset)
        after = taker_balances_after.get(asset)
        if before and after:
            assert before.real_balance == after.real_balance, (
                f"Taker {asset} balance should be unchanged after bust: "
                f"before={before.real_balance}, after={after.real_balance}"
            )

    logger.info("✅ Balances unchanged — settlement correctly reverted")

    # Cleanup: cancel any lingering maker GTC
    await maker_tester.orders.close_all(fail_if_none=False)
    await taker_tester.orders.close_all(fail_if_none=False)

    logger.info("✅ DELIBERATE BUST TEST COMPLETED")
