from __future__ import annotations

import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from typing import Any

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")
PASSWORD = "Secret123"

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


def main() -> int:
    suffix = secrets.token_hex(3)
    alice, bob = f"alice_{suffix}", f"bob_{suffix}"

    status, _ = call("GET", "/healthz")
    step("gateway is up", status == 200, status)

    for path in ("/api/v1/auth/docs", "/api/v1/docs", "/api/v1/notifications/docs"):
        status, html = fetch_bytes(path)
        step(f"{path} serves Swagger UI", status == 200 and b"SwaggerUIBundle" in html, status)

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
    step("alice has no notifications (recipient isolation)", status == 200 and body["items"] == [], body)

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

    def alice_tombstoned_in_notifications() -> bool:
        _, page = call("GET", "/api/v1/notifications", token=bob_token)
        items = page.get("items", []) if isinstance(page, dict) else []
        return any(n["type"] == "FOLLOW" and n["actor"]["display_name"] == "[deleted]" for n in items)

    wait_for("deletion event tombstones the Notification actor snapshot", alice_tombstoned_in_notifications)

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
