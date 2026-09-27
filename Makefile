PYTHON ?= python
HOST ?= 127.0.0.1
PORT ?= 8000
IMAGE ?= agent-marketplace:local
COMPOSE ?= docker compose
DOCKER_HOST ?= 127.0.0.1
DOCKER_PORT ?= 18000

.PHONY: sync run worker test test-unit test-serial lint lint-fix format typecheck migrate migrate-check seed bootstrap-admin demo-api docker-build docker-run docker-stop docker-smoke

sync:
	uv sync

run:
	uv run uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

worker:
	uv run python -m app.worker

test:
	uv run pytest -n 2

test-unit:
	uv run pytest tests/unit

test-serial:
	uv run pytest -n 0

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

migrate-check:
	uv run alembic upgrade head
	uv run alembic check

seed:
	uv run $(PYTHON) scripts/seed_demo.py

bootstrap-admin:
	uv run $(PYTHON) scripts/bootstrap_admin.py

demo-api:
	uv run uvicorn app.main:app --host $(HOST) --port $(PORT)

docker-build:
	docker build -t $(IMAGE) .

docker-run:
	$(COMPOSE) up --build -d --wait postgres redis app

docker-stop:
	$(COMPOSE) down

docker-smoke:
	@curl --fail --silent --show-error http://$(DOCKER_HOST):$(DOCKER_PORT)/health/live
	@printf '\n'
	@curl --fail --silent --show-error http://$(DOCKER_HOST):$(DOCKER_PORT)/health/ready
	@printf '\n'
