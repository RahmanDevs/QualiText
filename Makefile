.PHONY: setup dev dev-api dev-ui test test-backend test-frontend lint lint-backend lint-frontend migrate seed docker-up docker-down

UV ?= uv
NPM ?= npm
BACKEND_DIR := backend
FRONTEND_DIR := frontend

setup:
	cd $(BACKEND_DIR) && $(UV) sync --all-extras
	cd $(FRONTEND_DIR) && $(NPM) install

dev:
	@echo "Starting QualiText Fast-Dev stack (API + Frontend)..."
	@$(MAKE) -j2 dev-api dev-ui

dev-api:
	cd $(BACKEND_DIR) && $(UV) run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-ui:
	cd $(FRONTEND_DIR) && $(NPM) run dev

migrate:
	cd $(BACKEND_DIR) && $(UV) run alembic upgrade head

seed:
	cd $(BACKEND_DIR) && $(UV) run python -m scripts.seed

test: test-backend test-frontend

test-backend:
	cd $(BACKEND_DIR) && $(UV) run pytest

test-frontend:
	cd $(FRONTEND_DIR) && $(NPM) test -- --run

lint: lint-backend lint-frontend

lint-backend:
	cd $(BACKEND_DIR) && $(UV) run ruff check .
	cd $(BACKEND_DIR) && $(UV) run ruff format --check .
	cd $(BACKEND_DIR) && $(UV) run mypy --strict app/core app/modules

lint-frontend:
	cd $(FRONTEND_DIR) && $(NPM) run lint
	cd $(FRONTEND_DIR) && $(NPM) run typecheck

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down
