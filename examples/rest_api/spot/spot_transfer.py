#!/usr/bin/env python3
"""
Spot Asset Transfer - Transfer spot assets between accounts via order matching.

This script transfers a spot asset (e.g., WETH) from one account to another using
the SPOT API. Since there's no direct transfer mechanism for spot assets, this is
accomplished by matching orders:
1. Sender places a GTC sell order at a specific price
2. Receiver places an IOC buy order at the same price to match

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- SPOT_PRIVATE_KEY_1: Private key for the sender wallet
- SPOT_PRIVATE_KEY_2: Private key for the receiver wallet (defaults to SPOT_PRIVATE_KEY_1 if same wallet)
- Sender account must be whitelisted in ME_GTC_PERMISSIONS_WHITELIST on the API server
- Receiver account must have sufficient rUSD balance to pay for the asset

Usage:
    python -m examples.rest_api.spot.spot_transfer \\
        --from-account 10000000002 \\
        --to-account 10000000003 \\
        --asset ETH \\
        --qty 5
"""

import argparse
import asyncio
import logging
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv
from eth_account import Account

from sdk.open_api.models import TimeInForce
from sdk.reya_rest_api import ReyaTradingClient
from sdk.reya_rest_api.config import TradingConfig
from sdk.reya_rest_api.models.orders import LimitOrderParameters

logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("spot_transfer")
logger.setLevel(logging.INFO)
logger.handlers = []
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(handler)
logger.propagate = False

ASSET_TO_SYMBOL = {"ETH": "WETHRUSD", "WETH": "WETHRUSD"}
DEFAULT_TRANSFER_PRICE = "3156"  # Realistic ETH price for transfers
ORDER_SETTLEMENT_RETRIES = 3
ORDER_SETTLEMENT_DELAY = 1.0


async def get_account_balance(client: ReyaTradingClient, account_id: int, asset: str) -> Decimal:
    """Get the balance of a specific asset for a specific account."""
    balances = await client.get_account_balances()
    for balance in balances:
        if balance.account_id == account_id and balance.asset == asset:
            return Decimal(balance.real_balance)
    return Decimal("0")


async def log_account_balances(
    sender_client: ReyaTradingClient,
    receiver_client: ReyaTradingClient | None,
    from_account_id: int,
    to_account_id: int,
    asset: str,
    label: str,
) -> tuple[Decimal, Decimal]:
    """Log and return balances for both accounts.

    Uses sender_client for from_account and receiver_client for to_account
    since each client can only see balances for accounts owned by its wallet.
    """
    from_balance = await get_account_balance(sender_client, from_account_id, asset)

    # Use receiver client if available, otherwise try sender client
    if receiver_client:
        to_balance = await get_account_balance(receiver_client, to_account_id, asset)
    else:
        to_balance = await get_account_balance(sender_client, to_account_id, asset)

    logger.info(f"{label}")
    logger.info(f"  From Account ({from_account_id}): {from_balance:>12} {asset}")
    logger.info(f"    To Account ({to_account_id}): {to_balance:>12} {asset}")

    return from_balance, to_balance


async def validate_sufficient_balance(
    client: ReyaTradingClient,
    account_id: int,
    asset: str,
    required_qty: Decimal,
) -> bool:
    """Validate that the account has sufficient balance for the transfer."""
    balance = await get_account_balance(client, account_id, asset)

    if balance < required_qty:
        logger.error(
            f"❌ Insufficient balance! Account {account_id} has {balance} {asset}, "
            f"but {required_qty} {asset} is required for transfer."
        )
        return False

    logger.info(f"  ✅ Balance check passed: {balance} {asset} >= {required_qty} {asset}")
    return True


