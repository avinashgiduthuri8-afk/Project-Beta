"""Project Beta Single Server process entry point.

Runs Scanner, Paper Trading, and Learner services concurrently in a single process
with unified Telegram C2 Bot notifications and interactive controls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent
for _p in [
    _ROOT,
    _ROOT / "services",
    _ROOT / "shared",
    _ROOT / "shared" / "core",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from services.runtime import run_all


if __name__ == "__main__":
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\nShutdown requested by user. Project Beta single server stopped.")
