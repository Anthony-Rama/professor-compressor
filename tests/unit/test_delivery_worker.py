import asyncio
import json
from unittest.mock import AsyncMock, Mock, patch

from professor_compressor.app.runtime import Runtime
from professor_compressor.bot.delivery_policy import (
    DeliveryFailure,
    delivery_failure,
)
from professor_compressor.config import Settings
from professor_compressor.domain import (
    BrowserResult,
    CompressionDiagnostic,
    UploadJob,
)
from tests.support.runtime import RuntimeTestCase


class DeliveryWorkerTests(RuntimeTestCase):
    async def test_worker_releases_memory_after_success_and_failure(self) -> None:
        from professor_compressor.domain import DeliveryRequest, JobState

        for failure in (
            None,
            RuntimeError("Discord unavailable"),
            TimeoutError(),
            DeliveryFailure(
                "Ask an admin to restore access.", "DELIVERY_ACCESS_DENIED"
            ),
        ):
            job = UploadJob(
                token="worker-test",
                user_id=1,
                channel_id=2,
                expires_at=9999999999,
                discord_limit=1000,
                interaction=Mock(),
            )
            queue: asyncio.Queue[DeliveryRequest] = asyncio.Queue(1)
            completed = asyncio.get_running_loop().create_future()
            queue.put_nowait(
                DeliveryRequest(job, [BrowserResult("clip.mp4", b"video")], completed)
            )
            with (
                patch.object(self.runtime.relay, "queue", queue),
                patch.object(self.runtime.relay, "buffered_bytes", 5),
                patch.object(self.runtime.alerts, "schedule"),
                patch.object(self.runtime.sessions, "outcomes", {}),
                patch.object(
                    self.runtime.delivery,
                    "deliver_browser_results",
                    AsyncMock(return_value="sent", side_effect=failure),
                ),
            ):
                worker = asyncio.create_task(self.runtime.delivery.run_worker(1))
                try:
                    await asyncio.wait_for(queue.join(), 2)
                    self.assertEqual(self.runtime.relay.buffered_bytes, 0)
                    self.assertIs(job.state, JobState.DONE)
                    if failure is not None:
                        expected = delivery_failure(failure)
                        self.assertEqual(str(completed.exception()), str(expected))
                        receipt = self.runtime.sessions.outcomes[job.token].body
                        self.assertEqual(receipt["delivery_outcome"], expected.outcome)
                        self.assertEqual(receipt["error_code"], expected.error_code)
                    else:
                        self.assertEqual(completed.result(), "sent")
                finally:
                    worker.cancel()
                    await asyncio.gather(worker, return_exceptions=True)

    async def test_alert_failures_never_change_the_delivery_result(self) -> None:
        # A failure building the operator alert must not turn a confirmed send
        # into a failure, and must not stop the worker taking the next batch.
        from professor_compressor.domain import DeliveryRequest, JobState
        from professor_compressor.relay import worker as worker_module

        for failure in (None, RuntimeError("Discord unavailable")):
            with self.subTest(send_fails=failure is not None):
                queue: asyncio.Queue[DeliveryRequest] = asyncio.Queue(2)
                deliveries = []
                for token in ("first", "second"):
                    job = UploadJob(
                        token=token,
                        user_id=1,
                        channel_id=2,
                        expires_at=9999999999,
                        discord_limit=1000,
                        interaction=Mock(),
                    )
                    completed = asyncio.get_running_loop().create_future()
                    deliveries.append((job, completed))
                    queue.put_nowait(
                        DeliveryRequest(
                            job, [BrowserResult("clip.mp4", b"video")], completed
                        )
                    )
                with (
                    patch.object(self.runtime.relay, "queue", queue),
                    patch.object(self.runtime.relay, "buffered_bytes", 10),
                    patch.object(self.runtime.sessions, "outcomes", {}),
                    patch.object(
                        worker_module,
                        "outcome_alert",
                        side_effect=RuntimeError("alert formatting failed"),
                    ),
                    patch.object(
                        self.runtime.delivery,
                        "deliver_browser_results",
                        AsyncMock(return_value="sent", side_effect=failure),
                    ),
                ):
                    worker = asyncio.create_task(self.runtime.delivery.run_worker(1))
                    try:
                        await asyncio.wait_for(queue.join(), 2)
                        self.assertFalse(worker.done())
                        for job, completed in deliveries:
                            receipt = self.runtime.sessions.outcomes[job.token]
                            self.assertIs(job.state, JobState.DONE)
                            if failure is None:
                                self.assertEqual(completed.result(), "sent")
                                self.assertEqual(receipt.status, 200)
                            else:
                                self.assertIsInstance(
                                    completed.exception(), RuntimeError
                                )
                                self.assertEqual(receipt.status, 502)
                        self.assertEqual(self.runtime.relay.buffered_bytes, 0)
                    finally:
                        worker.cancel()
                        await asyncio.gather(worker, return_exceptions=True)

    async def test_unexpected_bookkeeping_error_never_stops_the_worker(self) -> None:
        from professor_compressor.domain import DeliveryRequest, JobState

        queue: asyncio.Queue[DeliveryRequest] = asyncio.Queue(2)
        deliveries = []
        for token in ("first", "second"):
            job = UploadJob(token, 1, 2, 9999999999, 1000, Mock())
            completed = asyncio.get_running_loop().create_future()
            deliveries.append((job, completed))
            queue.put_nowait(
                DeliveryRequest(job, [BrowserResult("clip.mp4", b"video")], completed)
            )
        with (
            patch.object(self.runtime.relay, "queue", queue),
            patch.object(self.runtime.relay, "buffered_bytes", 10),
            patch.object(
                self.runtime.sessions,
                "record_outcome",
                Mock(side_effect=[RuntimeError("receipt store failed"), None]),
            ),
            patch.object(
                self.runtime.delivery,
                "deliver_browser_results",
                AsyncMock(return_value="sent"),
            ),
        ):
            worker = asyncio.create_task(self.runtime.delivery.run_worker(1))
            try:
                await asyncio.wait_for(queue.join(), 2)
                self.assertFalse(worker.done())
                (first_job, first), (second_job, second) = deliveries
                # The browser is told delivery could not be confirmed rather
                # than waiting forever; the next batch is delivered normally.
                self.assertIsInstance(first.exception(), RuntimeError)
                self.assertEqual(second.result(), "sent")
                self.assertIs(first_job.state, JobState.DONE)
                self.assertIs(second_job.state, JobState.DONE)
                self.assertEqual(self.runtime.relay.buffered_bytes, 0)
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

    async def test_cancelled_worker_releases_only_its_dequeued_batch(self):
        from professor_compressor.domain import DeliveryRequest, JobState

        sending = asyncio.Event()

        async def send(*_):
            sending.set()
            await asyncio.Future()

        runtime = Runtime(Settings(discord_token=""), deliver_results=send)
        runtime.relay.queue = asyncio.Queue(2)
        for token, payload in (("in-flight", b"first"), ("queued", b"second")):
            job = UploadJob(
                token, 1, 2, 9999999999, 1000, Mock(), state=JobState.QUEUED
            )
            runtime.sessions.jobs[token] = job
            runtime.relay.buffered_bytes += len(payload)
            runtime.relay.queue.put_nowait(
                DeliveryRequest(
                    job,
                    [BrowserResult("clip.mp4", payload)],
                    asyncio.get_running_loop().create_future(),
                )
            )
        in_flight = runtime.sessions.jobs["in-flight"]
        worker = asyncio.create_task(runtime.delivery.run_worker(1))
        try:
            await asyncio.wait_for(sending.wait(), 2)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self.assertEqual(runtime.relay.buffered_bytes, len(b"second"))
        self.assertIs(in_flight.state, JobState.DONE)
        self.assertEqual(set(runtime.sessions.jobs), {"queued"})
        self.assertEqual(runtime.sessions.outcomes, {})
        remaining = runtime.relay.queue.get_nowait()
        self.assertEqual(remaining.job.token, "queued")
        remaining.completed.cancel()
        runtime.relay.queue.task_done()
        await asyncio.wait_for(runtime.relay.queue.join(), 2)

    def test_extreme_encoding_speed_cannot_break_delivery_logging(self):
        import asyncio
        import time

        from professor_compressor.domain import DeliveryRequest
        from professor_compressor.relay.worker import log_delivery_diagnostics

        job = UploadJob("x", 1, 2, 9999999999, 20 * 1024 * 1024, Mock())
        delivery = DeliveryRequest(
            job,
            [BrowserResult("private.mp4", b"x")],
            asyncio.get_event_loop().create_future(),
            diagnostics=[CompressionDiagnostic(100, 86400, 100, False, "mp4", 1e-320)],
        )
        with patch("professor_compressor.relay.worker.logger.info") as log:
            log_delivery_diagnostics(delivery, time.monotonic(), "delivered")
        payload = json.loads(log.call_args.args[1])
        self.assertNotIn("encoding_speed", payload["files"][0])
        self.assertNotIn("private.mp4", str(payload))