async def execute_spot_transfer(
    sender_client: ReyaTradingClient,
    receiver_client: ReyaTradingClient,
    from_account_id: int,
    to_account_id: int,
    symbol: str,
    qty: str,
    transfer_price: str,
) -> tuple[bool, str | None]:
    """
    Execute a spot transfer by matching orders between sender and receiver.

    The sender places a GTC sell order, and the receiver places an IOC buy order
    at the same price to match immediately.
    """
    logger.info("📤 Executing transfer...")

    # Step 1: Sender places GTC sell order
    logger.info(f"  [1/3] Placing sell order from Account {from_account_id}")

    sell_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=False,
        limit_px=transfer_price,
        qty=qty,
        time_in_force=TimeInForce.GTC,
    )

    sell_response = await sender_client.create_limit_order(sell_params)
    sell_order_id = sell_response.order_id

    if not sell_order_id:
        logger.error(f"❌ Failed to create sell order: {sell_response.status}")
        return False, None

    logger.info(f"        ✓ Order created: {sell_order_id}")

    await asyncio.sleep(0.5)

    # Step 2: Receiver places IOC buy order to match
    logger.info(f"  [2/3] Placing buy order from Account {to_account_id}")

    buy_params = LimitOrderParameters(
        symbol=symbol,
        is_buy=True,
        limit_px=transfer_price,
        qty=qty,
        time_in_force=TimeInForce.IOC,
    )

    buy_response = await receiver_client.create_limit_order(buy_params)
    buy_order_id = buy_response.order_id

    logger.info(f"        ✓ Order filled: {buy_response.status.value}")

    # Step 3: Wait for orders to settle
    logger.info("  [3/3] Settling orders...")
    await asyncio.sleep(2.0)

    # Check if sell order is still open
    order_fully_matched = False
    open_orders = await sender_client.get_open_orders()
    sell_order_still_open = any(order.order_id == sell_order_id for order in open_orders if hasattr(order, "order_id"))

    if sell_order_still_open:
        logger.warning(f"        ⚠️ Sell order {sell_order_id} partially filled, cancelling remainder...")
        try:
            await sender_client.cancel_order(
                order_id=sell_order_id,
                symbol=symbol,
                account_id=from_account_id,
            )
        except (OSError, RuntimeError):  # nosec B110
            pass  # Order may have been filled in the meantime
    else:
        order_fully_matched = True
        logger.info("        ✓ Sell order fully matched")

    # Get transaction hash from spot executions
    tx_hash = None
    try:
        spot_executions = await receiver_client.get_spot_executions()
        for execution in spot_executions.data:
            if execution.order_id == buy_order_id:
                tx_hash = execution.additional_properties.get("transactionHash")
                if not tx_hash:
                    tx_hash = execution.additional_properties.get("txHash")
                break
    except (OSError, RuntimeError):  # nosec B110
        pass  # Execution lookup may fail, but transfer still succeeded

    return order_fully_matched, tx_hash


def create_trading_client_config(
    private_key: str,
    account_id: int,
    base_config: TradingConfig,
) -> TradingConfig:
    """Create a TradingConfig for a specific account and private key."""
    wallet = Account.from_key(private_key)
    return TradingConfig(
        private_key=private_key,
        chain_id=base_config.chain_id,
        api_url=base_config.api_url,
        account_id=account_id,
        owner_wallet_address=wallet.address,
    )


