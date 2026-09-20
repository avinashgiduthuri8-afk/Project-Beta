"""V2 Background Task Scheduler."""

import asyncio
import logging
from typing import Callable, Coroutine, Dict, Any
from datetime import datetime, timezone

from v2.bus.event_bus import bus
from v2.bus.event_types import EventType

logger = logging.getLogger("v2.execution.scheduler")


class TaskScheduler:
    """Async background task scheduler for recurring jobs."""

    def __init__(self):
        self.tasks: Dict[str, asyncio.Task] = {}
        self._running = False

    async def start(self) -> None:
        """Starts all scheduled tasks."""
        self._running = True
        logger.info("TaskScheduler started.")

    async def stop(self) -> None:
        """Stops and cancels all tasks."""
        self._running = False
        for name, task in self.tasks.items():
            task.cancel()
        
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        logger.info("TaskScheduler stopped.")

    def schedule(self, name: str, interval_seconds: float, func: Callable[..., Coroutine[Any, Any, None]]) -> None:
        """Schedules a coroutine function to run periodically."""
        if not self._running:
            logger.warning(f"Cannot schedule {name}, scheduler is not running.")
            return

        async def _loop():
            logger.info(f"Scheduled task started: {name} (interval: {interval_seconds}s)")
            while self._running:
                try:
                    await bus.publish(EventType.JOB_STARTED, {"job": name})
                    await func()
                    await bus.publish(EventType.JOB_COMPLETED, {"job": name})
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Error in task {name}: {e}", exc_info=True)
                    await bus.publish(EventType.JOB_FAILED, {"job": name, "error": str(e)})
                
                await asyncio.sleep(interval_seconds)

        task = asyncio.create_task(_loop(), name=name)
        self.tasks[name] = task

