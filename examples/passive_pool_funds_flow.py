from reya_actions import get_config
from reya_actions.actions import stake, unstake
from reya_actions.actions import StakingParams, UnstakingParams

def main():
    config = get_config()

    amount_e6 = int(1e6)
    result = stake(config, StakingParams(token_amount=amount_e6, min_shares=0))
    shares_amount_e30 = result['shares_amount']
    print(f'Staking {amount_e6 / 1e6} rUSD resulted in {shares_amount_e30 / 1e30} shares')
    
    result = unstake(config, UnstakingParams(shares_amount=shares_amount_e30, min_tokens=0))
    token_amount_e6 = result['token_amount']
    print(f'Unstaking {shares_amount_e30 / 1e30} shares resulted in {token_amount_e6 / 1e6} rUSD')

if __name__ == "__main__":
    main()
