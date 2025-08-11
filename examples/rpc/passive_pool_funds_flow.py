from sdk.reya_rpc import StakingParams, UnstakingParams, get_config, stake, unstake


def main():
    """
    Example script demonstrating how to stake rUSD into the passive pool
    and subsequently unstake to retrieve rUSD.
    """

    # Load configuration
    config = get_config()

    # Define the amount in rUSD (scaled by 10^6)
    amount_e6 = int(1e6)

    # Stake rUSD in the passive pool and receive liquidity shares
    result = stake(config, StakingParams(token_amount=amount_e6, min_shares=0))
    shares_amount_e30 = result["shares_amount"]
    print(f"Staking {amount_e6 / 1e6} rUSD resulted in {shares_amount_e30 / 1e30} shares")

    # Unstake liquidity shares to retrieve rUSD
    result = unstake(config, UnstakingParams(shares_amount=shares_amount_e30, min_tokens=0))
    token_amount_e6 = result["token_amount"]
    print(f"Unstaking {shares_amount_e30 / 1e30} shares resulted in {token_amount_e6 / 1e6} rUSD")


if __name__ == "__main__":
    main()
