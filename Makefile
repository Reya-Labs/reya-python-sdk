# Define the shell to use when executing commands
SHELL := /usr/bin/env bash -o pipefail -o errexit

lockfile-update:	## Update poetry.lock
	poetry lock -n

lockfile-update-full:	## Fully regenerate poetry.lock
	poetry lock -n --regenerate

install:	## Install dependencies from poetry.lock
	poetry install -n

install-types:	## Find and install additional types for mypy
	poetry run mypy --install-types --non-interactive ./

E2E_TEST_PATHS ?= tests/engine tests/spot tests/perp tests/api_contract tests/ws_exec
OFFLINE_TEST_PATHS ?= tests
RATE_LIMIT_TEST_PATHS ?= tests/rate_limits
PYTEST_ARGS ?=

e2e:	## Run the live integration suite against the configured environment
	poetry run pytest $(E2E_TEST_PATHS) -ra --tb=short $(PYTEST_ARGS)

test-offline:	## Run every no-network test (parity, validation, rate-limit error surface). No .env or deployment needed.
	poetry run pytest $(OFFLINE_TEST_PATHS) -m offline -ra --tb=short $(PYTEST_ARGS)

e2e-rate-limits:	## Run the live Rate-Limit v1 suite (needs RL_TEST_ENABLED=1; see tests/rate_limits/README.md)
	poetry run pytest $(RATE_LIMIT_TEST_PATHS) -ra --tb=short $(PYTEST_ARGS)

poetry-download:	## Download and install poetry
	curl -sSL https://install.python-poetry.org | python -

lint: pre-commit	## Alias for the pre-commit target

pre-commit:  ## Run linters + formatters via pre-commit, run "make pre-commit hook=black" to run only black
	poetry run pre-commit run --all-files --verbose --show-diff-on-failure --color always $(hook)

check-safety:	## Run safety checks on dependencies
	poetry run safety check --full-report

update-dev-deps:	## Update development dependencies to latest versions
	poetry add -D mypy@latest pre-commit@latest pytest@latest safety@latest coverage@latest pytest-cov@latest
	poetry run pre-commit autoupdate

cleanup: ## Cleanup project
	find . | grep -E "(__pycache__|\.pyc|\.pyo$$)" | xargs rm -rf
	find . | grep -E ".DS_Store" | xargs rm -rf
	find . | grep -E ".mypy_cache" | xargs rm -rf
	find . | grep -E ".pytest_cache" | xargs rm -rf
	rm -rf build/

.PHONY: all $(MAKECMDGOALS)
