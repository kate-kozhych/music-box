# syntax=docker/dockerfile:1
# Identity Service — build context: repository root.
#   dev  : all dependency groups, autoreload (compose.dev.yaml, test profile)
#   prod : runtime dependencies only, non-root (default in compose.yaml)

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true
WORKDIR /app

FROM base AS builder
RUN pip install "poetry>=2.1,<3"
COPY packages/innotter-common /app/packages/innotter-common
COPY services/identity/pyproject.toml services/identity/poetry.lock /app/services/identity/
WORKDIR /app/services/identity
RUN poetry install --only main --no-root
COPY services/identity /app/services/identity
RUN poetry install --only main
# Sources may arrive with restrictive host permissions; the non-root user must read them.
RUN chmod -R a+rX /app

FROM builder AS dev
RUN poetry install --with dev
ENV PATH="/app/services/identity/.venv/bin:${PATH}"
EXPOSE 8000
CMD ["uvicorn", "identity.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/services/identity/src", "--reload-dir", "/app/packages/innotter-common/src"]

FROM base AS prod
RUN groupadd --system app \
 && useradd --system --gid app --no-create-home --home-dir /nonexistent app \
 && mkdir -p /var/lib/identity/keys /var/lib/identity/avatars \
 && chown -R app:app /var/lib/identity
COPY --from=builder /app /app
ENV PATH="/app/services/identity/.venv/bin:${PATH}"
WORKDIR /app/services/identity
USER app
EXPOSE 8000
CMD ["uvicorn", "identity.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
