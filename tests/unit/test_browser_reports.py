import asyncio
import json
import uuid
from unittest.mock import AsyncMock, Mock, patch

from professor_compressor.domain import (
    JobState,
    UploadJob,
)
from professor_compressor.web.report_routes import logger
from tests.support.runtime import (
    DiagnosticsTestCase,
    RelayTestCase,
    RuntimeTestCase,
    WebAppTestCase,
)


class ProgressReportTests(RelayTestCase):
    async def test_progress_reports_are_authenticated_bounded_and_deduplicated(self):
        job = self.job()
        url = "/upload/audit/progress"
        headers = {"X-Upload-Session": "private"}
        with patch.object(self.runtime.alerts, "schedule") as alert:
            self.assertEqual(
                (await self.client.post(url, json={"event": "started"})).status, 403
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "selected", "count": 11}
                    )
                ).status,
                400,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "selected", "count": True}
                    )
                ).status,
                400,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "unknown"}
                    )
                ).status,
                400,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url,
                        headers=headers,
                        json={
                            "event": "selected",
                            "count": 2,
                            "filename": "private.mov",
                        },
                    )
                ).status,
                200,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "selected", "count": 1}
                    )
                ).status,
                200,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "started"}
                    )
                ).status,
                200,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "started"}
                    )
                ).status,
                200,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "cancelled"}
                    )
                ).status,
                200,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "page_left"}
                    )
                ).status,
                200,
            )
            self.assertEqual(
                (
                    await self.client.post(
                        url, headers=headers, json={"event": "page_left"}
                    )
                ).status,
                200,
            )
            self.assertEqual(alert.call_count, 4)
            self.assertTrue(job.page_left)
            self.assertEqual(job.selected_count, 1)
            self.assertNotIn(
                "private.mov", "\n".join(call.args[0] for call in alert.call_args_list)
            )

    async def test_failed_outcome_is_authenticated_and_not_requeued(self):
        self.job()
        with (
            patch.object(
                self.runtime.delivery,
                "deliver_browser_results",
                AsyncMock(side_effect=RuntimeError("Permission changed")),
            ) as sender,
            patch.object(self.runtime.alerts, "schedule"),
        ):
            worker = asyncio.create_task(self.runtime.delivery.run_worker(1))
            try:
                response = await self.post_video()
                self.assertEqual(response.status, 502)
                repeated = await self.post_video()
                self.assertEqual(repeated.status, 502)
                sender.assert_awaited_once()
                private = await self.client.get("/upload/audit/status")
                self.assertEqual(private.status, 403)
                self.assertNotIn("Permission changed", await private.text())
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)


class TelemetryReportTests(WebAppTestCase):
    async def test_telemetry_authentication_sanitization_and_receipt(self):
        job = UploadJob(
            "telemetry-test",
            1,
            2,
            9999999999,
            20 * 1024 * 1024,
            Mock(),
            state=JobState.CLAIMED,
            claim_secret="test-secret",
        )
        self.runtime.sessions.jobs[job.token] = job
        url = "/upload/telemetry-test/telemetry"
        response = await self.client.post(url, json={"outcome": "delivered"})
        self.assertEqual(response.status, 403)
        headers = {"X-Upload-Session": "test-secret"}
        with patch.object(logger, "info") as log:
            response = await self.client.post(
                url,
                headers=headers,
                json={
                    "outcome": "failed",
                    "category": "network",
                    "stage": "delivery",
                    "total_seconds": 12.5,
                    "caption": "private text",
                    "filename": "private.mp4",
                },
            )
            self.assertEqual(response.status, 200)
            payload = json.loads(log.call_args.args[1])
            self.assertEqual(payload["total_seconds"], 12.5)
            self.assertNotIn("private", str(payload))
        self.runtime.sessions.record_outcome(job, 200, {"ok": True})
        try:
            response = await self.client.post(
                url, headers=headers, json={"outcome": "delivered"}
            )
            self.assertEqual(response.status, 200)
            response = await self.client.post(url, headers=headers, data="x" * 5000)
            self.assertEqual(response.status, 400)
        finally:
            self.runtime.sessions.outcomes.pop(job.token, None)

    async def test_malformed_secret_and_failure_reports(self) -> None:
        job = UploadJob(
            token="reports",
            user_id=1,
            channel_id=2,
            expires_at=9999999999,
            discord_limit=1000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="secret",
        )
        self.runtime.sessions.jobs[job.token] = job
        response = await self.client.post(
            "/upload/reports", headers={"X-Upload-Session": "é"}
        )
        self.assertEqual(response.status, 403)
        for payload, expected in (([], 400), ({"stage": "x" * 5000}, 413)):
            response = await self.client.post(
                "/upload/reports/failure",
                json=payload,
                headers={"X-Upload-Session": "secret"},
            )
            self.assertEqual(response.status, expected)


