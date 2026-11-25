from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
GENERATOR = ROOT / "tools" / "generate_dev_fixtures.py"
CATALOG = ROOT / "services" / "core" / "fixtures" / "catalog"


def generate(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(GENERATOR), *args], capture_output=True, text=True, check=False
    )


def test_the_recorded_catalog_directory_is_never_overwritten() -> None:
    before = {path.name: path.read_bytes() for path in CATALOG.iterdir()}

    assert generate("--out", str(CATALOG)).returncode != 0
    assert {path.name: path.read_bytes() for path in CATALOG.iterdir()} == before


def test_an_output_directory_must_be_chosen() -> None:
    assert generate().returncode != 0


@pytest.mark.parametrize("manifest", ['{"source": "MusicBrainz"}', "broken", None])
def test_a_copy_of_other_fixtures_is_never_overwritten(
    tmp_path: Path, manifest: str | None
) -> None:
    (tmp_path / "release-groups.json").write_text("[]", encoding="utf-8")
    if manifest is not None:
        (tmp_path / "manifest.json").write_text(manifest, encoding="utf-8")

    assert generate("--out", str(tmp_path)).returncode != 0
    assert (tmp_path / "release-groups.json").read_text(encoding="utf-8") == "[]"


def test_generated_fixtures_are_labelled_synthetic_and_can_be_regenerated(tmp_path: Path) -> None:
    assert generate("--out", str(tmp_path)).returncode == 0
    first = (tmp_path / "release-groups.json").read_bytes()

    assert generate("--out", str(tmp_path)).returncode == 0
    assert (tmp_path / "release-groups.json").read_bytes() == first
    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"] == "synthetic-development"
