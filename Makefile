PYTHON ?= python

.PHONY: sync run test lint format typecheck migrate seed

sync:
	uv sync

run:
	uv run fastapi dev app/main.py

test:
	uv run pytest

lint:
	uv run ruff check .

lint-fix:
	uv run ruff check --fix .

format:
	uv run ruff format .

typecheck:
	uv run ty check

migrate:
	uv run alembic upgrade head

seed:
	uv run $(PYTHON) scripts/seed_demo.py
