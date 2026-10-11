"""Project Beta Scanner process entry point."""

import asyncio
from services.runtime import run_scanner


if __name__ == "__main__":
    asyncio.run(run_scanner())
