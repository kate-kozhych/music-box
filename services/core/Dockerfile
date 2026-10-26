# syntax=docker/dockerfile:1
# Innotter Core — build context: repository root.
#   dev  : all dependency groups (compose.dev.yaml, test profile)
#   prod : runtime only, non-root, gunicorn (default in compose.yaml)

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    DJANGO_SETTINGS_MODULE=innotter_core.config.settings.base
WORKDIR /app

FROM base AS builder
# mysqlclient is compiled against the MariaDB Connector/C headers.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config default-libmysqlclient-dev \
 && rm -rf /var/lib/apt/lists/*
RUN pip install "poetry>=2.1,<3"
COPY packages/innotter-common /app/packages/innotter-common
COPY services/core/pyproject.toml services/core/poetry.lock /app/services/core/
WORKDIR /app/services/core
RUN poetry install --only main --no-root
COPY services/core /app/services/core
RUN poetry install --only main
# Sources may arrive with restrictive host permissions; the non-root user must read them.
RUN chmod -R a+rX /app

FROM builder AS dev
RUN poetry install --with dev
ENV PATH="/app/services/core/.venv/bin:${PATH}"
EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]

FROM base AS prod
RUN apt-get update \
 && apt-get install -y --no-install-recommends libmariadb3 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system app \
 && useradd --system --gid app --no-create-home --home-dir /nonexistent app
COPY --from=builder /app /app
ENV PATH="/app/services/core/.venv/bin:${PATH}"
WORKDIR /app/services/core
USER app
EXPOSE 8000
CMD ["gunicorn", "innotter_core.config.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--forwarded-allow-ips", "*", "--timeout", "30"]
