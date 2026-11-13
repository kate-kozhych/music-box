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
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, raw.decode(errors="replace")


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

    # DR-42: refollowing must not produce a second FOLLOW notification.
    status, body = call("PUT", f"/api/v1/users/{bob}/follow", token=token)
    step("refollow after unfollow -> 204", status == 204, body)

    status, body = call("POST", "/api/v1/auth/refresh", headers={"X-CSRF": "1"})
    step("refresh with cookie -> 200 + new access token", status == 200 and "access_token" in body, body)
    new_token = body["access_token"]

    status, body = call("POST", "/api/v1/auth/logout", token=new_token, headers={"X-CSRF": "1"})
    step("logout -> 204", status == 204, body)
    status, body = call("GET", "/api/v1/account", token=new_token)
    step("access token rejected after logout -> 401", status == 401, body)

    # --- The vertical slice: follow -> outbox -> RabbitMQ -> notification -----
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
    status, body = call("GET", "/api/v1/notifications", token=body["access_token"])
    step("alice has no notifications (recipient isolation)", status == 200 and body["items"] == [], body)

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