class FailureReportTests(RuntimeTestCase):
    async def test_browser_failure_is_authenticated_and_allows_retry(self) -> None:
        job = UploadJob(
            token="failure-token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
            guild_id=789,
            guild_name="Test Server",
            username="clip_creator",
        )
        self.runtime.sessions.jobs[job.token] = job
        request = Mock()
        request.match_info = {"token": job.token}
        request.headers = {"X-Upload-Session": "claim-secret"}
        request.json = AsyncMock(return_value={"stage": "Browser compression<script>"})
        request.clone.return_value = request
        try:
            with patch.object(self.runtime.alerts, "schedule") as schedule_alert:
                response = await self.runtime.report_routes.receive_browser_failure(
                    request
                )

            self.assertEqual(response.status, 200)
            self.assertIs(job.state, JobState.CLAIMED)
            self.assertIn(job.token, self.runtime.sessions.jobs)
            self.assertTrue(job.browser_failure_reported)
            alert = schedule_alert.call_args.args[0]
            self.assertIn("Compression failed", alert)
            self.assertIn("Username: **@clip\\_creator**", alert)
            self.assertIn("User ID: `123`", alert)
            self.assertIn("Browser compressionscript", alert)
            self.assertNotIn("<script>", alert)
        finally:
            self.runtime.sessions.jobs.pop(job.token, None)

    async def test_browser_failure_alert_is_only_sent_once(self) -> None:
        job = UploadJob(
            token="failure-token",
            user_id=123,
            channel_id=456,
            expires_at=9999999999,
            discord_limit=20_000_000,
            interaction=Mock(),
            state=JobState.CLAIMED,
            claim_secret="claim-secret",
            browser_failure_reported=True,
        )
        self.runtime.sessions.jobs[job.token] = job
        request = Mock()
        request.match_info = {"token": job.token}
        request.headers = {"X-Upload-Session": "claim-secret"}
        request.json = AsyncMock(return_value={"stage": "Browser compression"})
        request.clone.return_value = request
        try:
            with patch.object(self.runtime.alerts, "schedule") as schedule_alert:
                response = await self.runtime.report_routes.receive_browser_failure(
                    request
                )
        finally:
            self.runtime.sessions.jobs.pop(job.token, None)

        self.assertEqual(response.status, 200)
        schedule_alert.assert_not_called()


