from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable

from knowledge.quality.repository import QualityRepository


logger = logging.getLogger(__name__)


class QualityEvalWorker:
    def __init__(
        self,
        *,
        repository: QualityRepository,
        evaluator: Any,
        poll_seconds: float = 2,
        stale_seconds: int = 300,
        scheduled: bool = True,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.repository = repository
        self.evaluator = evaluator
        self.poll_seconds = poll_seconds
        self.stale_seconds = stale_seconds
        self.scheduled = scheduled
        self.now = now
        self._task: asyncio.Task | None = None
        self._closing = False
        self._last_daily = ""
        self._last_weekly = ""

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.repository.recover_eval_runs(self.stale_seconds)
        self._closing = False
        self._task = asyncio.create_task(self._run(), name="quality-eval-worker")

    async def close(self) -> None:
        self._closing = True
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        while not self._closing:
            try:
                if self.scheduled:
                    await self._schedule_due_runs()
                run = await self.repository.claim_next_eval_run()
                if run is None:
                    await asyncio.sleep(self.poll_seconds)
                    continue
                await self.evaluator.run_existing(run.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "Quality eval worker iteration failed error_type=%s",
                    type(exc).__name__,
                )
                await asyncio.sleep(self.poll_seconds)

    async def _schedule_due_runs(self) -> None:
        current = self.now()
        day = current.date().isoformat()
        if current.hour == 2 and current.minute == 0 and self._last_daily != day:
            cases = await self.repository.list_eval_cases(enabled=True)
            # The scheduled suite is an explicit, reviewed set.  Do not rely on
            # database insertion order, otherwise reserve cases can silently
            # displace an official case.
            critical = [
                case.id
                for case in cases
                if case.priority == "critical"
                and case.approval_state == "approved"
                and "official-critical-v2" in case.tags
            ]
            if critical:
                await self.evaluator.queue_cases(critical)
            self._last_daily = day
        if (
            current.weekday() == 6
            and current.hour == 3
            and current.minute == 0
            and self._last_weekly != day
        ):
            cases = await self.repository.list_eval_cases(enabled=True)
            approved = [case.id for case in cases if case.approval_state == "approved"]
            if approved:
                await self.evaluator.queue_cases(approved)
            self._last_weekly = day
