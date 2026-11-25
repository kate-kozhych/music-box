#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "services" / "core"
FIXTURES = CORE / "fixtures" / "catalog"
COMMAND = "record_catalog_fixtures"


def main(argv: list[str]) -> int:
    use_docker = "--docker" in argv
    passthrough = [a for a in argv if a != "--docker"]

    if os.environ.get("CORE_CATALOG_MODE") != "live":
        sys.stderr.write(
            "Refusing to record: set CORE_CATALOG_MODE=live and "
            "CORE_MB_USER_AGENT_CONTACT first (NFR-061).\n"
        )
        return 2

    if use_docker:
        redis = subprocess.call(["docker", "compose", "up", "-d", "--wait", "redis"], cwd=ROOT)
        if redis != 0:
            return redis
        cmd = [
            "docker", "compose", "run", "--rm", "--no-deps", "--build",
            "-e", "CORE_CATALOG_MODE=live",
            "-v", f"{FIXTURES}:/app/services/core/fixtures/catalog",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "core-api", "python", "manage.py", COMMAND, *passthrough,
        ]  # fmt: skip
        return subprocess.call(cmd, cwd=ROOT)

    return subprocess.call(
        ["poetry", "run", "python", "manage.py", COMMAND, *passthrough], cwd=CORE
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
