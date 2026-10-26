# syntax=docker/dockerfile:1
# Notification Service — build context: repository root.

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
COPY services/notification/pyproject.toml services/notification/poetry.lock /app/services/notification/
WORKDIR /app/services/notification
RUN poetry install --only main --no-root
COPY services/notification /app/services/notification
RUN poetry install --only main
# Sources may arrive with restrictive host permissions; the non-root user must read them.
RUN chmod -R a+rX /app

FROM builder AS dev
RUN poetry install --with dev
ENV PATH="/app/services/notification/.venv/bin:${PATH}"
EXPOSE 8000
CMD ["uvicorn", "notifier.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS prod
RUN groupadd --system app && useradd --system --gid app --no-create-home --home-dir /nonexistent app
COPY --from=builder /app /app
ENV PATH="/app/services/notification/.venv/bin:${PATH}"
WORKDIR /app/services/notification
USER app
EXPOSE 8000
CMD ["uvicorn", "notifier.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-access-log"]
