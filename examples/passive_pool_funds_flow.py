from reya_actions import get_config
from reya_actions.actions import stake, unstake
from reya_actions.actions import StakingParams, UnstakingParams

def main():
    config = get_config()

    amount = int(1e6)
    result = stake(config, StakingParams(token_amount=amount, min_shares=0))
    shares_amount = result['shares_amount']
    print("shares amount:", shares_amount)
    
    result = unstake(config, UnstakingParams(shares_amount=shares_amount, min_tokens=0))
    token_amount = result['token_amount']
    print("token amount:", token_amount)

if __name__ == "__main__":
    main()
