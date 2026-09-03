.PHONY: install lint format typecheck test run worker migrate seed

install:
	uv sync --extra dev

lint:
	uv run ruff check src tests
	uv run black --check src tests

format:
	uv run ruff check --fix src tests
	uv run black src tests

typecheck:
	uv run mypy src

test:
	uv run pytest

run:
	uv run uvicorn app.main:app --reload

worker:
	uv run python -m app.worker

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m app.seed --demo
