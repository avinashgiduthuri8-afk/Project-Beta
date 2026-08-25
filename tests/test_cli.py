"""Integration test for Main Bot CLI Dry Run."""

import subprocess
import sys


def test_cli_dry_run():
    cmd = [sys.executable, "main.py", "--mode", "paper", "--broker", "paper", "--dry-run"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0
    assert "DRY-RUN MODE" in result.stderr or "DRY-RUN MODE" in result.stdout
    assert "Dry-run completed successfully" in result.stderr or "Dry-run completed successfully" in result.stdout
