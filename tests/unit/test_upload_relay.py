import asyncio
import json
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import FormData

from professor_compressor.domain import DeliveryRequest, JobState, UploadJob
from professor_compressor.relay import upload as upload_relay
from professor_compressor.relay.upload import safe_result_name
from tests.support.media import valid_test_mp4
from tests.support.runtime import RelayTestCase, WebAppTestCase


class UploadRelayTests(RelayTestCase):
    async def test_stalled_metadata_obeys_deadline_and_releases_upload_slot(self):
        for name in ("caption", "diagnostics"):
            with self.subTest(field=name):
                job = self.job()
                cancelled = asyncio.Event()

                async def stall(*args, cancelled=cancelled):
                    try:
                        await asyncio.Event().wait()
                    finally:
                        cancelled.set()

                field = Mock()
                field.name = name
                field.read_chunk = AsyncMock(side_effect=stall)
                reader = Mock(next=AsyncMock(return_value=field))
                request = Mock(
                    content_type="multipart/form-data",
                    multipart=AsyncMock(return_value=reader),
                )
                # Start at t=0; metadata begins just before the total deadline.
                # Replace the app's time reference, not asyncio's real clock.
                ticks = iter([0, 0, 0, 299.99])
                clock = Mock(
                    monotonic=Mock(side_effect=lambda ticks=ticks: next(ticks, 300))
                )
                with (
                    patch.object(
                        self.runtime.sessions,
                        "session_access",
                        return_value=(job, None),
                    ),
                    patch.object(
                        self.runtime.rate_limiter, "retry_after", return_value=None
                    ),
                    patch.object(self.runtime.alerts, "schedule"),
                    patch.object(upload_relay, "time", clock),
                ):
                    response = await asyncio.wait_for(
                        self.runtime.session_routes.receive_results(request), 1
                    )
                self.assertEqual(response.status, 408)
                self.assertIn("upload_timeout", response.text)
                self.assertTrue(cancelled.is_set())
                self.assertEqual(self.runtime.relay.active_uploads, 0)
                self.assertEqual(self.runtime.relay.buffered_bytes, 0)
                self.assertTrue(self.runtime.relay.queue.empty())
                self.assertIs(job.state, JobState.CLAIMED)

    async def test_stalled_metadata_read_has_thirty_second_idle_timeout(self):
        job = self.job()
        field = Mock()
        field.name = "caption"
        field.read_chunk = AsyncMock(return_value=b"")
        reader = Mock(next=AsyncMock(return_value=field))
        request = Mock(
            content_type="multipart/form-data",
            multipart=AsyncMock(return_value=reader),
        )
        actual_wait_for = asyncio.wait_for
        timeouts = []

        async def wait_for(awaitable, timeout):
            timeouts.append(timeout)
            if len(timeouts) == 2:
                awaitable.close()
                raise TimeoutError
            return await actual_wait_for(awaitable, timeout)

        with (
            patch.object(
                self.runtime.sessions, "session_access", return_value=(job, None)
            ),
            patch.object(self.runtime.rate_limiter, "retry_after", return_value=None),
            patch.object(self.runtime.alerts, "schedule"),
            patch.object(asyncio, "wait_for", side_effect=wait_for),
        ):
            response = await self.runtime.session_routes.receive_results(request)
        self.assertEqual(timeouts, [30, 30])
        self.assertEqual(response.status, 408)
        self.assertEqual(self.runtime.relay.active_uploads, 0)

    async def test_connection_reset_records_partial_receipt_and_releases_resources(
        self,
    ):
        job = self.job()
        field = Mock(name="field")
        field.name = "clips"
        field.filename = "private-name.mp4"
        field.read_chunk = AsyncMock(
            side_effect=[b"partial", ConnectionResetError("private transport detail")]
        )
        reader = Mock(next=AsyncMock(return_value=field))
        request = Mock(
            content_type="multipart/form-data", multipart=AsyncMock(return_value=reader)
        )
        request.headers = {}
        with (
            patch.object(
                self.runtime.sessions, "session_access", return_value=(job, None)
            ),
            patch.object(self.runtime.rate_limiter, "retry_after", return_value=None),
            patch.object(self.runtime.alerts, "schedule"),
            patch.object(self.runtime.tracker, "track") as tracked,
        ):
            response = await self.runtime.session_routes.receive_results(request)
        self.assertEqual(response.status, 503)
        self.assertIn("upload_connection_reset", response.text)
        self.assertNotIn("private", response.text)
        facts = next(
            call.kwargs["data"]
            for call in tracked.call_args_list
            if call.args[1] == "upload_failed"
        )
        self.assertEqual(facts["error_code"], "UPLOAD_CONNECTION_RESET")
        self.assertEqual(facts["received_bytes"], 7)
        self.assertEqual(facts["upload_attempts"], 1)
        self.assertGreaterEqual(facts["receive_idle_seconds"], 0)
        self.assertEqual(self.runtime.relay.active_uploads, 0)
        self.assertEqual(self.runtime.relay.buffered_bytes, 0)
        self.assertTrue(self.runtime.relay.queue.empty())
        self.assertIs(job.state, JobState.CLAIMED)

    async def test_upload_cancellation_releases_only_its_own_reservation(self):
        job = self.job()
        self.runtime.relay.buffered_bytes = 13
        reading = asyncio.Event()
        partial = b"partial"
        reads = 0

        async def read(_size):
            nonlocal reads
            reads += 1
            if reads == 1:
                return partial
            reading.set()
            await asyncio.Future()

        field = Mock(filename="clip.mp4", read_chunk=read)
        field.name = "clips"
        reader = Mock(next=AsyncMock(return_value=field))
        request = Mock(
            content_type="multipart/form-data", multipart=AsyncMock(return_value=reader)
        )
        request.headers = {}
        with (
            patch.object(
                self.runtime.sessions, "session_access", return_value=(job, None)
            ),
            patch.object(self.runtime.rate_limiter, "retry_after", return_value=None),
        ):
            upload = asyncio.create_task(
                self.runtime.session_routes.receive_results(request)
            )
            try:
                await asyncio.wait_for(reading.wait(), 2)
                self.assertEqual(self.runtime.relay.active_uploads, 1)
                self.assertEqual(self.runtime.relay.buffered_bytes, 13 + len(partial))
            finally:
                upload.cancel()
                await asyncio.gather(upload, return_exceptions=True)
        self.assertTrue(upload.cancelled())
        self.assertEqual(self.runtime.relay.active_uploads, 0)
        self.assertEqual(self.runtime.relay.buffered_bytes, 13)
        self.assertTrue(self.runtime.relay.queue.empty())
        self.assertIs(job.state, JobState.CLAIMED)


