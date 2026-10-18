# Music Box 

A music-oriented social network: search albums (MusicBrainz), rate and review them, keep a listening diary, post, follow people, and see it all in a unified feed with in-app notifications. 

Architecture: three services — **Identity** (FastAPI + PostgreSQL), **Core** (Django/DRF + Celery + MySQL), **Notification** (FastAPI + MongoDB) + an Nginx gateway and a React SPA, connected by RabbitMQ events. Details in [`docs/architecture.md`](docs/architecture.md).

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.13 (`.python-version`) |
| Poetry | 2.x |
| Node.js | 24 LTS (`.nvmrc`) |
| Docker Engine + Compose v2 | ≥ 24 (from B-02) |
| GNU Make | any recent |

## Getting started

```bash
cp .env.example .env     # every variable is commented
make lock                # first time only: generate poetry.lock / package-lock.json
make install             # poetry install per project + npm ci
make lint                # ruff, mypy, import-linter, service isolation, eslint, prettier, tsc
make test                # pytest per project + jest
```

Run `make help` for every target. `up`, `down`, `reset`, `seed`, `test-e2e`, `load-test` and `similarity` are declared now and print which backlog item enables them.

## Repository layout

```
packages/musicbox-common/   shared library (auth, events, errors, pagination, logging) — B-46
services/identity/          accounts, sessions, avatars, administration (vertical slices)
services/core/              Django project; 12 apps under innotter_core/apps/
services/notification/      inbox consumer, notification rules, user snapshots
frontend/                   React 19 + TypeScript + Vite SPA, Jest, Storybook
gateway/                    Nginx config + SPA image
contracts/                  event JSON Schemas
e2e/  load/  tools/         Playwright, k6, helper scripts
docs/                       requirements, architecture, ADRs, diagrams, backlog
```

Each Python project has its own `pyproject.toml` (Poetry 2) so every container gets only its own dependencies; `innotter-common` is a path dependency. Ruff settings are shared from the root `ruff.toml`; module boundaries are declared per service in `.importlinter`.

## Configuration

All settings come from one root `.env`, prefixed per service: `IDENTITY_*`, `CORE_*`, `NOTIF_*`, `GW_*`. `.env` is git-ignored; `.env.example` holds local demo defaults. The music catalog runs **offline** by default (`CORE_CATALOG_MODE=offline`).

