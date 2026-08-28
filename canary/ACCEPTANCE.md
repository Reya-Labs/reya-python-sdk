# PRO-657 acceptance coverage

This is the branch-local implementation map for the current Linear ticket. It prevents the
credential-free harness from being mistaken for completed cutover evidence.

| Acceptance area | Current branch | Remaining work or live evidence |
| --- | --- | --- |
| Exact devnet1/mainnet identity and release pin | Implemented and offline-tested | Populate a reviewed local profile and run the read-only probes against the candidate release |
| Wallet/account/market allowlists and hard bounds | Implemented and offline-tested | Commander supplies the designated IDs and minimum safe order plan |
| Unique run/client-order identity | Implemented and offline-tested | Runtime supplies one unique run ID |
| Resting order, modify, exact cancel, REST/WS convergence | Injected SDK adapter implemented and offline-tested | Wire clients only after profile review; execute devnet before mainnet |
| Run-owned order cleanup | Implemented and offline-tested | Runtime must also prove no final position delta after controlled matching |
| Operator pause/resume, reconnect, sequence and projection recovery | Injected orchestration implemented and offline-tested | Supply a checkpoint implementation and operator evidence reference; run on devnet candidate |
| Controlled maker/taker match and position delta | Not implemented in the canary harness | Reuse the existing live e2e match primitives with run-owned position accounting |
| Tx/receipt, event/fill, DB, REST/WS correlation | Not implemented | Define or attach the operator-side chain/DB evidence collector |
| Fee/referral attribution | Not implemented | Pin fee/referral configuration and reconcile all affected balances/credits |
| Mark/funding freshness | Not implemented | Obtain the production thresholds and add read-only assertions |
| Frozen legacy-schema rejection | Not implemented | Obtain the canonical payload fixture and stable rejection code from PRO-644 |
| Correlated bust policy | Not implemented | Devnet expected-bust allowlist; mainnet disabled unless separately approved |
| ME/indexer restart | Deliberately impossible through this SDK boundary | Operator performs it; the harness only snapshots, pauses, reconnects, and verifies recovery |
| Timestamped evidence bundle | Preflight, lifecycle, and recovery records implemented | Add the final bundle assembler and attach executed evidence to PRO-261 |

The CLI remains preflight/read-only. No checked-in command can construct a credentialed trading
client, restart a service, query production databases, or submit an order.
