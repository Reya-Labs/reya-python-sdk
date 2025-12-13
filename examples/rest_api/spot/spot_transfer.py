#!/usr/bin/env python3
"""
Spot Asset Transfer - Transfer spot assets between accounts via order matching.

This script transfers a spot asset (e.g., WETH) from one account to another using
the SPOT API. Since there's no direct transfer mechanism for spot assets, this is
accomplished by matching orders:
1. Sender places a GTC sell order at a specific price
2. Receiver places an IOC buy order at the same price to match

Requirements:
- PRIVATE_KEY: Private key for the sender wallet
- PRIVATE_KEY_2: Private key for the receiver wallet (defaults to PRIVATE_KEY if same wallet)
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
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
from sdk.reya_rest_api.auth import SignatureGenerator
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
TRANSFER_PRICE = "0.01"  # Low price to avoid external matches
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
        limit_px=TRANSFER_PRICE,
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
        limit_px=TRANSFER_PRICE,
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
    sell_order_still_open = any(
        order.order_id == sell_order_id for order in open_orders if hasattr(order, "order_id")
    )

    if sell_order_still_open:
        logger.warning(f"        ⚠️ Sell order {sell_order_id} partially filled, cancelling remainder...")
        try:
            await sender_client.cancel_order(
                order_id=sell_order_id,
                symbol=symbol,
                account_id=from_account_id,
            )
        except Exception:
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
    except Exception:
        pass

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


async def main():
    """Main entry point for the spot transfer script."""
    parser = argparse.ArgumentParser(description="Transfer spot assets between accounts via order matching")
    parser.add_argument("--from-account", type=int, required=True, help="Account ID to transfer FROM (sender)")
    parser.add_argument("--to-account", type=int, required=True, help="Account ID to transfer TO (receiver)")
    parser.add_argument("--asset", type=str, required=True, choices=["ETH", "WETH"], help="Asset to transfer")
    parser.add_argument("--qty", type=str, required=True, help="Quantity to transfer (e.g., 5)")

    args = parser.parse_args()
    load_dotenv()

    from_account_id = args.from_account
    to_account_id = args.to_account
    asset = args.asset.upper()
    qty = args.qty

    symbol = ASSET_TO_SYMBOL.get(asset)
    if not symbol:
        logger.error(f"❌ Unknown asset: {asset}. Supported: {list(ASSET_TO_SYMBOL.keys())}")
        sys.exit(1)

    private_key = os.getenv("PRIVATE_KEY")
    private_key_2 = os.getenv("PRIVATE_KEY_2", private_key)

    if not private_key:
        logger.error("❌ PRIVATE_KEY environment variable is required")
        sys.exit(1)

    logger.info("🚀 SPOT ASSET TRANSFER")
    logger.info(f"  From Account: {from_account_id}")
    logger.info(f"    To Account: {to_account_id}")
    logger.info(f"  Asset:        {asset}")
    logger.info(f"  Quantity:     {qty}")
    logger.info(f"  Symbol:       {symbol}")

    async with ReyaTradingClient() as sender_client:
        base_config = sender_client._config

        # Configure sender client
        sender_client._config = create_trading_client_config(private_key, from_account_id, base_config)
        sender_client._signature_generator = SignatureGenerator(sender_client._config)
        await sender_client.start()

        # Validate sender has sufficient balance
        qty_decimal = Decimal(qty)
        if not await validate_sufficient_balance(sender_client, from_account_id, asset, qty_decimal):
            sys.exit(1)

        # Configure and start receiver client
        async with ReyaTradingClient() as receiver_client:
            receiver_client._config = create_trading_client_config(private_key_2, to_account_id, base_config)
            receiver_client._signature_generator = SignatureGenerator(receiver_client._config)
            await receiver_client.start()

            # Log initial balances (now both clients available)
            initial_from, initial_to = await log_account_balances(
                sender_client, receiver_client, from_account_id, to_account_id, asset, "📊 INITIAL BALANCES"
            )

            # Execute the transfer
            order_fully_matched, tx_hash = await execute_spot_transfer(
                sender_client, receiver_client, from_account_id, to_account_id, symbol, qty
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
                logger.warning(
                    f"⚠️ Partial transfer. Expected: {expected_change} {asset}, Got: {to_change} {asset}"
                )
                sys.exit(1)
            else:
                logger.error(
                    f"❌ Transfer failed. Expected: -{expected_change}/{expected_change}, Got: {from_change}/{to_change}"
                )
                sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
