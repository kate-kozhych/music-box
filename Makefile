# Innotter 2.0 — one command per developer task (FR-066, ADR-0008).
# Run `make help` for the list.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE      ?= docker compose
COMPOSE_DEV  := $(COMPOSE) -f compose.yaml -f compose.dev.yaml

PY_LIB       := packages/innotter-common
PY_SERVICES  := services/identity services/core services/notification
PY_PROJECTS  := $(PY_LIB) $(PY_SERVICES)
FRONTEND     := frontend
E2E          := e2e

require_compose = @test -f compose.yaml || { echo "compose.yaml not found — the Compose stack is added in B-02."; exit 1; }

.PHONY: help
help: ## List available targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# --- Setup -------------------------------------------------------------------

.PHONY: install
install: ## Install all Python (Poetry) and Node dependencies from lockfiles
	@for p in $(PY_PROJECTS); do echo "==> poetry install ($$p)"; (cd $$p && poetry install --no-interaction); done
	@echo "==> npm ci ($(FRONTEND))"; cd $(FRONTEND) && npm ci

.PHONY: lock
lock: ## (Re)generate poetry.lock and package-lock.json files
	@for p in $(PY_PROJECTS); do echo "==> poetry lock ($$p)"; (cd $$p && poetry lock); done
	@echo "==> npm install ($(FRONTEND))"; cd $(FRONTEND) && npm install --package-lock-only
	@echo "==> npm install ($(E2E))"; cd $(E2E) && npm install --package-lock-only

# --- Quality -----------------------------------------------------------------

.PHONY: lint
lint: lint-py lint-fe ## Lint, format-check, type-check and boundary-check everything

.PHONY: lint-py
lint-py: ## Ruff + mypy for every Python project, import-linter for services
	@for p in $(PY_PROJECTS); do \
	  echo "==> lint $$p"; \
	  (cd $$p && poetry run ruff check . && poetry run ruff format --check . && poetry run mypy); \
	done
	@for p in $(PY_SERVICES); do echo "==> import contracts $$p"; (cd $$p && poetry run lint-imports); done
	@python3 tools/check_service_isolation.py

.PHONY: lint-fe
lint-fe: ## ESLint, Prettier check and TypeScript for the SPA
	cd $(FRONTEND) && npm run lint && npm run format:check && npm run typecheck

.PHONY: format
format: ## Auto-format Python (Ruff) and frontend (Prettier) sources
	@for p in $(PY_PROJECTS); do (cd $$p && poetry run ruff check --fix . && poetry run ruff format .); done
	cd $(FRONTEND) && npm run format

# --- Tests -------------------------------------------------------------------

.PHONY: test
test: test-py test-fe ## Run all unit/integration test suites

.PHONY: test-py
test-py: test-common test-docker ## Python tests: shared library locally, services in Docker

.PHONY: test-common
test-common: ## pytest for innotter-common (no infrastructure needed)
	cd $(PY_LIB) && poetry run pytest

.PHONY: test-docker
test-docker: test-identity test-core ## Service tests against real PostgreSQL/MySQL/Redis in Compose

.PHONY: test-identity
test-identity: ## Identity API + integration tests (PostgreSQL 17, Redis)
	$(COMPOSE) --profile test run --rm --build identity-tests

.PHONY: test-core
test-core: ## Core API + integration tests (MySQL 8.4, Redis)
	$(COMPOSE) --profile test run --rm --build core-tests

.PHONY: smoke
smoke: ## End-to-end smoke test through the gateway (stack must be up)
	python3 tools/smoke_api.py http://localhost:$${GW_HTTP_PORT:-8080}

.PHONY: test-fe
test-fe: ## Jest + React Testing Library
	cd $(FRONTEND) && npm test

.PHONY: test-e2e
test-e2e: ## Playwright E2E suite against a running stack (B-41)
	$(require_compose)
	cd $(E2E) && npx playwright test

# --- Stack -------------------------------------------------------------------

.PHONY: up
up: ## Start the full stack (prod targets, offline catalog)
	$(require_compose)
	$(COMPOSE) up --build -d

.PHONY: ps
ps: ## Container status and health
	$(COMPOSE) ps

.PHONY: logs
logs: ## Follow logs of all services
	$(COMPOSE) logs -f --tail=100

.PHONY: up-dev
up-dev: ## Start the stack with the dev override (bind mounts, autoreload, Vite)
	$(require_compose)
	$(COMPOSE_DEV) up --build

.PHONY: down
down: ## Stop the stack, keep volumes
	$(require_compose)
	$(COMPOSE) down

.PHONY: reset
reset: ## Stop the stack and DELETE all volumes (databases, keys, avatars)
	$(require_compose)
	$(COMPOSE) down -v --remove-orphans

.PHONY: seed
seed: ## Run the idempotent seed job (admin, demo users, catalog fixtures)
	$(require_compose)
	$(COMPOSE) run --rm seed

# --- Evaluation --------------------------------------------------------------

.PHONY: load-test
load-test: ## k6 load profiles LP-1/LP-2 (B-42)
	$(require_compose)
	@test -n "$$(ls load/*.js 2>/dev/null)" || { echo "No k6 scripts yet — added in B-42."; exit 1; }
	$(COMPOSE) --profile tools run --rm k6

.PHONY: similarity
similarity: ## jscpd clone check vs a read-only legacy export (B-53)
	@test -x tools/similarity_check.sh || { echo "tools/similarity_check.sh is added in B-53."; exit 1; }
	tools/similarity_check.sh
