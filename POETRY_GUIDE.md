# Poetry Usage Guide for Reya Python SDK

This guide provides detailed instructions for using Poetry to manage your development environment for the Reya Python SDK.

## Prerequisites

Before getting started, ensure you have:

1. Python 3.12 or higher installed
2. [Poetry](https://python-poetry.org/docs/#installation) installed

If you need to install Poetry, use one of the following methods:

```bash
# Using pipx (recommended)
pipx install poetry

# Using the official installer
curl -sSL https://install.python-poetry.org | python3 -
```

## Initial Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Reya-Labs/reya-python-sdk.git
cd reya-python-sdk
```

### 2. Install Dependencies

From the root directory of the project, run:

```bash
poetry install
```

This command:
- Creates a new virtual environment
- Installs all dependencies specified in `pyproject.toml`
- Installs the SDK itself in development mode

## Using the Poetry Environment

### Activating the Environment

Before running any SDK code or examples, activate the Poetry virtual environment:

```bash
poetry shell
```

Your command prompt will change to indicate you're in the Poetry environment.

### Running Examples

Once in the Poetry shell, you can run examples directly:

```bash
python -m examples.trading.order_entry
python -m examples.basic_market_data
```

### Running Individual Scripts

You can also run individual Python files:

```bash
python examples/trading/account_info.py
```

### Deactivating the Environment

To exit the Poetry environment, either:

```bash
exit
```

or press `Ctrl+D`.

## Managing Dependencies

### Adding New Dependencies

To add a new package:

```bash
poetry add package_name
```

For development-only dependencies:

```bash
poetry add --group dev package_name
```

### Updating Dependencies

Update all dependencies to their latest versions according to your version constraints:

```bash
poetry update
```

Update a specific package:

```bash
poetry update package_name
```

### Exporting Requirements

If you need a `requirements.txt` file (e.g., for a deployment that doesn't use Poetry):

```bash
poetry export -f requirements.txt --output requirements.txt
```

Include development dependencies:

```bash
poetry export --with dev -f requirements.txt --output dev-requirements.txt
```

## Project Structure

The Reya Python SDK is now structured as a monorepo with all modules included in a single Poetry project:

- `reya_trading/`: The trading API client
- `reya_data_feed/`: WebSocket API client for real-time data
- `reya_actions/`: Blockchain action helpers
- `examples/`: Example scripts demonstrating SDK usage

## Troubleshooting

### Dependency Conflicts

If you encounter dependency conflicts, try the following:

```bash
poetry lock --no-update
poetry install
```

### Python Version Issues

Ensure you're using Python 3.12 or higher as specified in the `pyproject.toml`:

```bash
python --version
```

You can specify a different Python interpreter for Poetry to use:

```bash
poetry env use /path/to/python3.12
```

### Environment Issues

If you encounter issues with the Poetry environment:

```bash
poetry env info
```

To recreate the environment from scratch:

```bash
poetry env remove --all
poetry install
```

## Getting Help

If you need additional help with Poetry, consult the [official Poetry documentation](https://python-poetry.org/docs/).

For SDK-specific issues, please refer to the README or open a ticket on the Reya [Discord](https://discord.com/invite/reyaxyz).
