import asyncio
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from professor_compressor import application as app
from professor_compressor.assets import ASSET_PREFIX
from professor_compressor.delivery_policy import (
    bot_upload_limit,
    channel_delivery_problem,
)
from professor_compressor.domain import JobState, UploadJob


def mp4():
    def box(kind, data):
        return (len(data) + 8).to_bytes(4, "big") + kind + data

    return (
        box(b"ftyp", b"isom" + bytes(4) + b"mp41")
        + box(b"moov", b"meta")
        + box(b"mdat", b"video")
    )


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for key, value in {
            "jobs": {},
            "outcomes": {},
            "request_times": {},
            "delivery_queue": asyncio.Queue(8),
            "delivery_bytes_held": 0,
            "active_relay_uploads": 0,
            "draining": False,
        }.items():
            patcher = patch.object(app, key, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.http = TestClient(TestServer(app.create_web_application()))
        await self.http.start_server()
        self.addAsyncCleanup(self.http.close)

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
        app.jobs[job.token] = job
        return job

    async def post_video(self):
        data = FormData()
        data.add_field("clips", mp4(), filename="clip.mp4", content_type="video/mp4")
        return await self.http.post(
            "/upload/audit", data=data, headers={"X-Upload-Session": "private"}
        )

    async def test_head_does_not_claim_or_extend_link(self):
        job = self.job(JobState.OPEN)
        expiry = job.expires_at
        response = await self.http.head("/upload/audit")
        self.assertEqual(response.status, 200)
        self.assertIs(job.state, JobState.OPEN)
        self.assertEqual(job.expires_at, expiry)
        response = await self.http.get("/upload/audit")
        self.assertIn('id="submit"', await response.text())
        self.assertIs(job.state, JobState.CLAIMED)

    async def test_pending_request_survives_http_wait_and_expiry(self):
        job = self.job()
        gate = asyncio.Event()
        started = asyncio.Event()

        async def deliver(*args):
            started.set()
            await gate.wait()
            return "delivered once"

        with (
            patch.object(app, "DELIVERY_RESPONSE_WAIT_SECONDS", 0.01),
            patch.object(
                app, "deliver_browser_results", AsyncMock(side_effect=deliver)
            ) as sender,
            patch.object(app, "schedule_owner_alert"),
        ):
            worker = asyncio.create_task(app.delivery_worker(1))
            try:
                response = await self.post_video()
                self.assertEqual(response.status, 202)
                await started.wait()
                job.expires_at = time.time() - 1
                app.discard_expired_jobs()
                self.assertIn(job.token, app.jobs)
                second = await self.post_video()
                self.assertEqual(second.status, 202)
                gate.set()
                await asyncio.wait_for(app.delivery_queue.join(), 2)
                status = await self.http.get(
                    "/upload/audit/status", headers={"X-Upload-Session": "private"}
                )
                self.assertEqual(status.status, 200)
                self.assertTrue((await status.json())["ok"])
                duplicate = await self.post_video()
                self.assertEqual(duplicate.status, 200)
                sender.assert_awaited_once()
                self.assertEqual(app.delivery_bytes_held, 0)
                self.assertEqual(app.active_relay_uploads, 0)
                self.assertNotIn(job.token, app.jobs)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_failed_outcome_is_authenticated_and_not_requeued(self):
        self.job()
        with (
            patch.object(
                app,
                "deliver_browser_results",
                AsyncMock(side_effect=RuntimeError("Permission changed")),
            ) as sender,
            patch.object(app, "schedule_owner_alert"),
        ):
            worker = asyncio.create_task(app.delivery_worker(1))
            try:
                response = await self.post_video()
                self.assertEqual(response.status, 502)
                repeated = await self.post_video()
                self.assertEqual(repeated.status, 502)
                sender.assert_awaited_once()
                private = await self.http.get("/upload/audit/status")
                self.assertEqual(private.status, 403)
                self.assertNotIn("Permission changed", await private.text())
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_receipts_expire_and_never_retain_media(self):
        job = self.job()
        app.record_outcome(job, 200, {"ok": True, "message": "sent", "count": 1})
        app.jobs.clear()
        receipt = app.outcomes[job.token]
        self.assertFalse(hasattr(receipt, "results"))
        self.assertNotIn("private", repr(receipt))
        with patch.object(app.time, "time", return_value=receipt.expires_at + 1):
            app.discard_expired_jobs()
            self.assertFalse(app.outcomes)

    async def test_heartbeat_requires_secret_and_cannot_revive_expired_job(self):
        job = self.job()
        forbidden = await self.http.post("/upload/audit/heartbeat")
        self.assertEqual(forbidden.status, 403)
        response = await self.http.post(
            "/upload/audit/heartbeat", headers={"X-Upload-Session": "private"}
        )
        self.assertEqual(response.status, 200)
        self.assertGreater(job.processing_until, time.time())
        await self.http.post(
            "/upload/audit/pause", headers={"X-Upload-Session": "private"}
        )
        self.assertEqual(job.processing_until, 0)
        job.claimed_at = time.time() - 7190
        await self.http.post(
            "/upload/audit/heartbeat", headers={"X-Upload-Session": "private"}
        )
        self.assertLessEqual(job.expires_at, job.claimed_at + 7200)
        job.expires_at = time.time() - 1
        expired = await self.http.post(
            "/upload/audit/heartbeat", headers={"X-Upload-Session": "private"}
        )
        self.assertEqual(expired.status, 410)

    async def test_readiness_checks_discord_workers_sync_and_drain(self):
        healthy_workers = [
            Mock(done=Mock(return_value=False)) for _ in range(app.DELIVERY_WORKERS)
        ]
        for discord_ready, synced, workers, draining, expected in (
            (False, True, healthy_workers, False, 503),
            (True, False, healthy_workers, False, 503),
            (True, True, [], False, 503),
            (True, True, healthy_workers, True, 503),
            (True, True, healthy_workers, False, 200),
        ):
            with (
                patch.object(app.client, "is_ready", return_value=discord_ready),
                patch.object(app, "commands_synced", synced),
                patch.object(app, "delivery_workers", workers),
                patch.object(app, "draining", draining),
            ):
                response = await self.http.get("/readyz")
                self.assertEqual(response.status, expected)
                self.assertIn("noindex", response.headers["X-Robots-Tag"])
                live = await self.http.get("/healthz")
                self.assertEqual(live.status, 200)

    async def test_only_versioned_assets_are_immutable(self):
        for prefix, immutable in ((ASSET_PREFIX, True), ("/assets", False)):
            response = await self.http.get(
                prefix + "/core-mt-esm/ffmpeg-core.worker.js"
            )
            self.assertEqual(response.status, 200)
            self.assertEqual(
                "immutable" in response.headers.get("Cache-Control", ""), immutable
            )

    async def test_drain_waits_for_active_work_and_respects_deadline(self):
        job = self.job(JobState.QUEUED)
        task = asyncio.create_task(app.drain_sessions(timeout=1))
        await asyncio.sleep(0.01)
        self.assertTrue(app.draining)
        self.assertFalse(task.done())
        app.jobs.clear()
        await asyncio.wait_for(task, 1)
        app.jobs[job.token] = job
        started = time.monotonic()
        await app.drain_sessions(timeout=0.01)
        self.assertLess(time.monotonic() - started, 0.5)

    async def test_delivery_rechecks_permissions_and_current_limit(self):
        job = self.job()
        guild = Mock(filesize_limit=20 * 1024 * 1024)
        channel = Mock()
        channel.send = AsyncMock()
        channel.permissions_for.return_value = discord.Permissions(
            view_channel=True, send_messages=True, attach_files=False
        )
        with (
            patch.object(app, "get_channel", AsyncMock(return_value=channel)),
            patch.object(app.client, "get_guild", return_value=guild),
        ):
            with self.assertRaisesRegex(RuntimeError, "Attach Files"):
                await app.deliver_browser_results(
                    job, [app.BrowserResult("a.mp4", mp4())]
                )
            channel.send.assert_not_awaited()
            channel.permissions_for.return_value.attach_files = True
            with patch.object(app, "bot_upload_limit", return_value=1):
                with self.assertRaisesRegex(RuntimeError, "allowance changed"):
                    await app.deliver_browser_results(
                        job, [app.BrowserResult("a.mp4", mp4())]
                    )
            channel.send.assert_not_awaited()


class DeliveryPolicyTests(unittest.TestCase):
    def test_base_override_and_boosted_allowance(self):
        self.assertEqual(
            bot_upload_limit(Mock(filesize_limit=10 * 1024 * 1024)), 20 * 1024 * 1024
        )
        self.assertEqual(
            bot_upload_limit(Mock(filesize_limit=100 * 1024 * 1024)), 100 * 1024 * 1024
        )
        self.assertEqual(bot_upload_limit(Mock(filesize_limit=10), 123), 123)

    def test_threads_use_thread_permission_and_reject_archived_or_unjoined(self):
        thread = Mock(spec=discord.Thread)
        thread.archived = False
        thread.is_private.return_value = False
        permissions = discord.Permissions(
            view_channel=True, attach_files=True, send_messages=True
        )
        self.assertIn(
            "Send Messages in Threads", channel_delivery_problem(thread, permissions)
        )
        permissions.send_messages_in_threads = True
        self.assertIsNone(channel_delivery_problem(thread, permissions))
        thread.archived = True
        self.assertIn("Unarchive", channel_delivery_problem(thread, permissions))
        thread.archived = False
        thread.is_private.return_value = True
        thread.me = None
        self.assertIn("private thread", channel_delivery_problem(thread, permissions))
