"""
Cross-wallet rUSD Transfer - Transfer rUSD between wallets.

Transfers rUSD from one margin account to another owned by a DIFFERENT wallet.

Flow:
1. Withdraw rUSD from source account to source wallet (Wallet A)
2. ERC20 transfer rUSD tokens from Wallet A to Wallet B
3. Deposit rUSD from Wallet B into destination account

Requirements:
- CHAIN_ID: The chain ID (1729 for mainnet, 89346162 for testnet)
- PERP_PRIVATE_KEY_1: Private key for Wallet A (source wallet)
- PERP_PRIVATE_KEY_2: Private key for Wallet B (destination wallet)
"""

import os

from dotenv import load_dotenv
from eth_abi import encode

from sdk.reya_rpc import WithdrawParams, get_config, withdraw
from sdk.reya_rpc.types import CommandType


def main():
    """Execute full cross-wallet rUSD transfer."""

    load_dotenv()

    # Configuration
    from_account_id = 8044  # Source account (owned by Wallet A)
    to_account_id = 10000000003  # Destination SPOT account (owned by Wallet B)
    to_wallet_address = "0xfCf3AdeE1CfE963ff270CCdf66AB1C8cdc659C36"  # Wallet B
    amount_rusd = 1  # Amount to transfer (1 rUSD is enough for 5 ETH at 0.01 price)

    # Amount scaled by 10^6 (rUSD has 6 decimals)
    amount_e6 = int(amount_rusd * 1e6)

    print("=" * 60)
    print("CROSS-WALLET rUSD TRANSFER")
    print("=" * 60)
    print(f"  Amount:       {amount_rusd} rUSD")
    print(f"  From Account: {from_account_id}")
    print(f"  To Account:   {to_account_id}")
    print(f"  To Wallet:    {to_wallet_address}")
    print("=" * 60)

    # =========================================================================
    # STEP 1: Withdraw rUSD from source account to Wallet A
    # =========================================================================
    print("\n[Step 1/3] Withdrawing rUSD from margin account to Wallet A...")

    config_a = get_config()  # Uses PRIVATE_KEY from .env (Wallet A)
    wallet_a = config_a["w3account"].address

    print(f"  From Account {from_account_id} → Wallet A ({wallet_a})")

    result = withdraw(config_a, WithdrawParams(account_id=from_account_id, amount=amount_e6))
    tx_hash = result["transaction_receipt"]["transactionHash"].hex()
    print(f"  ✓ Withdrawn: {tx_hash}")

    # =========================================================================
    # STEP 2: ERC20 transfer rUSD from Wallet A to Wallet B
    # =========================================================================
    print("\n[Step 2/3] Transferring rUSD tokens from Wallet A to Wallet B...")

    w3 = config_a["w3"]
    rusd = config_a["w3contracts"]["rusd"]

    # Check balance
    balance = rusd.functions.balanceOf(wallet_a).call()
    print(f"  Wallet A balance: {balance / 1e6} rUSD")

    if balance < amount_e6:
        print(f"  ❌ Insufficient balance! Need {amount_e6 / 1e6} rUSD")
        return

    # Transfer tokens (must sign manually since RPC node doesn't have private key)
    print(f"  Wallet A ({wallet_a}) → Wallet B ({to_wallet_address})")
    account_a = config_a["w3account"]
    tx = rusd.functions.transfer(to_wallet_address, amount_e6).build_transaction(
        {
            "from": account_a.address,
            "nonce": w3.eth.get_transaction_count(account_a.address),
            "chainId": config_a["chain_id"],
        }
    )
    signed_tx = w3.eth.account.sign_transaction(tx, private_key=account_a.key)
    tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
    tx_receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    print(f"  ✓ Transferred: {tx_receipt['transactionHash'].hex()}")

    # =========================================================================
    # STEP 3: Deposit rUSD from Wallet B into destination account
    # =========================================================================
    print("\n[Step 3/3] Depositing rUSD from Wallet B into margin account...")

    # Get Wallet B's private key
    private_key_2 = os.environ.get("PERP_PRIVATE_KEY_2")
    if not private_key_2:
        print("  ❌ PERP_PRIVATE_KEY_2 not set in .env!")
        print("  Please add: PERP_PRIVATE_KEY_2=<wallet_b_private_key>")
        print("  Then run the deposit manually or re-run this script.")
        return

    # Create config for Wallet B
    os.environ["PERP_PRIVATE_KEY_1"] = private_key_2
    config_b = get_config()
    account_b = config_b["w3account"]
    w3_b = config_b["w3"]
    core_b = config_b["w3contracts"]["core"]
    rusd_b = config_b["w3contracts"]["rusd"]

    print(f"  Wallet B ({account_b.address}) → Account {to_account_id}")

    # Step 3a: Approve rUSD to core contract
    print("  Approving rUSD...")
    approve_tx = rusd_b.functions.approve(core_b.address, amount_e6).build_transaction(
        {
            "from": account_b.address,
            "nonce": w3_b.eth.get_transaction_count(account_b.address),
            "chainId": config_b["chain_id"],
        }
    )
    signed_approve = w3_b.eth.account.sign_transaction(approve_tx, private_key=account_b.key)
    approve_hash = w3_b.eth.send_raw_transaction(signed_approve.raw_transaction)
    w3_b.eth.wait_for_transaction_receipt(approve_hash)
    print(f"  ✓ Approved: {approve_hash.hex()}")

    # Step 3b: Deposit into margin account
    inputs_encoded = encode(["(address,uint256)"], [[rusd_b.address, amount_e6]])
    command = (CommandType.Deposit.value, inputs_encoded, 0, 0)
    commands = [command]

    deposit_tx = core_b.functions.execute(to_account_id, commands).build_transaction(
        {
            "from": account_b.address,
            "nonce": w3_b.eth.get_transaction_count(account_b.address),
            "chainId": config_b["chain_id"],
        }
    )
    signed_deposit = w3_b.eth.account.sign_transaction(deposit_tx, private_key=account_b.key)
    deposit_hash = w3_b.eth.send_raw_transaction(signed_deposit.raw_transaction)
    tx_receipt = w3_b.eth.wait_for_transaction_receipt(deposit_hash)
    print(f"  ✓ Deposited: {tx_receipt['transactionHash'].hex()}")

    # =========================================================================
    # DONE
    # =========================================================================
    print("\n" + "=" * 60)
    print("✅ TRANSFER COMPLETE!")
    print(f"   {amount_rusd} rUSD moved from Account {from_account_id} to Account {to_account_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
