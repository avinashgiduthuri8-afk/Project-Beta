"""Project Beta single-server service entry point.

Allows launching the unified single server via `python -m services.main`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent
for _p in [
    _ROOT,
    _ROOT / "services",
    _ROOT / "shared",
    _ROOT / "shared" / "core",
]:
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from services.runtime import run_all


if __name__ == "__main__":
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\nShutdown requested by user. Project Beta single server stopped.")
