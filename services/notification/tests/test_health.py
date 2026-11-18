import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from notifier.app import create_app
from notifier.settings import Settings

pytestmark = pytest.mark.integration


def settings_without_consumers() -> Settings:
    return Settings(consumers_enabled=False)  # type: ignore[call-arg]


def test_health_with_real_dependencies() -> None:
    with TestClient(create_app(settings_without_consumers())) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["mongo"] == "ok"
    assert response.json()["redis"] == "ok"
    assert response.json()["consumer"] == "disabled"


@pytest.mark.parametrize("dependency", ["mongo", "redis"])
def test_readiness_reports_unavailable_dependency(dependency: str) -> None:
    settings = settings_without_consumers()
    if dependency == "mongo":
        settings.mongo_url = SecretStr("mongodb://127.0.0.1:1/?connectTimeoutMS=200")
    else:
        settings.redis_auth_url = "redis://127.0.0.1:1/12"
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/live").status_code == 200
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()[dependency] == "unavailable"
    other = "redis" if dependency == "mongo" else "mongo"
    assert response.json()[other] == "ok"
