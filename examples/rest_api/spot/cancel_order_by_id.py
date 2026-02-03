#!/usr/bin/env python3
"""Cancel a specific order by ID to verify it exists and can be cancelled."""

import asyncio
import sys

from dotenv import load_dotenv

from sdk.reya_rest_api import ReyaTradingClient, get_spot_config

# Order details (example)
ORDER_ID = "18560524635424686088"
SYMBOL = "WETHRUSD"
ACCOUNT_ID = 10000000002


async def main():
    load_dotenv()

    print("=" * 60)
    print("CANCEL ORDER BY ID")
    print("=" * 60)
    print(f"Order ID: {ORDER_ID}")
    print(f"Symbol: {SYMBOL}")
    print(f"Account ID: {ACCOUNT_ID}")
    print("=" * 60)

    # Use account 1 (Maker) since that's account 10000000002
    config = get_spot_config(account_number=1)

    if config.account_id is None:
        print("❌ SPOT_ACCOUNT_ID_1 not set")
        sys.exit(1)

    if config.private_key is None:
        print("❌ SPOT_PRIVATE_KEY_1 not set")
        sys.exit(1)

    if config.account_id != ACCOUNT_ID:
        print(f"⚠️  Warning: Config account {config.account_id} != target account {ACCOUNT_ID}")

    client = ReyaTradingClient(config)
    await client.start()

    try:
        print(f"\n📤 Sending cancel request for order {ORDER_ID}...")
        result = await client.cancel_order(order_id=ORDER_ID, symbol=SYMBOL, account_id=ACCOUNT_ID)
        print(f"✅ Cancel response: {result}")
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"❌ Error cancelling order: {e}")
    finally:
        await client.close()

    print("=" * 60)
    print("Done!")


if __name__ == "__main__":
    asyncio.run(main())