class UploadRouteTests(WebAppTestCase):
    async def test_valid_result_batch_reaches_the_delivery_queue(self) -> None:
        token = "valid-session"
        job = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
        )
        self.runtime.sessions.jobs[token] = job
        queue: asyncio.Queue[DeliveryRequest] = asyncio.Queue(maxsize=1)
        received = []

        async def accept_delivery() -> None:
            delivery = await queue.get()
            received.append(delivery)
            delivery.completed.set_result("Delivered in test")
            queue.task_done()

        form = FormData()
        form.add_field("caption", "x" * 1000)
        form.add_field(
            "diagnostics",
            json.dumps(
                [
                    {
                        "input_bytes": 1000,
                        "duration_seconds": 2.5,
                        "video_kbps": 1000,
                        "copied": False,
                        "input_format": "mov",
                    }
                ]
            ),
        )
        form.add_field(
            "clips",
            valid_test_mp4(),
            filename="../clip.mp4",
            content_type="video/mp4",
        )
        consumer = asyncio.create_task(accept_delivery())
        with patch.object(self.runtime.relay, "queue", queue):
            response = await self.client.post(
                f"/upload/{token}",
                data=form,
                headers={"X-Upload-Session": "claim-secret"},
            )
        await consumer

        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["message"], "Delivered in test")
        self.assertEqual(len(received[0].results), 1)
        self.assertEqual(received[0].caption, "x" * 1000)
        self.assertEqual(received[0].diagnostics[0].input_bytes, 1000)
        self.assertEqual(received[0].diagnostics[0].input_format, "mov")
        self.assertTrue(received[0].results[0].name.endswith(".mp4"))
        self.assertNotIn("/", received[0].results[0].name)
        self.assertIs(job.state, JobState.DONE)

    async def test_rejects_oversized_caption_before_delivery(self) -> None:
        token = "long-caption"
        job = UploadJob(
            token=token,
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
        )
        self.runtime.sessions.jobs[token] = job
        queue: asyncio.Queue[DeliveryRequest] = asyncio.Queue(maxsize=1)
        form = FormData()
        form.add_field("caption", "x" * 1001)
        form.add_field(
            "clips", valid_test_mp4(), filename="clip.mp4", content_type="video/mp4"
        )
        with patch.object(self.runtime.relay, "queue", queue):
            response = await self.client.post(
                f"/upload/{token}",
                data=form,
                headers={"X-Upload-Session": "claim-secret"},
            )
        self.assertEqual(response.status, 400)
        self.assertIn("1,000 characters", (await response.json())["error"])
        self.assertTrue(queue.empty())
        self.assertIs(job.state, JobState.CLAIMED)

    async def test_rejected_uploads_release_resources_and_allow_retry(self) -> None:
        job = UploadJob(
            token="limits",
            user_id=1,
            channel_id=2,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="secret",
        )
        self.runtime.sessions.jobs[job.token] = job
        for limit, data, expected in (
            (1, valid_test_mp4(), 503),
            (10000, b"invalid", 400),
        ):
            form = FormData()
            form.add_field("clips", data, filename="clip.mp4")
            with (
                patch.object(self.runtime.relay, "queue", asyncio.Queue(1)),
                patch.object(
                    self.runtime.session_routes,
                    "settings",
                    replace(self.runtime.settings, max_delivery_buffer_bytes=limit),
                ),
            ):
                response = await self.client.post(
                    "/upload/limits", data=form, headers={"X-Upload-Session": "secret"}
                )
            self.assertEqual(response.status, expected)
            self.assertEqual(self.runtime.relay.buffered_bytes, 0)
            self.assertEqual(self.runtime.relay.active_uploads, 0)
            self.assertIs(job.state, JobState.CLAIMED)