class DiagnosticEventTests(DiagnosticsTestCase):
    async def test_authentication_validation_and_content_sanitization(self):
        response = await self.client.post(self.url, json=self.payload())
        self.assertEqual(response.status, 403)
        payload = self.payload()
        response = await self.client.post(self.url, json=payload, headers=self.headers)
        self.assertEqual(response.status, 200)
        for bad in (["delivered"], "delivered"):
            payload["events"][0]["event"] = bad
            response = await self.client.post(
                self.url, json=payload, headers=self.headers
            )
            self.assertEqual(response.status, 400)
        await asyncio.to_thread(self.store._queue.join)
        result = self.store.query(
            include_tests=True, reference=self.job.diagnostic_reference
        )
        self.assertEqual(
            result["events"][0]["data"], {"error_code": "METADATA_INVALID"}
        )
        self.assertNotIn("private", json.dumps(result))

    async def test_oversized_events_and_per_session_budget(self):
        response = await self.client.post(
            self.url, data="x" * 20000, headers=self.headers
        )
        self.assertEqual(response.status, 400)
        self.runtime.sessions.diagnostic_event_counts[self.job.diagnostic_id] = 1000
        response = await self.client.post(
            self.url, json=self.payload(), headers=self.headers
        )
        self.assertEqual(response.status, 429)

    async def test_diagnostic_request_rate_limit_remains_enforced(self):
        payload = {"run_id": uuid.uuid4().hex, "events": []}
        for _ in range(120):
            response = await self.client.post(
                self.url, json=payload, headers=self.headers
            )
            self.assertEqual(response.status, 200)
        response = await self.client.post(self.url, json=payload, headers=self.headers)
        self.assertEqual(response.status, 429)
        self.assertEqual((await response.json())["code"], "rate_limited")

    async def test_receipt_auth_allows_late_reporting_but_not_forged_delivery(self):
        self.job.browser_secret = "browser-secret"
        self.runtime.sessions.record_outcome(self.job, 200, {"ok": True})
        self.runtime.sessions.jobs.pop(self.job.token)
        response = await self.client.post(
            self.url, json=self.payload(), headers=self.headers
        )
        self.assertEqual(response.status, 200)
        response = await self.client.post(
            self.url, json=self.payload(), headers={"X-Upload-Session": "wrong"}
        )
        self.assertEqual(response.status, 403)

    async def test_late_failure_does_not_alert_or_suppress_current_run_failure(self):
        self.job.active_run_id = uuid.uuid4().hex
        with patch.object(self.runtime.alerts, "schedule") as alert:
            response = await self.client.post(
                "/upload/diagnostic-test/failure",
                headers=self.headers,
                json={"run_id": uuid.uuid4().hex, "error_code": "METADATA_INVALID"},
            )
            self.assertEqual(response.status, 200)
            alert.assert_not_called()
            self.assertFalse(self.job.browser_failure_reported)
            response = await self.client.post(
                "/upload/diagnostic-test/failure",
                headers=self.headers,
                json={"run_id": self.job.active_run_id, "error_code": "ENCODE_FAILED"},
            )
            self.assertEqual(response.status, 200)
            alert.assert_called_once()

    async def test_compact_failure_sanitizes_evidence_and_file_index(
        self,
    ):
        run = uuid.uuid4().hex
        with patch.object(self.runtime.alerts, "schedule"):
            response = await self.client.post(
                "/upload/diagnostic-test/failure",
                headers=self.headers,
                json={
                    "run_id": run,
                    "file_index": 1,
                    "operation": "encoding",
                    "error_code": "BROWSER_MEMORY",
                    "engine_signal": "oom",
                    "signal_source": "engine_log",
                    "exception_kind": "TypeError",
                    "encoder_threads": 4,
                    "raw_log": "secret.mp4",
                },
            )
        self.assertEqual(response.status, 200)
        await asyncio.to_thread(self.store._queue.join)
        result = self.store.query(
            include_tests=True, reference=self.job.diagnostic_reference
        )
        failure = next(e for e in result["events"] if e["event"] == "client_failed")
        self.assertEqual(failure["file_index"], 1)
        self.assertEqual(failure["data"]["engine_signal"], "oom")
        self.assertNotIn("secret.mp4", json.dumps(result))
        with patch.object(self.runtime.alerts, "schedule"):
            response = await self.client.post(
                "/upload/diagnostic-test/failure",
                headers=self.headers,
                json={"run_id": uuid.uuid4().hex, "file_index": True},
            )
        self.assertEqual(response.status, 200)
        await asyncio.to_thread(self.store._queue.join)
        result = self.store.query(
            include_tests=True, reference=self.job.diagnostic_reference
        )
        self.assertEqual(result["events"][-1]["file_index"], 0)

    async def test_storage_failure_never_interrupts_core_progress(self):
        with patch.object(
            self.store, "record", side_effect=RuntimeError("disk unavailable")
        ):
            response = await self.client.post(
                "/upload/diagnostic-test/progress",
                json={"event": "started", "run_id": uuid.uuid4().hex},
                headers=self.headers,
            )
        self.assertEqual(response.status, 200)
        self.assertTrue(self.job.compression_started)
