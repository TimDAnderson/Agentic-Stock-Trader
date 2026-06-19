# Agentic-Stock-Trader — common dev tasks.
# All commands run inside the uv-managed environment.

.DEFAULT_GOAL := help
.PHONY: help install sync test lint typecheck format fmt check demo clean \
	dynamo-up dynamo-down test-int

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install sync: ## Create/refresh the virtualenv from pyproject (incl. dev tools)
	uv sync

test: ## Run the test suite
	uv run pytest

lint: ## Lint + verify formatting (no changes written) — what CI should run
	uv run ruff check src tests examples
	uv run ruff format --check src tests examples

typecheck: ## Static type check with mypy (strict)
	uv run mypy

format fmt: ## Auto-fix lint issues and reformat (single-quote style)
	uv run ruff check --fix src tests examples
	uv run ruff format src tests examples

check: lint typecheck test ## Lint, format-check, type-check, and test

demo: ## Run the synthetic end-to-end backtest demo
	uv run python examples/run_backtest.py

dynamo-up: ## Start DynamoDB Local in the background (docker compose)
	docker compose up -d dynamodb

dynamo-down: ## Stop local infra
	docker compose down

test-int: ## Run integration tests against DynamoDB Local (needs dynamo-up)
	DYNAMODB_ENDPOINT=http://localhost:8000 uv run --extra aws pytest tests/test_state_dynamodb.py -v

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .ruff_cache .coverage htmlcov dist build
	find . -type d -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