async def balance_accounts_mode() -> None:
    """Balance ETH and RUSD between SPOT_ACCOUNT_1 and SPOT_ACCOUNT_2."""
    load_dotenv()

    spot_account_1 = int(os.getenv("SPOT_ACCOUNT_ID_1", "0"))
    spot_account_2 = int(os.getenv("SPOT_ACCOUNT_ID_2", "0"))
    spot_key_1 = os.getenv("SPOT_PRIVATE_KEY_1")
    spot_key_2 = os.getenv("SPOT_PRIVATE_KEY_2")

    if not all([spot_account_1, spot_account_2, spot_key_1, spot_key_2]):
        logger.error("❌ SPOT_ACCOUNT_ID_1, SPOT_ACCOUNT_ID_2, SPOT_PRIVATE_KEY_1, SPOT_PRIVATE_KEY_2 must all be set")
        sys.exit(1)

    logger.info("⚖️  BALANCE ACCOUNTS MODE")
    logger.info(f"  Account 1: {spot_account_1}")
    logger.info(f"  Account 2: {spot_account_2}")

    # Get base config to inherit api_url and chain_id
    base_client = ReyaTradingClient()
    base_config = base_client.config

    # Create client 1 with proper config
    config_1 = create_trading_client_config(spot_key_1, spot_account_1, base_config)
    async with ReyaTradingClient(config=config_1) as client_1:
        await client_1.start()

        # Create client 2 with proper config
        config_2 = create_trading_client_config(spot_key_2, spot_account_2, base_config)
        async with ReyaTradingClient(config=config_2) as client_2:
            await client_2.start()

            # Get current balances
            eth_1 = await get_account_balance(client_1, spot_account_1, "ETH")
            eth_2 = await get_account_balance(client_2, spot_account_2, "ETH")
            rusd_1 = await get_account_balance(client_1, spot_account_1, "RUSD")
            rusd_2 = await get_account_balance(client_2, spot_account_2, "RUSD")

            logger.info("📊 CURRENT BALANCES")
            logger.info(f"  Account {spot_account_1}: {eth_1} ETH, {rusd_1} RUSD")
            logger.info(f"  Account {spot_account_2}: {eth_2} ETH, {rusd_2} RUSD")

            # Calculate targets
            total_eth = eth_1 + eth_2
            total_rusd = rusd_1 + rusd_2
            target_eth = total_eth / 2
            _ = total_rusd / 2  # target_rusd - calculated but not used directly

            logger.info("🎯 TARGET ETH BALANCE")
            logger.info(f"  Each account: {target_eth} ETH")
            logger.info(f"  (RUSD will flow at market price ${DEFAULT_TRANSFER_PRICE})")

            # Determine transfer direction and amounts
            eth_diff_1 = eth_1 - target_eth  # Positive = account 1 has excess

            # We transfer ETH from the account with excess
            # Note: We use the market price (DEFAULT_TRANSFER_PRICE) for the transfer.
            # This balances ETH between accounts but RUSD will flow at market price,
            # not at an arbitrary price to balance both assets simultaneously.
            # On-chain price validation enforces orders must be within ~5% of oracle price.
            if abs(eth_diff_1) < Decimal("0.001"):
                logger.info("✅ Accounts already balanced (ETH difference < 0.001)")
                return

            if eth_diff_1 > 0:
                # Account 1 has excess ETH, transfer to Account 2
                from_account = spot_account_1
                to_account = spot_account_2
                sender_client = client_1
                receiver_client = client_2
                eth_to_transfer = eth_diff_1
            else:
                # Account 2 has excess ETH, transfer to Account 1
                from_account = spot_account_2
                to_account = spot_account_1
                sender_client = client_2
                receiver_client = client_1
                eth_to_transfer = -eth_diff_1

            # Use market price for transfer (on-chain enforces price limits)
            transfer_price = Decimal(DEFAULT_TRANSFER_PRICE)

            # Round to reasonable precision
            eth_qty = str(eth_to_transfer.quantize(Decimal("0.001")))
            price_str = str(transfer_price.quantize(Decimal("0.01")))

            logger.info("📤 TRANSFER PLAN")
            logger.info(f"  From: {from_account} → To: {to_account}")
            logger.info(f"  ETH:  {eth_qty}")
            logger.info(f"  Price: ${price_str} (RUSD per ETH)")

            # Execute transfer
            symbol = "WETHRUSD"
            _, tx_hash = await execute_spot_transfer(
                sender_client, receiver_client, from_account, to_account, symbol, eth_qty, price_str
            )

            if tx_hash:
                logger.info(f"🔗 Transaction: {tx_hash}")

            # Wait and show final balances
            logger.info("⏳ Waiting for balance updates...")
            await asyncio.sleep(2.0)

            eth_1_final = await get_account_balance(client_1, spot_account_1, "ETH")
            eth_2_final = await get_account_balance(client_2, spot_account_2, "ETH")
            rusd_1_final = await get_account_balance(client_1, spot_account_1, "RUSD")
            rusd_2_final = await get_account_balance(client_2, spot_account_2, "RUSD")

            logger.info("📊 FINAL BALANCES")
            logger.info(f"  Account {spot_account_1}: {eth_1_final} ETH, {rusd_1_final} RUSD")
            logger.info(f"  Account {spot_account_2}: {eth_2_final} ETH, {rusd_2_final} RUSD")
            logger.info("🎉 Accounts balanced!")


