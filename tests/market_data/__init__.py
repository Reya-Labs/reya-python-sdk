"""Market-data pipeline e2e tests (localnet).

Covers behaviours introduced by the market-data producer/consumer work that the
rest of the suite does not exercise:

- T1 ``test_order_changes_batching`` — batched orderChanges frames (one WS frame
  may carry multiple order events), losslessness and per-/cross-frame ordering.
- T2 ``test_depth_continuity`` — snapshot + absolute per-level diffs (qty "0"
  removes a level), local-book convergence to REST depth, and ``?limit=N``.
- T3 ``test_resync_close`` — matching-engine reseed resync signal (1012 close
  and/or inline FEED_RESET), reconnect+resubscribe, fresh-snapshot consistency.
- T4 ``test_slow_consumer_close`` — 1013 slow-consumer/backpressure close
  (opt-in stress; see the module docstring for why it is skip-by-default).
"""
