# reya-python-sdk — Python SDK

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
