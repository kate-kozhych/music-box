from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
PASSWORD = "Secret123"
ADMIN_USERNAME = os.environ.get("IDENTITY_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("IDENTITY_ADMIN_PASSWORD", "change-me-Admin-Passw0rd")
FIXTURES = Path(__file__).resolve().parent.parent / "services/core/fixtures/catalog/release-groups.json"

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


class SmokeError(Exception):
    pass


def decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw.decode(errors="replace").strip()


def call(
    method: str, path: str, body: Any = None, token: str | None = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)  # noqa: S310
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, decode(response.read())
    except urllib.error.HTTPError as error:
        return error.code, decode(error.read())


PNG_HEADER = b"\x89PNG\r\n\x1a\n"
MAX_AVATAR_BYTES = 2 * 1024 * 1024


def png(size: int) -> bytes:
    return PNG_HEADER + b"\x00" * (size - len(PNG_HEADER))


def upload_avatar(data: bytes, token: str | None = None, filename: str = "avatar.png") -> tuple[int, Any]:
    boundary = "----smoke" + secrets.token_hex(16)
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
            b"Content-Type: application/octet-stream\r\n\r\n",
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(  # noqa: S310
        BASE + "/api/v1/account/avatar", data=body, method="PUT"
    )
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    try:
        with opener.open(request, timeout=30) as response:
            return response.status, decode(response.read())
    except urllib.error.HTTPError as error:
        return error.code, decode(error.read())


def fetch_bytes(path: str) -> tuple[int, bytes]:
    request = urllib.request.Request(BASE + path, method="GET")  # noqa: S310
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def step(name: str, condition: bool, detail: Any = "") -> None:
    sys.stdout.write(f"{'PASS' if condition else 'FAIL'}  {name}\n")
    if not condition:
        raise SmokeError(f"{name}: {detail}")


def wait_for(name: str, predicate: Any, timeout: float = 15.0) -> Any:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(0.5)
    step(name, False, last)
    return None


def recorded_release_group() -> Any:
    manifest = FIXTURES.parent / "manifest.json"
    if not manifest.exists() or json.loads(manifest.read_text(encoding="utf-8")).get("source") != "MusicBrainz":
        return None
    return json.loads(FIXTURES.read_text(encoding="utf-8"))["release-groups"][0]


def check_seeded_album() -> str:
    row = recorded_release_group()
    step("recorded MusicBrainz fixtures are bundled", row is not None, FIXTURES)
    query = urllib.parse.urlencode({"q": f"{row['title']} {row['artist-credit'][0]['name']}", "size": 50})
    wait_for(
        "album search finds a seeded fixture",
        lambda: row["id"] in [i["mbid"] for i in call("GET", f"/api/v1/albums?{query}")[1].get("items", [])],
    )
    status, page = call("GET", f"/api/v1/albums?{query}")
    step("search reports offline mode and attribution", page["external_status"] == "offline" and page["attribution"]["source"]["name"] == "MusicBrainz", page)
    item = next(i for i in page["items"] if i["mbid"] == row["id"])
    step("offline search results carry no remote artwork", item["cover_url"] is None, item)
    status, album = call("GET", f"/api/v1/albums/{item['id']}")
    step("album page -> 200", status == 200 and album["title"] == row["title"], album)
    status, same = call("GET", f"/api/v1/albums/mbid/{row['id']}")
    step("album by MBID -> the same album", status == 200 and same["id"] == item["id"], same)
    return str(item["id"])


def check_catalog() -> str:
    album_id = check_seeded_album()
    status, page = call("GET", "/api/v1/albums?q=homogenic")
    step("offline search -> 200 + attribution", status == 200 and page["external_status"] == "offline" and "attribution" in page, page)
    status, body = call("GET", "/api/v1/albums?q=a")
    step("a one-character query -> 422", status == 422 and body["code"] == "validation_error", body)
    status, body = call("GET", "/api/v1/albums/mbid/not-an-mbid")
    step("a malformed MBID -> 422", status == 422, body)
    status, body = call("GET", "/api/v1/albums/mbid/ffffffff-ffff-4fff-8fff-ffffffffffff")
    step("an MBID outside the fixtures -> 404 offline", status == 404, body)
    return album_id


def check_music_logging(album_id: str, username: str, token: str) -> None:
    album = f"/api/v1/albums/{album_id}"
    status, body = call("PUT", f"{album}/rating", {"value": 4}, token=token)
    step("rate an album -> 200 + aggregates", status == 200 and body["album_stats"]["count"] >= 1, body)
    status, body = call("PUT", f"{album}/rating", {"value": 4})
    step("guest rating -> 401", status == 401, body)
    status, review = call("POST", f"{album}/reviews", {"body": "Smoke review", "rating": 4.5}, token=token)
    step("write a review with a rating -> 201", status == 201 and review["rating"] == 4.5, review)
    status, body = call("POST", f"{album}/reviews", {"body": "Again"}, token=token)
    step("a second review -> 409 review_exists", status == 409 and body["code"] == "review_exists", body)
    status, page = call("GET", f"{album}/reviews?sort=popular")
    step("guests read community reviews", status == 200 and review["id"] in [i["id"] for i in page["items"]], page)
    status, body = call("GET", album, token=token)
    step("album page shows my review", body["viewer"]["review_id"] == review["id"], body)
    entries = []
    for visibility in ("public", "private"):
        status, entry = call("POST", "/api/v1/diary-entries", {"album_id": album_id, "visibility": visibility}, token=token)
        step(f"log a {visibility} listen -> 201", status == 201, entry)
        entries.append(entry)
    step("the second listen is a re-listen", entries[1]["relisten"] is True, entries[1])
    status, body = call("GET", f"/api/v1/diary-entries/{entries[1]['id']}")
    step("a private entry is 404 for guests", status == 404, body)
    _, page = call("GET", f"/api/v1/users/{username}/diary")
    step("guests see only public entries", [i["id"] for i in page["items"]] == [entries[0]["id"]], page)
    _, body = call("GET", f"/api/v1/users/{username}")
    status, page = call("GET", "/api/v1/discover/albums?sort=popular&size=50")
    step("guests browse popular albums, rated first", status == 200 and album_id in [i["id"] for i in page["items"]] and page["items"][0]["rating_count"] > 0, page)
    counts = [body["counts"][name] for name in ("ratings", "reviews", "diary_entries")]
    step("profile counts exclude private entries", counts == [1, 1, 1], body)


def login(identifier: str, password: str) -> str:
    status, body = call("POST", "/api/v1/auth/login", {"identifier": identifier, "password": password})
    step(f"login {identifier} -> 200", status == 200, body)
    return str(body["access_token"])


def check_social(album_id: str, author: str, author_token: str, reader: str, reader_token: str) -> None:
    status, post = call("POST", "/api/v1/posts", {"body": "Smoke post", "album_id": album_id}, token=author_token)
    step("publish a post about an album -> 201", status == 201 and post["album"]["id"] == album_id, post)
    path = f"/api/v1/posts/{post['id']}"
    status, body = call("POST", "/api/v1/posts", {"body": "x"})
    step("guest post -> 401", status == 401, body)
    status, page = call("GET", f"/api/v1/users/{author}/posts")
    step("guests read profile posts", status == 200 and [i["id"] for i in page["items"]] == [post["id"]], page)
    status, comment = call("POST", f"{path}/comments", {"body": "Smoke comment"}, token=reader_token)
    step("comment on a post -> 201", status == 201 and comment["author"]["username"] == reader, comment)
    like = f"/api/v1/comments/{comment['id']}/like"
    for target in (f"{path}/like", f"{path}/like", like):
        status, body = call("PUT", target, token=reader_token)
        step("like -> 204 (idempotent)", status == 204, body)
    _, body = call("GET", path, token=reader_token)
    step("the post shows exact counters and liked_by_me", (body["like_count"], body["comment_count"], body["liked_by_me"]) == (1, 1, True), body)
    status, body = call("PATCH", f"/api/v1/comments/{comment['id']}", {"body": "x"}, token=author_token)
    step("the post author cannot edit the comment -> 403", status == 403, body)
    admin = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    moderation = f"/api/v1/admin/moderation/posts/{post['id']}"
    status, body = call("POST", f"{moderation}/hide", {"reason": "smoke"}, token=reader_token)
    step("a user cannot moderate -> 403", status == 403, body)
    status, body = call("POST", f"{moderation}/hide", {"reason": "smoke"}, token=admin)
    step("an admin hides the post -> 204", status == 204, body)
    status, body = call("POST", f"{moderation}/hide", {"reason": "smoke"}, token=admin)
    step("hiding again -> 409 already_hidden", status == 409 and body["code"] == "already_hidden", body)
    step("the hidden post is 404 for guests", call("GET", path)[0] == 404)
    status, body = call("GET", path, token=author_token)
    step("the author sees the reason", status == 200 and body["hidden_reason"] == "smoke", body)
    status, body = call("PUT", f"{path}/like", token=reader_token)
    step("hidden content cannot be liked -> 404", status == 404, body)
    status, body = call("POST", f"{moderation}/restore", token=admin)
    step("an admin restores the post -> 204", status == 204, body)
    status, log = call("GET", "/api/v1/admin/moderation/log?limit=2", token=admin)
    step("the moderation log records both actions", [i["action"] for i in log["items"]] == ["restore", "hide"], log)
    status, body = call("GET", "/api/v1/admin/users/moderation-log", token=admin)
    step("the user moderation log is served by Identity", status == 200 and "items" in body, body)
    status, body = call("DELETE", f"/api/v1/comments/{comment['id']}", token=reader_token)
    step("delete my comment -> 204", status == 204, body)
    _, body = call("GET", path)
    step("the comment count follows", body["comment_count"] == 0, body)
    status, body = call("DELETE", path, token=author_token)
    step("delete my post -> 204", status == 204, body)
    step("the deleted post is 404", call("GET", path)[0] == 404)


def feed_objects(path: str, token: str | None = None) -> list[str]:
    status, page = call("GET", path, token=token)
    step(f"GET {path} -> 200", status == 200, page)
    return [item["object"]["id"] for item in page["items"]]


def check_feed(author: str, author_token: str, reader_token: str) -> None:
    status, body = call("GET", "/api/v1/feed")
    step("guest home feed -> 401", status == 401, body)
    status, post = call("POST", "/api/v1/posts", {"body": "Smoke feed post"}, token=author_token)
    step("publish a post for the feed -> 201", status == 201, post)
    step("my post is in my home feed", post["id"] in feed_objects("/api/v1/feed", author_token))
    step("a stranger's post is not in my home feed", post["id"] not in feed_objects("/api/v1/feed", reader_token))
    step("guests find the post in explore", post["id"] in feed_objects("/api/v1/feed/explore?type=post"))
    step("the diary filter excludes posts", post["id"] not in feed_objects("/api/v1/feed/explore?type=diary"))
    status, body = call("GET", "/api/v1/feed/explore?type=comment")
    step("unknown feed type -> 422", status == 422, body)
    call("PUT", f"/api/v1/users/{author}/follow", token=reader_token)
    step("following adds the post", post["id"] in feed_objects("/api/v1/feed", reader_token))
    call("DELETE", f"/api/v1/users/{author}/follow", token=reader_token)
    step("unfollowing removes the post", post["id"] not in feed_objects("/api/v1/feed", reader_token))
    status, body = call("DELETE", f"/api/v1/posts/{post['id']}", token=author_token)
    step("delete the feed post -> 204", status == 204, body)
    step("the deleted post left explore", post["id"] not in feed_objects("/api/v1/feed/explore"))


def notification_types(token: str) -> list[str]:
    _, page = call("GET", "/api/v1/notifications", token=token)
    return sorted(item["type"] for item in page["items"])


def check_notifications(owner_token: str, actor_token: str) -> None:
    _, post = call("POST", "/api/v1/posts", {"body": "Smoke notifications"}, token=owner_token)
    path = f"/api/v1/posts/{post['id']}"

    def about_post() -> list[Any]:
        _, page = call("GET", "/api/v1/notifications", token=owner_token)
        found = [n for n in page["items"] if post["id"] in (n["target"]["id"], n["target"]["parent_id"])]
        return sorted(found, key=lambda n: str(n["type"]))

    for _ in range(2):
        call("PUT", f"{path}/like", token=actor_token)
    call("POST", f"{path}/comments", {"body": "Smoke comment"}, token=actor_token)
    call("PUT", f"{path}/like", token=owner_token)
    wait_for("LIKE and COMMENT notifications arrive", lambda: len(about_post()) == 2)
    found = about_post()
    step("one notification per action and none for my own", [n["type"] for n in found] == ["COMMENT", "LIKE"], found)
    newest = f"/api/v1/notifications/{found[0]['id']}/read"
    for _ in range(2):
        status, body = call("POST", newest, token=owner_token)
        step("mark one as read -> 204 (idempotent)", status == 204, body)
    step("only that one is read", [n["read"] for n in about_post()] == [True, False])
    status, body = call("POST", newest, token=actor_token)
    step("another user's notification -> 404", status == 404, body)
    status, body = call("POST", "/api/v1/notifications/read-all")
    step("guest read-all -> 401", status == 401, body)
    status, body = call("POST", "/api/v1/notifications/read-all", token=owner_token)
    step("mark all as read -> 204", status == 204, body)
    step("both are read", [n["read"] for n in about_post()] == [True, True])
    admin = login(ADMIN_USERNAME, ADMIN_PASSWORD)
    moderation = f"/api/v1/admin/moderation/posts/{post['id']}"
    call("POST", f"{moderation}/hide", {"reason": "smoke"}, token=admin)
    wait_for("hiding the post hides its notifications", lambda: about_post() == [])
    call("POST", f"{moderation}/restore", token=admin)
    wait_for("restoring the post shows them again", lambda: len(about_post()) == 2)
    call("DELETE", path, token=owner_token)
    wait_for("deleting the post removes its notifications", lambda: about_post() == [])


def main() -> int:
    suffix = secrets.token_hex(3)
    alice, bob = f"alice_{suffix}", f"bob_{suffix}"

    status, _ = call("GET", "/healthz")
    step("gateway is up", status == 200, status)

    for path in ("/api/v1/auth/docs", "/api/v1/docs", "/api/v1/notifications/docs"):
        status, html = fetch_bytes(path)
        step(f"{path} serves Swagger UI", status == 200 and b"SwaggerUIBundle" in html, status)

    album_id = check_catalog()

    for user in (alice, bob):
        status, body = call(
            "POST",
            "/api/v1/auth/register",
            {"username": user, "email": f"{user}@example.com", "display_name": user.title(), "password": PASSWORD},
        )
        step(f"register {user} -> 201", status == 201, body)

    status, body = call("POST", "/api/v1/auth/login", {"identifier": f"{alice}@example.com", "password": PASSWORD})
    step("login alice by email -> 200 + access token", status == 200 and "access_token" in body, body)
    token = body["access_token"]

    status, body = call("GET", "/api/v1/account", token=token)
    step("GET /account shows my private email", status == 200 and body["email"] == f"{alice}@example.com", body)

    status, body = call("PATCH", "/api/v1/account", {"display_name": "Alice A.", "bio": "Shoegaze"}, token=token)
    step("PATCH /account -> 200", status == 200 and body["display_name"] == "Alice A.", body)

    status, body = call("PATCH", "/api/v1/account", {"role": "ADMIN"}, token=token)
    step("PATCH /account with role -> 422 unknown_field", status == 422 and body["code"] == "unknown_field", body)

    # --- Avatars (US-006): upload -> fetch -> replace -> remove --------------
    status, body = upload_avatar(png(4096), token)
    step("upload a PNG avatar -> 200 + url", status == 200 and body["avatar_url"].startswith("/media/avatars/"), body)
    first_avatar = body["avatar_url"]

    status, raw = fetch_bytes(first_avatar)
    step("the gateway serves the uploaded avatar", status == 200 and raw == png(4096), status)

    status, body = call("GET", "/api/v1/account", token=token)
    step("GET /account returns the avatar url", body["avatar_url"] == first_avatar, body)

    status, body = upload_avatar(png(MAX_AVATAR_BYTES), token)
    step("a 2 MB avatar passes the gateway and is accepted", status == 200, (status, body))
    max_size_avatar = body["avatar_url"]
    status, _ = fetch_bytes(first_avatar)
    step("the replaced avatar file is gone", status == 404, status)

    status, body = upload_avatar(png(MAX_AVATAR_BYTES + 1), token)
    step("an oversized avatar reaches the app and is refused", status == 413 and body["code"] == "file_too_large", (status, body))

    status, body = upload_avatar(b"MZ\x90\x00\x03\x00\x00\x00" * 8, token, filename="avatar.png")
    step("a renamed executable -> 422 unsupported_media_type", status == 422 and body["code"] == "unsupported_media_type", body)

    status, body = call("GET", "/api/v1/account", token=token)
    step("rejected uploads leave the avatar intact", body["avatar_url"] == max_size_avatar, body)

    wait_for(
        "the avatar reaches the Core profile (event)",
        lambda: call("GET", f"/api/v1/users/{alice}")[1].get("avatar_url") == max_size_avatar,
    )

    status, body = call("DELETE", "/api/v1/account/avatar", token=token)
    step("DELETE /account/avatar -> 204", status == 204, body)
    status, body = call("DELETE", "/api/v1/account/avatar", token=token)
    step("deleting again is still 204", status == 204, body)
    status, body = call("GET", "/api/v1/account", token=token)
    step("the account shows no avatar", body["avatar_url"] is None, body)
    status, _ = fetch_bytes(max_size_avatar)
    step("the removed avatar file is gone", status == 404, status)
    status, body = upload_avatar(png(4096))
    step("guest avatar upload -> 401", status == 401, body)

    wait_for("bob's projection reaches Core (event)", lambda: call("GET", f"/api/v1/users/{bob}")[0] == 200)

    check_music_logging(album_id, alice, token)

    reader_token = login(f"{bob}@example.com", PASSWORD)
    check_social(album_id, alice, token, bob, reader_token)
    check_feed(alice, token, reader_token)
    check_notifications(token, reader_token)
    login(f"{alice}@example.com", PASSWORD)

    status, body = call("PUT", f"/api/v1/users/{bob}/follow", token=token)
    step("alice follows bob -> 204", status == 204, body)
    status, body = call("PUT", f"/api/v1/users/{bob}/follow", token=token)
    step("follow again is idempotent -> 204", status == 204, body)
    status, body = call("PUT", f"/api/v1/users/{alice}/follow", token=token)
    step("self-follow -> 422 self_follow", status == 422 and body["code"] == "self_follow", body)
    status, body = call("PUT", f"/api/v1/users/{bob}/follow")
    step("guest follow -> 401", status == 401, body)

    status, body = call("GET", f"/api/v1/users/{bob}", token=token)
    step(
        "bob's profile: 1 follower, viewer follows",
        status == 200 and body["counts"]["followers"] == 1 and body["viewer"]["follows"] is True,
        body,
    )
    status, body = call("GET", f"/api/v1/users/{bob}/followers")
    step("bob's followers list contains alice", [i["user"]["username"] for i in body["items"]] == [alice], body)
    status, body = call("GET", f"/api/v1/users/{alice}/following")
    step("alice's following list contains bob", [i["user"]["username"] for i in body["items"]] == [bob], body)

    wait_for(
        "profile update reaches Core (event)",
        lambda: call("GET", f"/api/v1/users/{alice}")[1].get("display_name") == "Alice A.",
    )

    status, body = call("DELETE", f"/api/v1/users/{bob}/follow", token=token)
    step("unfollow -> 204", status == 204, body)
    status, body = call("GET", f"/api/v1/users/{bob}")
    step("bob has 0 followers again", body["counts"]["followers"] == 0, body)

    status, body = call("PUT", f"/api/v1/users/{bob}/follow", token=token)
    step("refollow after unfollow -> 204", status == 204, body)

    status, body = call("POST", "/api/v1/auth/refresh", headers={"X-CSRF": "1"})
    step("refresh with cookie -> 200 + new access token", status == 200 and "access_token" in body, body)
    new_token = body["access_token"]

    status, body = call("POST", "/api/v1/auth/logout", token=new_token, headers={"X-CSRF": "1"})
    step("logout -> 204", status == 204, body)
    status, body = call("GET", "/api/v1/account", token=new_token)
    step("access token rejected after logout -> 401", status == 401, body)

    status, body = call("GET", "/api/v1/notifications")
    step("guest notifications -> 401", status == 401, body)

    status, body = call("POST", "/api/v1/auth/login", {"identifier": f"{bob}@example.com", "password": PASSWORD})
    step("login bob -> 200", status == 200 and "access_token" in body, body)
    bob_token = body["access_token"]

    def follow_notifications() -> list[Any]:
        _, page = call("GET", "/api/v1/notifications", token=bob_token)
        items = page.get("items", []) if isinstance(page, dict) else []
        return [n for n in items if n["type"] == "FOLLOW" and n["actor"]["username"] == alice]

    found = wait_for("bob gets alice's FOLLOW notification (p95 <= 2 s, NFR-082)", follow_notifications)
    step("exactly one FOLLOW notification after follow+refollow (DR-42)", len(found) == 1, found)
    step("notification targets the actor", found[0]["target"]["type"] == "user", found[0])
    step("notification is unread", found[0]["read"] is False, found[0])

    status, body = call("GET", "/api/v1/notifications/unread-count", token=bob_token)
    step("bob's unread count is 1", status == 200 and body["count"] == 1, body)

    status, body = call("POST", "/api/v1/auth/login", {"identifier": f"{alice}@example.com", "password": PASSWORD})
    step("login alice again -> 200", status == 200, body)
    alice_token = body["access_token"]
    status, body = call("GET", "/api/v1/notifications", token=alice_token)
    foreign = [n for n in body["items"] if n["actor"]["username"] != bob]
    step("alice sees only her own notifications", status == 200 and foreign == [], body)

    # --- US-008: password change ------------------------------------------------
    new_password = "Better456"
    status, body = call(
        "POST", "/api/v1/account/password",
        {"current_password": "Wrong1234", "new_password": new_password}, token=alice_token,
    )
    step("password change with a wrong current password -> 403", status == 403 and body["code"] == "invalid_current_password", body)
    status, body = call(
        "POST", "/api/v1/account/password",
        {"current_password": PASSWORD, "new_password": new_password}, token=alice_token,
    )
    step("password change -> 204", status == 204, body)
    status, body = call("POST", "/api/v1/auth/login", {"identifier": alice, "password": PASSWORD})
    step("old password no longer logs in -> 401", status == 401, body)
    status, body = call("POST", "/api/v1/auth/login", {"identifier": alice, "password": new_password})
    step("new password logs in -> 200", status == 200, body)
    alice_token = body["access_token"]

    # --- US-009: account deletion, propagated to Core and Notification ----------
    status, body = call("DELETE", "/api/v1/account", {"password": "Wrong1234"}, token=alice_token)
    step("delete with a wrong password -> 403", status == 403, body)
    status, body = call("DELETE", "/api/v1/account", {"password": new_password}, token=alice_token)
    step("delete account -> 204", status == 204, body)
    status, body = call("GET", "/api/v1/account", token=alice_token)
    step("Identity rejects the deleted user's token -> 401", status == 401, body)
    status, body = call("GET", f"/api/v1/users/{bob}/followers", token=alice_token)
    step("Core rejects the deleted user's token via Redis -> 401", status == 401, body)
    status, body = call("GET", "/api/v1/notifications", token=alice_token)
    step("Notification rejects the deleted user's token via Redis -> 401", status == 401, body)
    status, body = call("POST", "/api/v1/auth/refresh", headers={"X-CSRF": "1"})
    step("refresh after deletion -> 401", status == 401, body)
    wait_for(
        "deletion event tombstones the Core projection",
        lambda: call("GET", f"/api/v1/users/{alice}")[0] == 404,
    )

    wait_for("deletion removes the notifications alice sent", lambda: notification_types(bob_token) == [])
    _, body = call("GET", "/api/v1/notifications/unread-count", token=bob_token)
    step("bob's unread count is 0 again", body["count"] == 0, body)
    _, body = call("GET", "/api/v1/feed/explore")
    step("alice's activity left the explore feed", alice not in [i["actor"]["username"] for i in body["items"]], body)

    sys.stdout.write("\nAll smoke checks passed.\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeError as exc:
        sys.stderr.write(f"\nSmoke test failed: {exc}\n")
        sys.exit(1)
    except urllib.error.URLError as exc:
        sys.stderr.write(f"\nCannot reach {BASE}: {exc}. Is `docker compose up` running?\n")
        sys.exit(2)
