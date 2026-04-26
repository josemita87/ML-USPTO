.PHONY: install test lint format clean

install:
	uv sync --extra dev

test:
	uv run pytest

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -rf data/raw data/processed data/models cdk.out .pytest_cache
