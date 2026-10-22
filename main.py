"""Project Beta Single Server process entry point.

Runs Scanner, Paper Trading, and Learner services concurrently in a single process
with unified Telegram C2 Bot notifications and interactive controls.
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
