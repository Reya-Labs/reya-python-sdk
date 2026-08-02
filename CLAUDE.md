# reya-python-sdk — Python SDK

## Important Notes
* Always read entire files. Otherwise, you don't know what you don't know, and will end up making mistakes, duplicating code that already exists, or misunderstanding the architecture.
* Commit early and often. When working on large tasks, your task could be broken down into multiple logical milestones. After a certain milestone is completed and confirmed to be ok by the user, you should commit it. If you do not, if something goes wrong in further steps, we would need to end up throwing away all the code, which is expensive and time consuming.
* Your internal knowledgebase of libraries might not be up to date. When working with any external library, unless you are 100% sure that the library has a super stable interface, you will look up the latest syntax and usage via either Perplexity (first preference) or web search (less preferred, only use if Perplexity is not available)
* Do not say things like: "x library isn't working so I will skip it". Generally, it isn't working because you are using the incorrect syntax or patterns. This applies doubly when the user has explicitly asked you to use a specific library, if the user wanted to use another library they wouldn't have asked you to use a specific one in the first place.
* Always run linting after making major changes. Otherwise, you won't know if you've corrupted a file or made syntax errors, or are using the wrong methods, or using methods in the wrong way.
* Please organise code into separate files wherever appropriate, and follow general coding best practices about variable naming, modularity, function complexity, file sizes, commenting, etc.
* Code is read more often than it is written, make sure your code is always optimised for readability
* Unless explicitly asked otherwise, the user never wants you to do a "dummy" implementation of any given task. Never do an implementation where you tell the user: "This is how it *would* look like". Just implement the thing.
* Whenever you are starting a new task, it is of utmost importance that you have clarity about the task. You should ask the user follow up questions if you do not, rather than making incorrect assumptions.
* Do not carry out large refactors unless explicitly instructed to do so.
* When starting on a new task, you should first understand the current architecture, identify the files you will need to modify, and come up with a Plan. In the Plan, you will think through architectural aspects related to the changes you will be making, consider edge cases, and identify the best approach for the given task. Get your Plan approved by the user before writing a single line of code.
* If you are running into repeated issues with a given task, figure out the root cause instead of throwing random things at the wall and seeing what sticks, or throwing in the towel by saying "I'll just use another library / do a dummy implementation".
* You are an incredibly talented and experienced polyglot with decades of experience in diverse areas such as software architecture, system design, development, UI & UX, copywriting, and more.
* When doing UI & UX work, make sure your designs are both aesthetically pleasing, easy to use, and follow UI / UX best practices. You pay attention to interaction patterns, micro-interactions, and are proactive about creating smooth, engaging user interfaces that delight users.
* When you receive a task that is very large in scope or too vague, you will first try to break it down into smaller subtasks. If that feels difficult or still leaves you with too many open questions, push back to the user and ask them to consider breaking down the task for you, or guide them through that process. This is important because the larger the task, the more likely it is that things go wrong, wasting time and energy for everyone involved.

## Components
- **REST API Client** (`sdk/reya_rest_api/`) — HTTP client for Trading API (EIP-712 auth)
- **RPC Client** (`sdk/reya_rpc/`) — Web3-based client for on-chain actions
- **WebSocket Client** (`sdk/reya_websocket/`) — Real-time data streaming

## Development Commands
```bash
poetry install              # Install dependencies
poetry shell                # Activate venv
make lint                   # Run all linting (black, isort, flake8, mypy via pre-commit)
make pre-commit hook=black  # Run specific linter
make check-safety           # Security checks
```

## Running Examples
```bash
poetry shell
python -m examples.rest_api.wallet_example
python -m examples.websocket.market_monitoring
python -m examples.rpc.trade_execution
```

## Test tree layout (one axis per directory)
* `tests/engine/` — market-AGNOSTIC matching-engine behavior, every test parametrized `[spot, perp]` via the root-conftest `market_config`/`maker`/`taker` fixtures (lazy: a one-market env never spins up the other market's sessions). This is the home for ALL shared engine behavior including the feature suites — lifecycle (GTC/IOC/cancel/SMP), plus `test_modify_*`, `test_cod_*`, `test_post_only_*`. Select a feature with its marker: `-m modify` / `-m cod` / `-m post_only`. Fill-producing modules assert settlement via the injected `settlement_probe` (spot balance deltas / perp position deltas; `tests/helpers/settlement.py`) and wire per-market cleanup via the autouse `settlement_cleanup_guard`.
* `tests/spot/` — spot PHYSICS only (balance deltas, conservation, spotExecutions surfaces, pre-trade balance checks). `tests/perp/` — perp physics (baseline-relative positions, reduce-only, trigger orders).
* `tests/api_contract/` — raw EIP-712/nonce/deadline envelope validation; live but never trades (no balance/position guards). Pinned to the spot market + a 2-test perp cross-market smoke.
* `tests/ws_exec/` — WS order-entry transport (session semantics, error envelopes).
* `tests/parity/` + `tests/validation/` — OFFLINE (no network), selectable via `pytest -m offline` (also by path, unchanged).
* Selection: `-m spot` / `-m perp` are auto-derived from param ids + hand markers; `-m modify|cod|post_only` cross-cut the feature tests in `engine/` (and their offline guard twins in `validation/`).

## Testing against devnet
* The live suites (`tests/engine`, `tests/spot`, `tests/perp`, `tests/api_contract`, `tests/ws_exec`) run **live against devnet** — they place real orders, fill, settle on-chain, and assert on executions/balances.
* **Before running the suite, kill any long-running example scripts** (e.g. `examples.websocket.perps.depth_market_maker`, any `python -m examples.*`). They maintain resting orders / open positions on the shared devnet test accounts and **pollute test state** — symptoms include `cancelledCount` mismatches, "reduce-only not rejected" (a leftover position exists), and matching against the wrong counterparty. Check with `ps -Ao pid,etime,command | grep -iE "examples\.|market_maker"` and kill stragglers before a run.
* Tests share a small pool of devnet accounts; leftover orders from a crashed/aborted run can also pollute — a clean run starts from no resting orders / no open positions on the test accounts.

## Key Architecture
- REST: client.py (main entry) -> resources/ (endpoints) -> auth/signatures.py (EIP-712) -> models/ (Pydantic)
- RPC: actions/ (tx builders) -> abis/ (contract ABIs) -> config.py (network addresses)
- WebSocket: socket.py (connection manager) -> resources/ (subscriptions) -> config.py

## Networks
- Mainnet: chain_id=1729
- Testnet: chain_id=89346162
- Contract addresses in sdk/reya_rpc/config.py

## Code Quality
- Line length: 120 chars (Black, isort, Pylint)
- Python: 3.12+ required
- Type checking: strict mypy
- Testing: pytest with vcrpy for HTTP recording

## Environment
Create `.env` with: ACCOUNT_ID, PRIVATE_KEY, CHAIN_ID, REYA_WS_URL
