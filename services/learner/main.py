"""Project Beta post-trade learner process entry point."""

import asyncio
from services.runtime import run_learner


if __name__ == "__main__":
    asyncio.run(run_learner())
