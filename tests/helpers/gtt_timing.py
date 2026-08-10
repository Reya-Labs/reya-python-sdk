"""Timing constants for the GTT auto-reap fixtures.

GTT expiry is wall-clock (the ME reaper scans on a ~500ms interval), so the reap
tests are REAL waits and every margin here is load-bearing. They live in a
neutral module because the offline suite pins the derivation below, and an
offline test must not import a live e2e module to reach a constant.

The expiry offset is DERIVED from the deployment's settlement headroom rather
than hardcoded. The engine refuses a lifetime that does not outlast that window,
so a fixed 55s offset is refused outright wherever the headroom reaches it —
turning a reap assertion into an admission failure that never reaches the
behaviour under test. Deriving it keeps the fixtures admissible on a
production-headroom deployment and on a local one pinned lower, and keeps the
pre-expiry setup budget constant instead of letting it ride the headroom down.
"""

from sdk.reya_rest_api.config import settlement_headroom_from_env

# EIP-712 signature validity for the create. Must be under the expiry (the GTT
# coupling), and long enough to submit.
GTT_REAP_DEADLINE_OFFSET_S = 20

# The order must be created, confirmed over WS and read back inside this window,
# because the "still resting" assertion below fires once it elapses.
GTT_REAP_SETUP_BUDGET_S = 40

# Assert "still resting" this far before expiry — absorbs ME<->test clock skew.
GTT_REAP_PRE_EXPIRY_MARGIN_S = 15

# Max acceptable lag from expiry to observing CANCELLED (Redis -> indexer -> WS).
GTT_REAP_DETECT_BOUND_S = 40

# Room to observe OPEN and assert "still resting", on top of the headroom the
# engine requires the lifetime to clear.
GTT_REAP_OBSERVATION_WINDOW_S = GTT_REAP_SETUP_BUDGET_S + GTT_REAP_PRE_EXPIRY_MARGIN_S

GTT_REAP_EXPIRY_OFFSET_S = settlement_headroom_from_env() + GTT_REAP_OBSERVATION_WINDOW_S

# Wait budget once polling for the reap starts (from ~pre-expiry to detection).
REAP_WAIT_TIMEOUT_S = GTT_REAP_PRE_EXPIRY_MARGIN_S + GTT_REAP_DETECT_BOUND_S + 10
