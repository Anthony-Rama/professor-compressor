import asyncio
import time

from professor_compressor.domain import JobState
from tests.support.runtime import RelayTestCase


class LifecycleTests(RelayTestCase):
    async def test_drain_waits_for_active_work_and_respects_deadline(self):
        job = self.job(JobState.QUEUED)
        task = asyncio.create_task(self.runtime.lifecycle.drain_sessions(timeout=1))
        await asyncio.sleep(0.01)
        self.assertTrue(self.runtime.sessions.draining)
        self.assertFalse(task.done())
        self.runtime.sessions.jobs.clear()
        await asyncio.wait_for(task, 1)
        self.runtime.sessions.jobs[job.token] = job
        started = time.monotonic()
        await self.runtime.lifecycle.drain_sessions(timeout=0.01)
        self.assertLess(time.monotonic() - started, 0.5)
