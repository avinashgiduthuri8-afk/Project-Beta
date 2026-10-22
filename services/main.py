"""Project Beta single-server service entry point.

Allows launching the unified single server via `python -m services.main`.
"""

from __future__ import annotations

import asyncio
import sys

from services.runtime import run_all


if __name__ == "__main__":
    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\nShutdown requested by user. Project Beta single server stopped.")