def failing_alert(failing_title: str):
    """Build progress alerts normally, except the one whose title matches."""

    def build(job, title, detail):
        if failing_title in title:
            raise RuntimeError("alert formatting failed")
        return f"{title}: {detail}"

    return build


class RelayAlertFailureTests(RelayTestCase):
    """Operator alerts are optional; a failure building one must not change
    what the uploader sees, release resources early, or allow a duplicate."""

    async def test_failed_upload_started_alert_does_not_hold_the_upload_slot(
        self,
    ) -> None:
        job = self.job()
        queue = self.runtime.relay.queue
        assert queue is not None

        async def deliver() -> None:
            delivery = await queue.get()
            delivery.completed.set_result("Delivered in test")
            queue.task_done()

        consumer = asyncio.create_task(deliver())
        with patch.object(
            upload_relay,
            "compression_progress_alert",
            failing_alert("Relay upload started"),
        ):
            response = await self.post_video()
        try:
            await asyncio.wait_for(asyncio.shield(consumer), 2)
        except TimeoutError:
            consumer.cancel()

        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["message"], "Delivered in test")
        self.assertEqual(self.runtime.relay.active_uploads, 0)
        self.assertIs(job.state, JobState.DONE)

    async def test_failed_queued_alert_keeps_one_queued_delivery(self) -> None:
        job = self.job()
        queue = self.runtime.relay.queue
        assert queue is not None
        with (
            patch.object(
                upload_relay,
                "compression_progress_alert",
                failing_alert("Finished files reached relay"),
            ),
            patch.object(upload_relay, "DELIVERY_RESPONSE_WAIT_SECONDS", 0.05),
        ):
            first = await self.post_video()
            retry = await self.post_video()

        # The batch belongs to the delivery queue; the browser is told to wait,
        # and a retry while it is queued cannot enqueue the clips a second time.
        self.assertEqual(first.status, 202)
        self.assertTrue((await first.json())["pending"])
        self.assertIs(job.state, JobState.QUEUED)
        self.assertEqual(retry.status, 202)
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(self.runtime.relay.active_uploads, 0)
        self.assertGreater(self.runtime.relay.buffered_bytes, 0)


class ResultNameTests(unittest.TestCase):
    def test_result_names_are_safe_and_bounded(self) -> None:
        result = safe_result_name("../" + "a" * 300 + ".mp4", 1)

        self.assertEqual(result, "a" * 120 + ".mp4")
        self.assertNotIn("/", result)
