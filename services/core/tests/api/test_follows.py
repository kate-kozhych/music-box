from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from conftest import ProfileFactory, TokenFactory, auth
from innotter_common.auth import deny_key, user_state_key
from innotter_core.apps.platform.models import OutboxEvent
from innotter_core.apps.social.models import Follow
from innotter_core.apps.users.models import UserProfile

pytestmark = pytest.mark.django_db


def test_follow_is_idempotent_and_emits_one_event(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory
) -> None:
    ann, ben = make_user("ann"), make_user("ben")
    client = auth(api, make_token(ann))

    assert client.put("/api/v1/users/ben/follow").status_code == 204
    assert client.put("/api/v1/users/ben/follow").status_code == 204

    assert Follow.objects.filter(follower=ann, followee=ben).count() == 1
    events = list(OutboxEvent.objects.values_list("type", flat=True))
    assert events == ["core.follow.created.v1"]
    assert api.get("/api/v1/users/ben").json()["viewer"]["follows"] is True
    assert APIClient().get("/api/v1/users/ben").json()["counts"]["followers"] == 1


def test_unfollow_is_idempotent(api: APIClient, make_user: ProfileFactory, make_token: TokenFactory) -> None:
    ann, _ = make_user("ann"), make_user("ben")
    client = auth(api, make_token(ann))
    client.put("/api/v1/users/ben/follow")

    assert client.delete("/api/v1/users/ben/follow").status_code == 204
    assert client.delete("/api/v1/users/ben/follow").status_code == 204
    assert Follow.objects.count() == 0


def test_guest_cannot_follow(api: APIClient, make_user: ProfileFactory) -> None:
    make_user("ben")

    response = api.put("/api/v1/users/ben/follow")

    assert response.status_code == 401
    assert response.json()["code"] == "token_invalid"


def test_self_follow_is_rejected(api: APIClient, make_user: ProfileFactory, make_token: TokenFactory) -> None:
    ann = make_user("ann")

    response = auth(api, make_token(ann)).put("/api/v1/users/ann/follow")

    assert response.status_code == 422
    assert response.json()["code"] == "self_follow"


@pytest.mark.parametrize("target_status", ["blocked", "deleted"])
def test_cannot_follow_blocked_or_deleted(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory, target_status: str
) -> None:
    ann = make_user("ann")
    make_user("zed", status=target_status)

    response = auth(api, make_token(ann)).put("/api/v1/users/zed/follow")

    assert response.status_code == 404


def test_follow_unknown_user_is_404(api: APIClient, make_user: ProfileFactory, make_token: TokenFactory) -> None:
    ann = make_user("ann")

    assert auth(api, make_token(ann)).put("/api/v1/users/nobody/follow").status_code == 404


def test_projection_is_created_lazily_from_token_claims(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory
) -> None:
    make_user("ben")
    token = make_token(None, username="newbie")

    assert auth(api, token).put("/api/v1/users/ben/follow").status_code == 204
    assert UserProfile.objects.filter(username="newbie", profile_version=0).exists()


def test_revoked_token_is_rejected(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory, auth_redis
) -> None:
    ann, _ = make_user("ann"), make_user("ben")
    token = make_token(ann, jti="revoked-jti")
    auth_redis.set(deny_key("revoked-jti"), "1", ex=60)

    assert auth(api, token).put("/api/v1/users/ben/follow").status_code == 401


def test_blocked_account_token_is_rejected(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory, auth_redis
) -> None:
    ann, _ = make_user("ann"), make_user("ben")
    auth_redis.hset(user_state_key(ann.user_id), mapping={"status": "blocked", "revoked_before": "0"})

    assert auth(api, make_token(ann)).put("/api/v1/users/ben/follow").status_code == 401


def test_lists_are_newest_first_with_cursor_pagination(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory
) -> None:
    star = make_user("star")
    fans = [make_user(f"fan{i}") for i in range(3)]
    for fan in fans:
        auth(APIClient(), make_token(fan)).put("/api/v1/users/star/follow")

    first = api.get("/api/v1/users/star/followers?limit=2").json()
    second = api.get(f"/api/v1/users/star/followers?limit=2&cursor={first['next_cursor']}").json()

    names = [e["user"]["username"] for e in first["items"] + second["items"]]
    assert names == ["fan2", "fan1", "fan0"]
    assert second["next_cursor"] is None
    assert first["items"][0]["viewer_follows"] is None
    following = api.get("/api/v1/users/fan0/following").json()
    assert [e["user"]["username"] for e in following["items"]] == [star.username]


def test_viewer_follows_flag(api: APIClient, make_user: ProfileFactory, make_token: TokenFactory) -> None:
    ann, ben, cat = make_user("ann"), make_user("ben"), make_user("cat")
    auth(APIClient(), make_token(ben)).put("/api/v1/users/cat/follow")
    auth(APIClient(), make_token(cat)).put("/api/v1/users/cat/follow")
    auth(APIClient(), make_token(ann)).put("/api/v1/users/ben/follow")

    items = auth(api, make_token(ann)).get("/api/v1/users/cat/followers").json()["items"]

    assert [(e["user"]["username"], e["viewer_follows"]) for e in items] == [("ben", True)]


def test_blocked_users_are_hidden_from_lists_except_for_admins(
    api: APIClient, make_user: ProfileFactory, make_token: TokenFactory
) -> None:
    star, bad, root = make_user("star"), make_user("bad"), make_user("root")
    auth(APIClient(), make_token(bad)).put("/api/v1/users/star/follow")
    UserProfile.objects.filter(pk=bad.pk).update(status="blocked")

    guest_view = api.get("/api/v1/users/star/followers").json()["items"]
    admin_view = auth(api, make_token(root, role="ADMIN")).get("/api/v1/users/star/followers").json()["items"]

    assert guest_view == []
    assert [e["user"]["username"] for e in admin_view] == ["bad"]
    assert star.username == "star"


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "cursor=%%%"])
def test_bad_paging_parameters(api: APIClient, make_user: ProfileFactory, query: str) -> None:
    make_user("star")

    response = api.get(f"/api/v1/users/star/followers?{query}")

    assert response.status_code == 422
