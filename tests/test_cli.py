"""
Unit and integration tests for CLI argument parsing and dry-run mode.
"""

from __future__ import annotations

import subprocess
import sys


def test_cli_dry_run():
    result = subprocess.run(
        [sys.executable, "main.py", "--mode", "paper", "--broker", "paper", "--dry-run"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.returncode == 0
    assert "Dry-run completed successfully" in result.stdout or "Dry-run completed successfully" in result.stderr
