def get_core_sig_nonce(core, account_id: int) -> int:
    core_sig_nonce = core.functions.getAccountOwnerNonce(account_id).call()
    return core_sig_nonce