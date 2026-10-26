from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from conftest import ProfileFactory, TokenFactory, auth

pytestmark = pytest.mark.django_db


def test_guest_sees_public_profile(api: APIClient, make_user: ProfileFactory) -> None:
    make_user("ann", bio="Shoegaze")

    response = api.get("/api/v1/users/ANN")

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "ann"
    assert body["bio"] == "Shoegaze"
    assert body["counts"]["followers"] == 0
    assert body["viewer"] == {"is_self": False, "follows": None}
    assert "email" not in body and "status" not in body


def test_unknown_user_is_problem_404(api: APIClient) -> None:
    response = api.get("/api/v1/users/nobody")

    assert response.status_code == 404
    assert response["Content-Type"].startswith("application/problem+json")
    assert response.json()["code"] == "not_found"


def test_blocked_user_hidden_except_for_admins(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory
) -> None:
    make_user("bad", status="blocked")
    viewer = make_user("ben")
    admin = make_user("root")

    assert api.get("/api/v1/users/bad").status_code == 404
    assert auth(api, make_token(viewer)).get("/api/v1/users/bad").status_code == 404
    response = auth(api, make_token(admin, role="ADMIN")).get("/api/v1/users/bad")
    assert response.status_code == 200
    assert response.json()["status"] == "blocked"


def test_own_profile_marks_is_self(api: APIClient, make_user: ProfileFactory, make_token: TokenFactory) -> None:
    me = make_user("cat")

    response = auth(api, make_token(me)).get("/api/v1/users/cat")

    assert response.json()["viewer"] == {"is_self": True, "follows": None}


def test_invalid_token_is_401_even_on_public_reads(api: APIClient, make_user: ProfileFactory) -> None:
    make_user("dora")

    response = auth(api, "not-a-jwt").get("/api/v1/users/dora")

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"
