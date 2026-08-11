# One definition of "green", used by humans and by CI.
#
# The checks were previously written out in the README, CONTRIBUTING, AGENTS.md
# and the workflow, and drifted: people ran `ruff format` instead of
# `ruff format --check`, or skipped schema validation, and found out from CI.

.DEFAULT_GOAL := help
.PHONY: help install verify lint format typecheck test test-hermetic schemas clean

help:  ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Create the venv and install dependencies
	uv sync

verify: lint typecheck test test-hermetic schemas  ## Run every check CI runs
	@echo "All checks passed."

lint:  ## Lint, and fail on any formatting difference
	uv run ruff check .
	uv run ruff format --check .

format:  ## Apply formatting and autofixes
	uv run ruff format .
	uv run ruff check . --fix

typecheck:  ## Static types
	uv run mypy tfqa/ tests/

test:  ## Unit and CLI tests
	uv run pytest -q

test-hermetic:  ## Tests again with every external tool hidden
	uv run pytest -q --hermetic

schemas:  ## The shipped JSON schemas parse and declare their metadata
	uv run tfqa validate-schemas --output json
	uv run tfqa lint-schemas --output json

clean:  ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
