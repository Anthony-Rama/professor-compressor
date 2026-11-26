import asyncio
import signal
import time
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import discord
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from professor_compressor.application import Runtime
from professor_compressor.background_tasks import BackgroundTasks
from professor_compressor.config import Settings
from professor_compressor.domain import JobState, UploadJob


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = Runtime(Settings(discord_token=""))

    async def test_two_apps_isolate_uploads_delivery_and_metrics(self):
        settings = Settings(discord_token="")
        sender_a = AsyncMock(return_value="Test sender A delivered")
        sender_b = AsyncMock(return_value="Test sender B delivered")
        a = Runtime(settings, deliver_results=sender_a)
        b = Runtime(settings, deliver_results=sender_b)
        a.relay.queue = asyncio.Queue(settings.delivery_queue_size)
        b.relay.queue = asyncio.Queue(settings.delivery_queue_size)
        self.assertIsNot(a.client, b.client)
        self.assertIsNot(a.event_store, b.event_store)
        self.assertIs(a.tree.get_command("compress").binding, a)
        self.assertIs(b.tree.get_command("compress").binding, b)

        job = UploadJob(
            "runtime-isolation",
            1,
            2,
            time.time() + 60,
            10_000,
            Mock(),
            state=JobState.CLAIMED,
            claim_secret="synthetic-claim",
        )
        a.jobs[job.token] = job
        path = f"/upload/{job.token}"
        headers = {"X-Upload-Session": "synthetic-claim"}

        def box(kind, data):
            return (len(data) + 8).to_bytes(4, "big") + kind + data

        clip = (
            box(b"ftyp", b"isom" + bytes(4) + b"mp41")
            + box(b"moov", b"meta")
            + box(b"mdat", b"video")
        )
        data = FormData()
        data.add_field("clips", clip, filename="clip.mp4", content_type="video/mp4")
        async with (
            TestClient(TestServer(a.create_web_application())) as http_a,
            TestClient(TestServer(b.create_web_application())) as http_b,
        ):
            self.assertEqual(
                (await http_b.get(path + "/status", headers=headers)).status, 410
            )
            async with asyncio.TaskGroup() as tasks:
                workers = [
                    tasks.create_task(runtime.delivery_worker(1)) for runtime in (a, b)
                ]
                try:
                    response = await http_a.post(path, headers=headers, data=data)
                    self.assertEqual(response.status, 200)
                    self.assertTrue((await response.json())["ok"])
                finally:
                    for worker in workers:
                        worker.cancel()
            self.assertEqual(
                (await http_a.get(path + "/status", headers=headers)).status, 200
            )
            self.assertEqual(
                (await http_b.get(path + "/status", headers=headers)).status, 410
            )
            for http, count in ((http_a, 1), (http_b, 0)):
                metrics = await (await http.get("/metricsz")).json()
                self.assertEqual(metrics["batches_queued"], count)
                self.assertEqual(metrics["deliveries_succeeded"], count)
        sender_a.assert_awaited_once()
        self.assertIs(sender_a.await_args.args[0], job)
        sender_b.assert_not_awaited()
        self.assertEqual(a.relay.buffered_bytes, 0)
        self.assertEqual(b.relay.buffered_bytes, 0)
        self.assertEqual(b.sessions.outcomes, {})

    async def test_transient_command_sync_retries(self):
        response = Mock(status=503, reason="Unavailable")
        error = discord.HTTPException(response, {"code": 0, "message": "Temporary"})
        with (
            patch.object(self.runtime, "commands_synced", False),
            patch.object(
                self.runtime.tree, "sync", AsyncMock(side_effect=[error, []])
            ) as sync,
            patch.object(asyncio, "sleep", AsyncMock()),
        ):
            await self.runtime.sync_commands_with_retry()
            self.assertEqual(sync.await_count, 2)
            self.assertTrue(self.runtime.commands_synced)

    async def test_startup_and_signal_shutdown_close_http_and_workers(self):
        signals = {}
        gateway_closed = asyncio.Event()
        test = self

        class Client:
            closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                await self.close()

            def is_closed(self):
                return self.closed

            async def start(self, token):
                test.assertEqual(token, "test-only")
                test.assertIsNotNone(test.runtime.web_runner)
                test.assertEqual(
                    len(test.runtime.delivery_workers),
                    test.runtime.settings.delivery_workers,
                )
                signals[signal.SIGTERM]()
                await gateway_closed.wait()

            async def close(self):
                self.closed = True
                gateway_closed.set()

        loop = asyncio.get_running_loop()
        with (
            patch.object(self.runtime, "client", Client()),
            patch.object(
                self.runtime, "settings", replace(self.runtime.settings, web_port=0)
            ),
            patch.object(self.runtime, "web_runner", None),
            patch.object(self.runtime.sessions, "jobs", {}),
            patch.object(self.runtime, "background_tasks", BackgroundTasks()),
            patch.object(self.runtime, "delivery_workers", []),
            patch.object(self.runtime.sessions, "draining", False),
            patch.object(
                loop,
                "add_signal_handler",
                side_effect=lambda sig, fn: signals.update({sig: fn}),
            ),
            patch.object(loop, "remove_signal_handler"),
        ):
            await asyncio.wait_for(self.runtime.serve("test-only"), 3)
            self.assertIsNone(self.runtime.web_runner)
            self.assertTrue(self.runtime.client.is_closed())
            self.assertTrue(
                all(worker.done() for worker in self.runtime.delivery_workers)
            )
