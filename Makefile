PYTHON ?= python
HOST ?= 127.0.0.1
PORT ?= 8000

.PHONY: sync run test lint lint-fix format typecheck migrate seed demo-upstream demo-api demo-client

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

demo-upstream:
	uv run $(PYTHON) examples/mock_upstream.py

demo-api:
	uv run uvicorn app.main:app --host $(HOST) --port $(PORT)

demo-client:
	uv run $(PYTHON) examples/client.py
