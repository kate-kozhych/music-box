from __future__ import annotations

import os
import subprocess
import sys


def import_base_settings(**overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", "import musicbox_core.config.settings.base"],
        env={**os.environ, **overrides},
        capture_output=True,
        text=True,
        check=False,
    )


def test_live_mode_refuses_to_start_without_a_contact() -> None:
    result = import_base_settings(CORE_CATALOG_MODE="live", CORE_MB_USER_AGENT_CONTACT=" ")

    assert result.returncode != 0
    assert "CORE_MB_USER_AGENT_CONTACT" in result.stderr


def test_live_mode_starts_with_a_contact() -> None:
    result = import_base_settings(
        CORE_CATALOG_MODE="live", CORE_MB_USER_AGENT_CONTACT="test@example.com"
    )

    assert result.returncode == 0, result.stderr