async def main():
    """Main entry point for the spot transfer script."""
    parser = argparse.ArgumentParser(description="Transfer spot assets between accounts via order matching")
    parser.add_argument(
        "--balance-accounts", action="store_true", help="Balance ETH and RUSD between SPOT_1 and SPOT_2 accounts"
    )
    parser.add_argument("--from-account", type=int, help="Account ID to transfer FROM (sender)")
    parser.add_argument("--to-account", type=int, help="Account ID to transfer TO (receiver)")
    parser.add_argument("--asset", type=str, choices=["ETH", "WETH"], help="Asset to transfer")
    parser.add_argument("--qty", type=str, help="Quantity to transfer (e.g., 5)")
    parser.add_argument("--price", type=str, default=None, help="Custom price for transfer (default: 0.01)")

    args = parser.parse_args()

    if args.balance_accounts:
        await balance_accounts_mode()
        return

    # Validate required args for manual transfer mode
    if not all([args.from_account, args.to_account, args.asset, args.qty]):
        parser.error("--from-account, --to-account, --asset, and --qty are required for manual transfer")

    load_dotenv()

    from_account_id = args.from_account
    to_account_id = args.to_account
    asset = args.asset.upper()
    qty = args.qty
    transfer_price = args.price if args.price else DEFAULT_TRANSFER_PRICE

    symbol = ASSET_TO_SYMBOL.get(asset)
    if not symbol:
        logger.error(f"❌ Unknown asset: {asset}. Supported: {list(ASSET_TO_SYMBOL.keys())}")
        sys.exit(1)

    # Map account IDs to their private keys
    spot_account_1 = int(os.getenv("SPOT_ACCOUNT_ID_1", "0"))
    spot_account_2 = int(os.getenv("SPOT_ACCOUNT_ID_2", "0"))
    spot_key_1 = os.getenv("SPOT_PRIVATE_KEY_1")
    spot_key_2 = os.getenv("SPOT_PRIVATE_KEY_2")

    if not spot_key_1:
        logger.error("❌ SPOT_PRIVATE_KEY_1 environment variable is required")
        sys.exit(1)

    # Determine which key to use for sender and receiver based on account ownership
    if from_account_id == spot_account_1:
        sender_key = spot_key_1
    elif from_account_id == spot_account_2:
        sender_key = spot_key_2
    else:
        logger.error(f"❌ Unknown from_account {from_account_id}. Must be {spot_account_1} or {spot_account_2}")
        sys.exit(1)

    if to_account_id == spot_account_1:
        receiver_key = spot_key_1
    elif to_account_id == spot_account_2:
        receiver_key = spot_key_2
    else:
        logger.error(f"❌ Unknown to_account {to_account_id}. Must be {spot_account_1} or {spot_account_2}")
        sys.exit(1)

    if not sender_key:
        logger.error(f"❌ Private key not configured for sender account {from_account_id}")
        sys.exit(1)
    if not receiver_key:
        logger.error(f"❌ Private key not configured for receiver account {to_account_id}")
        sys.exit(1)

    logger.info("🚀 SPOT ASSET TRANSFER")
    logger.info(f"  From Account: {from_account_id}")
    logger.info(f"    To Account: {to_account_id}")
    logger.info(f"  Asset:        {asset}")
    logger.info(f"  Quantity:     {qty}")
    logger.info(f"  Symbol:       {symbol}")
    logger.info(f"  Price:        ${transfer_price}")

    # Get base config to inherit api_url and chain_id
    base_client = ReyaTradingClient()
    base_config = base_client.config

    # Create sender client with proper config
    sender_config = create_trading_client_config(sender_key, from_account_id, base_config)
    async with ReyaTradingClient(config=sender_config) as sender_client:
        await sender_client.start()

        # Validate sender has sufficient balance
        qty_decimal = Decimal(qty)
        if not await validate_sufficient_balance(sender_client, from_account_id, asset, qty_decimal):
            sys.exit(1)

        # Create receiver client with proper config
        receiver_config = create_trading_client_config(receiver_key, to_account_id, base_config)
        async with ReyaTradingClient(config=receiver_config) as receiver_client:
            await receiver_client.start()

            # Log initial balances (now both clients available)
            initial_from, initial_to = await log_account_balances(
                sender_client, receiver_client, from_account_id, to_account_id, asset, "📊 INITIAL BALANCES"
            )

            # Execute the transfer
            order_fully_matched, tx_hash = await execute_spot_transfer(
                sender_client, receiver_client, from_account_id, to_account_id, symbol, qty, transfer_price
            )

            if tx_hash:
                logger.info(f"🔗 Transaction: {tx_hash}")

            # Wait for balances to update
            logger.info("⏳ Waiting for balance updates...")
            await asyncio.sleep(2.0)

            # Log final balances (use both clients)
            final_from, final_to = await log_account_balances(
                sender_client, receiver_client, from_account_id, to_account_id, asset, "📊 FINAL BALANCES"
            )

            # Verify the transfer using balance changes
            from_change = final_from - initial_from
            to_change = final_to - initial_to

            logger.info("📈 Balance changes:")
            logger.info(f"  From Account ({from_account_id}): {from_change:>+12} {asset}")
            logger.info(f"    To Account ({to_account_id}): {to_change:>+12} {asset}")

            # Determine success based on both order settlement and balance changes
            expected_change = qty_decimal
            balances_match = from_change == -expected_change and to_change == expected_change

            if order_fully_matched and balances_match:
                logger.info("🎉 Transfer complete - verified successfully!")
            elif balances_match:
                logger.info("🎉 Transfer complete - balances verified (order was partially filled by stale orders)")
            elif to_change > 0:
                logger.warning(f"⚠️ Partial transfer. Expected: {expected_change} {asset}, Got: {to_change} {asset}")
                sys.exit(1)
            else:
                logger.error(
                    f"❌ Transfer failed. Expected: -{expected_change}/{expected_change}, Got: {from_change}/{to_change}"
                )
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
