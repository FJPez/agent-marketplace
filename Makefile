PYTHON ?= python
HOST ?= 127.0.0.1
PORT ?= 8000
IMAGE ?= agent-marketplace:local
COMPOSE ?= docker compose
DOCKER_HOST ?= 127.0.0.1
DOCKER_PORT ?= 18000

.PHONY: sync run test lint lint-fix format typecheck migrate seed bootstrap-admin demo-upstream demo-api demo-client demo-provider docker-build docker-run docker-stop docker-smoke

sync:
	uv sync

run:
	uv run uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

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

bootstrap-admin:
	uv run $(PYTHON) scripts/bootstrap_admin.py

demo-upstream:
	uv run $(PYTHON) examples/mock_upstream.py

demo-api:
	uv run uvicorn app.main:app --host $(HOST) --port $(PORT)

demo-client:
	uv run $(PYTHON) examples/client.py

demo-provider:
	uv run $(PYTHON) examples/provider_client.py

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
