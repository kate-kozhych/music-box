#!/usr/bin/env python3
"""Enforce the coverage gates of NFR-020 and NFR-021 on a coverage.json report.

`pytest --cov-fail-under` only gates a single blended percentage. NFR-020 states
two different numbers (line and branch), and NFR-021 adds a higher bar for the
authentication, authorization and feed modules, so the gate is checked here.

    python3 tools/check_coverage.py coverage.json --line 80 --branch 70 \
        --critical identity/sessions --critical-line 90
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _pct(covered: int, total: int) -> float:
    return 100.0 if total == 0 else 100.0 * covered / total


def _totals(files: dict[str, Any], prefixes: tuple[str, ...] = ()) -> tuple[float, float, int]:
    statements = covered = branches = partial = matched = 0
    for name, entry in files.items():
        if prefixes and not any(p in name for p in prefixes):
            continue
        matched += 1
        summary = entry["summary"]
        statements += summary["num_statements"]
        covered += summary["covered_lines"]
        branches += summary["num_branches"]
        partial += summary["covered_branches"]
    return _pct(covered, statements), _pct(partial, branches), matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="coverage.json produced by --cov-report=json")
    parser.add_argument("--line", type=float, default=80.0, help="minimum line %% (NFR-020)")
    parser.add_argument("--branch", type=float, default=70.0, help="minimum branch %% (NFR-020)")
    parser.add_argument(
        "--critical",
        action="append",
        default=[],
        help="path fragment of a critical module (NFR-021); repeatable",
    )
    parser.add_argument("--critical-line", type=float, default=90.0)
    parser.add_argument(
        "--critical-advisory",
        action="store_true",
        help="report critical modules below the bar without failing (NFR-021 is a Should)",
    )
    args = parser.parse_args(argv)

    if not args.report.exists():
        print(f"coverage report not found: {args.report}", file=sys.stderr)
        return 2

    files = json.loads(args.report.read_text())["files"]
    line, branch, _ = _totals(files)
    failures: list[str] = []

    print(f"overall: line {line:.1f}% (min {args.line}), branch {branch:.1f}% (min {args.branch})")
    if line < args.line:
        failures.append(f"line coverage {line:.1f}% < {args.line}% (NFR-020)")
    if branch < args.branch:
        failures.append(f"branch coverage {branch:.1f}% < {args.branch}% (NFR-020)")

    for fragment in args.critical:
        crit_line, _, matched = _totals(files, (fragment,))
        if matched == 0:
            failures.append(f"critical module {fragment!r} matched no measured file")
            continue
        status = "ok" if crit_line >= args.critical_line else "LOW"
        print(f"critical {fragment}: line {crit_line:.1f}% ({matched} files) {status}")
        if crit_line < args.critical_line and not args.critical_advisory:
            failures.append(
                f"{fragment}: line coverage {crit_line:.1f}% < {args.critical_line}% (NFR-021)"
            )

    for failure in failures:
        print(f"FAIL {failure}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
