"""Raw API contract suite — EIP-712 envelope + request validation.

Tests here drive RAW requests (hand-built payloads, real signatures unless
deliberately tampered) and assert SERVER-side rejection. They never produce
fills, so — deliberately — NO balance or position guards are wired for this
directory (moving them out of tests/spot/ also freed them from that
directory's autouse spot_balance_guard, which forced two-account balance
initialization onto tests that never trade).
"""
