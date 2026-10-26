from __future__ import annotations

import ast
import sys
from pathlib import Path

SERVICE_PACKAGES: dict[str, str] = {
    "identity": "identity",
    "core": "innotter_core",
    "notification": "notifier",
}
SHARED_LIBRARY_DIR = Path("packages/innotter-common")


def imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def main(root: Path) -> int:
    all_packages = set(SERVICE_PACKAGES.values())
    violations: list[str] = []

    scopes = [(root / "services" / d, {pkg}) for d, pkg in SERVICE_PACKAGES.items()]
    scopes.append((root / SHARED_LIBRARY_DIR, set()))

    for base, allowed in scopes:
        forbidden = all_packages - allowed
        for file in sorted(base.rglob("*.py")):
            if any(part in {".venv", "node_modules", "migrations"} for part in file.parts):
                continue
            for name in sorted(imported_roots(file) & forbidden):
                violations.append(f"{file.relative_to(root)}: imports '{name}'")

    if violations:
        sys.stderr.write("Cross-service imports found (only innotter_common may be shared):\n")
        sys.stderr.write("\n".join(f"  {v}" for v in violations) + "\n")
        return 1
    sys.stdout.write("service isolation: OK\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()))
