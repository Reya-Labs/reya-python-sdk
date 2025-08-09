# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Important Notes
* Always read entire files. Otherwise, you don’t know what you don’t know, and will end up making mistakes, duplicating code that already exists, or misunderstanding the architecture.
* Commit early and often. When working on large tasks, your task could be broken down into multiple logical milestones. After a certain milestone is completed and confirmed to be ok by the user, you should commit it. If you do not, if something goes wrong in further steps, we would need to end up throwing away all the code, which is expensive and time consuming.
* Your internal knowledgebase of libraries might not be up to date. When working with any external library, unless you are 100% sure that the library has a super stable interface, you will look up the latest syntax and usage via either Perplexity (first preference) or web search (less preferred, only use if Perplexity is not available)
* Do not say things like: “x library isn’t working so I will skip it”. Generally, it isn’t working because you are using the incorrect syntax or patterns. This applies doubly when the user has explicitly asked you to use a specific library, if the user wanted to use another library they wouldn’t have asked you to use a specific one in the first place.
* Always run linting after making major changes. Otherwise, you won’t know if you’ve corrupted a file or made syntax errors, or are using the wrong methods, or using methods in the wrong way.
* Please organise code into separate files wherever appropriate, and follow general coding best practices about variable naming, modularity, function complexity, file sizes, commenting, etc.
* Code is read more often than it is written, make sure your code is always optimised for readability
* Unless explicitly asked otherwise, the user never wants you to do a “dummy” implementation of any given task. Never do an implementation where you tell the user: “This is how it *would* look like”. Just implement the thing.
* Whenever you are starting a new task, it is of utmost importance that you have clarity about the task. You should ask the user follow up questions if you do not, rather than making incorrect assumptions.
* Do not carry out large refactors unless explicitly instructed to do so.
* When starting on a new task, you should first understand the current architecture, identify the files you will need to modify, and come up with a Plan. In the Plan, you will think through architectural aspects related to the changes you will be making, consider edge cases, and identify the best approach for the given task. Get your Plan approved by the user before writing a single line of code.
* If you are running into repeated issues with a given task, figure out the root cause instead of throwing random things at the wall and seeing what sticks, or throwing in the towel by saying “I’ll just use another library / do a dummy implementation”.
* You are an incredibly talented and experienced polyglot with decades of experience in diverse areas such as software architecture, system design, development, UI & UX, copywriting, and more.
* When doing UI & UX work, make sure your designs are both aesthetically pleasing, easy to use, and follow UI / UX best practices. You pay attention to interaction patterns, micro-interactions, and are proactive about creating smooth, engaging user interfaces that delight users.
* When you receive a task that is very large in scope or too vague, you will first try to break it down into smaller subtasks. If that feels difficult or still leaves you with too many open questions, push back to the user and ask them to consider breaking down the task for you, or guide them through that process. This is important because the larger the task, the more likely it is that things go wrong, wasting time and energy for everyone involved.


## Project Overview

This is the Reya Python SDK, providing Python interfaces for interacting with the Reya ecosystem. The SDK consists of three main components:

- **REST API Client** (`sdk/reya_rest_api/`) - HTTP client for Reya's Trading API
- **RPC Client** (`sdk/reya_rpc/`) - Web3-based client for on-chain actions
- **WebSocket Client** (`sdk/reya_websocket/`) - Real-time data streaming client

## Development Commands

### Setup and Installation
```bash
# Install dependencies
poetry install

# Activate virtual environment
poetry shell
# Or use: source $(poetry env info --path)/bin/activate
```

### Code Quality and Testing
```bash
# Run all linting and formatting (via pre-commit)
make lint
# Or: make pre-commit

# Run specific linter only
make pre-commit hook=black
make pre-commit hook=isort
make pre-commit hook=flake8
make pre-commit hook=mypy

# Install additional type stubs for mypy
make install-types

# Run security checks
make check-safety

# Clean build artifacts
make cleanup
```

### Dependency Management
```bash
# Update poetry.lock
make lockfile-update

# Fully regenerate poetry.lock
make lockfile-update-full

# Update dev dependencies to latest
make update-dev-deps
```

### Running Examples
```bash
# Activate environment first
poetry shell

# Run examples using module notation
python -m examples.rest_api.wallet_example
python -m examples.websocket.market_monitoring
python -m examples.rpc.trade_execution
```

## Architecture Overview

### REST API Client Architecture
The REST API client (`sdk/reya_rest_api/`) follows a resource-based pattern:

- **Client** (`client.py`) - Main entry point with signature authentication
- **Resources** (`resources/`) - Organized by API endpoints (wallet, markets, orders, assets, prices)
- **Auth** (`auth/signatures.py`) - EIP-712 signature generation for authenticated requests
- **Models** (`models/`) - Pydantic models for request/response data
- **Config** (`config.py`) - Configuration management with environment variable support

### RPC Client Architecture  
The RPC client (`sdk/reya_rpc/`) provides Web3-based blockchain interactions:

- **Actions** (`actions/`) - High-level transaction builders for common operations
- **ABIs** (`abis/`) - Smart contract ABIs for all supported contracts
- **Config** (`config.py`) - Network-specific contract addresses and configuration
- **Utils** (`utils/`) - Core transaction execution utilities

### WebSocket Client Architecture
The WebSocket client (`sdk/reya_websocket/`) offers resource-oriented real-time data access:

- **Socket** (`socket.py`) - Main WebSocket connection manager with auto-reconnection
- **Resources** (`resources/`) - Market, wallet, and price subscription managers
- **Config** (`config.py`) - WebSocket connection configuration

### Key Configuration Patterns

The codebase uses environment variables extensively for configuration:

- **Trading API**: Uses `TradingConfig.from_env()` to load API URLs, authentication, etc.
- **RPC**: Uses `get_config()` to load chain IDs, contract addresses, private keys
- **WebSocket**: Uses `WebSocketConfig.from_env()` for connection parameters

### Smart Contract Integration

The RPC client supports two main networks:
- **Mainnet** (chain_id=1729): Production Reya network
- **Testnet** (chain_id=89346162): Testing environment

Contract addresses are network-specific and configured in `sdk/reya_rpc/config.py`.

### Code Quality Configuration

- **Line length**: 120 characters (Black, isort, Pylint)
- **Python version**: 3.12+ required, 3.10 for type checking
- **Type checking**: Strict mypy configuration with comprehensive checks
- **Pre-commit hooks**: Automated formatting and linting on commit

## Environment Setup Requirements

Create `.env` file with:
```
ACCOUNT_ID=your_account_id
PRIVATE_KEY=your_private_key  
CHAIN_ID=1729  # or 89346162 for testnet
REYA_WS_URL=wss://ws.reya.xyz/
```

## Testing Approach

The project uses pytest with additional packages:
- `pytest-recording` and `vcrpy` for HTTP request/response recording
- `pytest-cov` for coverage reporting
- Test files should be placed alongside source code or in dedicated test directories
