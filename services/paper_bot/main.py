"""Project Beta paper-trading process entry point."""

import asyncio
from services.runtime import run_paper


if __name__ == "__main__":
    asyncio.run(run_paper())
