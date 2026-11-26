"""Runtime fixtures: one fresh application per test, optionally served over HTTP."""

import asyncio
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock

# Settings must come from each test, never from a developer's real .env file.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ.setdefault("DISCORD_TOKEN", "test-token")

from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from professor_compressor.app.runtime import Runtime
from professor_compressor.config import Settings
from professor_compressor.domain import JobState, UploadJob
from tests.support.media import mp4


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    """A Runtime built from make_settings(), without a Discord token."""

    def make_settings(self) -> Settings:
        return Settings(discord_token="")

    def setUp(self) -> None:
        self.runtime = Runtime(self.make_settings())


class WebAppTestCase(RuntimeTestCase):
    """The Runtime's aiohttp application, served to self.client."""

    async def asyncSetUp(self) -> None:
        self.client = TestClient(TestServer(self.runtime.create_web_application()))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)


class RelayTestCase(WebAppTestCase):
    """A served Runtime with a delivery queue and one claimed upload link."""

    async def asyncSetUp(self) -> None:
        self.runtime.relay.queue = asyncio.Queue(8)
        await super().asyncSetUp()

    def job(self, state=JobState.CLAIMED):
        job = UploadJob(
            "audit",
            1,
            2,
            time.time() + 600,
            20_000_000,
            Mock(),
            state=state,
            claim_secret="private",
            claimed_at=time.time(),
            guild_id=3,
        )
        self.runtime.sessions.jobs[job.token] = job
        return job

    async def post_video(self):
        data = FormData()
        data.add_field("clips", mp4(), filename="clip.mp4", content_type="video/mp4")
        return await self.client.post(
            "/upload/audit", data=data, headers={"X-Upload-Session": "private"}
        )


class DiagnosticsTestCase(WebAppTestCase):
    """A served Runtime with diagnostic storage, an operator key, and a test job."""

    def make_settings(self) -> Settings:
        return Settings(
            discord_token="",
            observability_db_path=str(Path(self.directory.name) / "events.sqlite3"),
            observability_admin_key=self.key,
            diagnostic_test_guild_ids=frozenset({999}),
        )

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.key = "operator-test-key-" * 3
        super().setUp()
        self.store = self.runtime.event_store

    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.job = UploadJob(
            "diagnostic-test",
            123,
            456,
            time.time() + 600,
            20 * 1024 * 1024,
            Mock(),
            state=JobState.CLAIMED,
            claim_secret="private-page-secret",
            guild_id=999,
        )
        self.runtime.sessions.jobs[self.job.token] = self.job
        self.headers = {"X-Upload-Session": "private-page-secret"}
        self.url = "/upload/diagnostic-test/events"

    async def asyncTearDown(self) -> None:
        self.runtime.sessions.jobs.clear()
        self.runtime.sessions.outcomes.clear()
        self.runtime.request_times.clear()
        self.runtime.sessions.diagnostic_event_counts.clear()

    def payload(self):
        return {
            "run_id": uuid.uuid4().hex,
            "events": [
                {
                    "event_id": uuid.uuid4().hex,
                    "event": "client_failed",
                    "file_index": 1,
                    "data": {
                        "error_code": "METADATA_INVALID",
                        "filename": "private.mp4",
                        "caption": "private-caption",
                    },
                }
            ],
        }
