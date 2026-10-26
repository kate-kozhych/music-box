from __future__ import annotations

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_no_missing_migrations() -> None:
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


@pytest.mark.django_db
def test_openapi_schema_is_served() -> None:
    response = APIClient().get("/api/v1/schema?format=json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/users/{username}/follow" in paths
    assert "bearerAuth" in response.json()["components"]["securitySchemes"]
