import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp import BasicAuth
from aiohttp.test_utils import TestClient, TestServer

from professor_compressor import application as app
from professor_compressor.domain import JobState, UploadJob
from professor_compressor.observability import EventStore, sanitize


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = EventStore(
            str(Path(self.directory.name) / "events.sqlite3"), key="test-only" * 8
        )
        self.store.start()
        self.addCleanup(self.store.stop)
        deadline = time.monotonic() + 3
        while not self.store.ready and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.store.ready)
        self.session = uuid.uuid4().hex
        self.reference = "PC-" + self.session[:12].upper()
        self.run = uuid.uuid4().hex

    def emit(self, event, **kwargs):
        self.store.record(
            self.session, self.reference, event, run_id=self.run, **kwargs
        )

    def view(self, **kwargs):
        self.store._queue.join()
        return self.store.query(reference=self.reference, **kwargs)

    def test_allowlist_rejects_content_invalid_types_and_nonfinite_numbers(self):
        payload = {
            "filename": "private.mp4",
            "caption": "private",
            "token": "secret",
            "raw_error": "private.mp4",
            "stage": ["metadata"],
            "input_bytes": True,
            "output_bytes": 10**1000,
            "seconds": float("nan"),
            "input_fps": float("inf"),
            "error_code": "<script>private</script>",
            "browser": "Chrome",
            "video_codec": "h264",
            "duration_seconds": 12.5,
        }
        self.assertEqual(
            sanitize(payload),
            {"video_codec": "h264", "browser": "Chrome", "duration_seconds": 12.5},
        )

    def test_pseudonyms_are_keyed_and_distinct_by_type(self):
        self.assertEqual(
            self.store.pseudonym("user", 123), self.store.pseudonym("user", 123)
        )
        self.assertNotEqual(
            self.store.pseudonym("user", 123), self.store.pseudonym("guild", 123)
        )
        self.assertNotEqual(
            self.store.pseudonym("user", 123),
            EventStore(key="other").pseudonym("user", 123),
        )
        self.assertEqual(EventStore().pseudonym("user", 123), "")

    def test_retry_recovery_duplicates_and_late_browser_failure(self):
        self.emit("created")
        self.emit("started")
        self.emit(
            "client_failed",
            source="browser",
            event_id=self.run,
            data={"error_code": "METADATA_INVALID", "filename": "private"},
        )
        self.emit(
            "client_failed",
            source="browser",
            event_id=self.run,
            file_index=2,
            data={"stage": "metadata"},
        )
        self.emit("started")
        self.emit("delivered", data={"files": 2})
        self.emit("client_failed", source="browser", data={"error_code": "NETWORK"})
        result = self.view()
        self.assertEqual(result["totals"]["delivered"], 1)
        self.assertEqual(result["totals"]["recovered"], 1)
        self.assertEqual(result["sessions"][0]["status"], "delivered")
        self.assertEqual(result["sessions"][0]["files"], 2)
        failures = [e for e in result["events"] if e["event"] == "client_failed"]
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["data"]["stage"], "metadata")
        self.assertEqual(failures[0]["file_index"], 2)
        self.assertNotIn("private", json.dumps(result))

    def test_stale_run_report_does_not_mark_new_run_failed(self):
        self.emit("created")
        old_run = self.run
        self.emit("started")
        self.run = uuid.uuid4().hex
        self.emit("started")
        self.store.record(
            self.session,
            self.reference,
            "client_failed",
            run_id=old_run,
            source="browser",
        )
        self.assertEqual(self.view()["sessions"][0]["status"], "processing")

    def test_late_selection_does_not_regress_processing(self):
        self.emit("started")
        self.emit("selected")
        result = self.view()
        self.assertEqual(result["sessions"][0]["status"], "processing")
        self.assertEqual(result["totals"]["selected"], 1)

    def test_duplicate_failure_cannot_modify_different_event(self):
        self.emit("stage_started", source="browser", event_id=self.run)
        self.emit(
            "client_failed",
            source="browser",
            event_id=self.run,
            data={"error_code": "METADATA_INVALID"},
        )
        result = self.view()
        self.assertEqual(result["events"][0]["data"], {})
        self.assertEqual(result["sessions"][0]["failures"], 0)

    def test_unknown_duplicate_does_not_erase_specific_failure(self):
        self.emit(
            "client_failed",
            source="browser",
            event_id=self.run,
            data={"error_code": "METADATA_INVALID"},
        )
        self.emit(
            "client_failed",
            source="browser",
            event_id=self.run,
            data={"error_code": "UNKNOWN", "stage": "metadata"},
        )
        self.assertEqual(
            self.view()["events"][0]["data"]["error_code"], "METADATA_INVALID"
        )

    def test_cancellation_is_not_counted_as_failure_stage(self):
        self.emit(
            "stage_finished",
            source="browser",
            data={"stage": "engine", "error_code": "CANCELLED", "outcome": "cancelled"},
        )
        self.assertEqual(self.view()["groups"]["failure_stage"], [])

    def test_daily_activity_uses_event_time_not_session_creation(self):
        with patch(
            "professor_compressor.observability.time.time",
            return_value=time.time() - 2 * 86400,
        ):
            self.emit("created")
        self.emit("delivered", data={"files": 1})
        result = self.view(days=1)
        self.assertEqual(result["totals"]["sessions"], 0)
        self.assertEqual([row["event"] for row in result["activity"]], ["delivered"])

    def test_no_server_delivery_inferred_from_browser_complete(self):
        self.emit("created")
        self.emit("started")
        self.emit("client_complete", source="browser", data={"outcome": "delivered"})
        self.assertEqual(self.view()["totals"]["delivered"], 0)
        self.emit("expired")
        self.assertEqual(self.view()["sessions"][0]["status"], "unknown")

    def test_client_cannot_forge_delivery_or_inflate_delivered_file_count(self):
        self.emit("created")
        self.emit("file_ready", source="browser", data={"files": 100})
        self.emit("delivered", source="browser", data={"files": 100})
        self.assertEqual(self.view()["totals"]["delivered"], 0)
        self.emit("delivered", data={"files": 1})
        self.assertEqual(self.view()["totals"]["files"], 1)

    def test_expiration_preserves_reported_failure_and_cancelled_states(self):
        self.emit("created")
        self.emit("started")
        self.emit("client_failed", source="browser")
        self.emit("expired")
        self.assertEqual(self.view()["sessions"][0]["status"], "client_failed")
        self.emit("started")
        self.emit("cancelled")
        self.emit("expired")
        self.assertEqual(self.view()["sessions"][0]["status"], "cancelled")

    def test_test_sessions_excluded_by_default_and_not_lost_after_late_reports(self):
        self.emit("created", is_test=True)
        self.emit("started")
        self.assertEqual(self.view()["totals"]["sessions"], 0)
        self.assertEqual(self.view(include_tests=True)["totals"]["sessions"], 1)

    def test_installs_not_counted_as_compression_sessions(self):
        self.emit("installed", data={"connected_servers": 57})
        result = self.view()
        self.assertEqual(result["totals"]["sessions"], 0)
        self.assertEqual(result["activity"][0]["event"], "installed")

    def test_groups_percentiles_and_encoding_speed(self):
        self.emit("created")
        for index, seconds in enumerate((5, 10, 20), 1):
            self.emit(
                "file_ready",
                file_index=index,
                source="browser",
                data={
                    "encoding_seconds": seconds,
                    "duration_seconds": 30,
                    "input_format": "mp4",
                    "output_height": 720,
                    "browser": "Chrome",
                    "engine_seconds": 0.2,
                },
            )
        result = self.view()
        self.assertEqual(
            result["timings"]["encoding_seconds"], {"samples": 3, "p50": 10, "p95": 20}
        )
        self.assertEqual(result["performance"][0]["files"], 3)
        self.assertAlmostEqual(result["performance"][0]["speed"], 90 / 35)
        filtered = self.view(dimension="input_format", value="mp4")
        self.assertEqual(filtered["totals"]["sessions"], 1)
        self.assertEqual(
            self.view(dimension="browser", value="Firefox")["totals"]["sessions"], 0
        )

    def test_delivered_file_aggregate_uses_latest_server_receipt_only(self):
        self.emit("created")
        self.emit(
            "file_received",
            file_index=1,
            data={"input_bytes": 1000, "output_bytes": 500, "path": "compressed"},
        )
        self.emit(
            "file_received",
            file_index=1,
            data={"input_bytes": 1000, "output_bytes": 400, "path": "compressed"},
        )
        self.emit(
            "file_ready",
            source="browser",
            file_index=1,
            data={"input_bytes": 1000, "output_bytes": 400},
        )
        self.emit("file_received", file_index=2, data={"output_bytes": 200})
        self.assertEqual(self.view()["files"]["measured_files"], 0)
        self.emit("delivered", data={"files": 2})
        result = self.view()
        self.assertEqual(result["files"]["measured_files"], 1)
        self.assertEqual(result["files"]["input_bytes"], 1000)
        self.assertEqual(result["files"]["output_bytes"], 400)
        self.assertEqual(result["files"]["reduction_percent"], 60)

    def test_late_post_delivery_failure_is_not_a_recovery(self):
        self.emit("delivered", data={"files": 1})
        self.emit("client_failed", source="browser")
        self.assertEqual(self.view()["totals"]["recovered"], 0)

    def test_distinct_manual_runs_and_automatic_retries(self):
        self.emit("started")
        self.emit("started")
        self.emit("retry", source="browser", data={"attempt": 2})
        self.run = uuid.uuid4().hex
        self.emit("started")
        self.assertEqual(self.view()["retries"], {"runs": 2, "automatic_retries": 1})

    def test_extended_filters_and_hidden_test_notice(self):
        self.emit("created", is_test=True)
        self.emit(
            "file_ready",
            source="browser",
            data={
                "device": "desktop",
                "engine": "single-threaded",
                "path": "compressed",
            },
        )
        self.assertEqual(self.view()["collection"]["hidden_test_sessions"], 1)
        self.assertEqual(
            self.view(include_tests=True)["collection"]["hidden_test_sessions"], 0
        )
        self.assertEqual(
            self.view(dimension="browser", value="Firefox")["collection"][
                "hidden_test_sessions"
            ],
            0,
        )
        for field, value in (
            ("device", "desktop"),
            ("engine", "single-threaded"),
            ("path", "compressed"),
        ):
            result = self.view(include_tests=True, dimension=field, value=value)
            self.assertEqual(result["totals"]["sessions"], 1)
            self.assertEqual(result["groups"][field][0]["value"], value)

    def test_pending_cancelled_and_unknown_are_separate(self):
        self.emit("started")
        self.assertEqual(self.view()["totals"]["in_progress"], 1)
        self.emit("cancelled")
        result = self.view()["totals"]
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(result["needs_attention"], 0)
        self.emit("started")
        self.emit("interrupted")
        result = self.view()["totals"]
        self.assertEqual(result["unknown"], 1)
        self.assertEqual(result["needs_attention"], 0)

    def test_timing_samples_not_duplicated_by_repeated_measurement_fields(self):
        self.emit("created")
        self.emit("received", data={"relay_receive_seconds": 3})
        self.emit("delivered", data={"relay_receive_seconds": 3, "queue_seconds": 1})
        self.emit("client_complete", source="browser", data={"engine_seconds": 0})
        result = self.view()
        self.assertEqual(result["timings"]["relay_receive_seconds"]["samples"], 1)
        self.assertNotIn("engine_seconds", result["timings"])

    def test_restart_preserves_history_and_marks_incomplete_work_unknown(self):
        self.emit("created")
        self.emit("started")
        self.view()
        self.store.stop()
        reopened = EventStore(self.store.path)
        reopened.start()
        self.addCleanup(reopened.stop)
        deadline = time.monotonic() + 3
        while not reopened.ready and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(reopened.query()["sessions"][0]["status"], "unknown")

    def test_retention_and_row_cap(self):
        for _ in range(8):
            self.emit("stage_started")
        self.view()
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            self.store.max_events = 3
            self.store._prune(connection)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 3
            )
            connection.execute(
                "UPDATE sessions SET updated=?", (time.time() - 40 * 86400,)
            )
            self.store._prune(connection)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0
            )

    def test_full_queue_and_unavailable_storage_do_not_raise(self):
        blocked = EventStore("/unused", queue_size=1)
        blocked.record(self.session, self.reference, "created")
        blocked.record(self.session, self.reference, "opened")
        self.assertEqual(blocked.dropped, 1)
        broken = EventStore(str(Path(self.directory.name) / "missing" / "bad.sqlite3"))
        with patch.object(
            broken, "_connection", side_effect=OSError("disk unavailable")
        ):
            broken.start()
            broken._thread.join(timeout=2)
        self.assertFalse(broken.ready)
        self.assertEqual(broken.errors, 1)

    def test_transient_write_error_is_counted_and_later_records_recover(self):
        with patch.object(
            self.store, "_save", side_effect=sqlite3.OperationalError("disk busy")
        ):
            self.emit("created")
            self.store._queue.join()
        self.emit("started")
        self.emit("delivered", data={"files": 1})
        result = self.view()
        self.assertEqual(self.store.errors, 1)
        self.assertEqual(result["totals"]["delivered"], 1)

    def test_periodic_pruning_survives_a_failed_insert(self):
        for _ in range(8):
            self.emit("stage_started")
        self.view()
        self.store.stop()
        worker = EventStore(self.store.path, max_events=1)
        worker.record(self.session, self.reference, "started")
        worker._stop.set()  # Drain the queued record and return synchronously.
        prune = worker._prune
        calls = 0

        def periodic_only(connection):
            nonlocal calls
            calls += 1
            if calls > 1:
                prune(connection)

        with (
            patch.object(worker, "_prune", side_effect=periodic_only),
            patch.object(
                worker, "_save", side_effect=sqlite3.OperationalError("database full")
            ),
            patch(
                "professor_compressor.observability.time.monotonic", side_effect=[0, 61]
            ),
        ):
            worker._worker()
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
            )
        self.assertEqual(worker.errors, 1)


class DiagnosticHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = EventStore(
            str(Path(self.directory.name) / "events.sqlite3"), key="test-key" * 8
        )
        self.key = "operator-test-key-" * 3
        self.store_patch = patch.object(app, "event_store", self.store)
        self.settings_patch = patch.object(
            app,
            "settings",
            Mock(
                observability_admin_key=self.key,
                diagnostic_test_guild_ids=frozenset({999}),
            ),
        )
        self.store_patch.start()
        self.settings_patch.start()
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.settings_patch.stop)
        self.client = TestClient(TestServer(app.create_web_application()))
        await self.client.start_server()
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
        app.jobs[self.job.token] = self.job
        self.headers = {"X-Upload-Session": "private-page-secret"}
        self.url = "/upload/diagnostic-test/events"

    async def asyncTearDown(self):
        app.jobs.clear()
        app.outcomes.clear()
        app.request_times.clear()
        app.diagnostic_event_counts.clear()
        await self.client.close()

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
        app.diagnostic_event_counts[self.job.diagnostic_id] = 1000
        response = await self.client.post(
            self.url, json=self.payload(), headers=self.headers
        )
        self.assertEqual(response.status, 429)

    async def test_receipt_auth_allows_late_reporting_but_not_forged_delivery(self):
        self.job.browser_secret = "browser-secret"
        app.record_outcome(self.job, 200, {"ok": True})
        app.jobs.pop(self.job.token)
        response = await self.client.post(
            self.url, json=self.payload(), headers=self.headers
        )
        self.assertEqual(response.status, 200)
        response = await self.client.post(
            self.url, json=self.payload(), headers={"X-Upload-Session": "wrong"}
        )
        self.assertEqual(response.status, 403)

    async def test_dashboard_requires_auth_and_has_private_headers(self):
        response = await self.client.get("/ops")
        self.assertEqual(response.status, 401)
        response = await self.client.get("/ops", auth=BasicAuth("admin", self.key))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        response = await self.client.get("/ops/data", auth=BasicAuth("admin", self.key))
        self.assertEqual(response.status, 200)
        self.assertIn("storage", await response.json())
        response = await self.client.get(
            "/ops/data?days=bad", auth=BasicAuth("admin", self.key)
        )
        self.assertEqual(response.status, 400)

    async def test_live_server_count_is_current_private_and_filter_independent(self):
        fake = SimpleNamespace(
            guilds=[
                SimpleNamespace(unavailable=False, name="private-guild", id=123),
                SimpleNamespace(unavailable=True, name="private-guild", id=456),
            ],
            latency=0.042,
            is_ready=Mock(return_value=True),
        )
        with patch.object(app, "client", fake):
            response = await self.client.get(
                "/ops/data?reference=PC-NONE&tests=0", auth=BasicAuth("admin", self.key)
            )
            result = await response.json()
            self.assertEqual(result["runtime"]["connected_servers"], 2)
            self.assertTrue(result["runtime"]["server_count_current"])
            self.assertEqual(result["runtime"]["unavailable_servers"], 1)
            self.assertEqual(result["runtime"]["gateway_latency_ms"], 42)
            self.assertNotIn("private-guild", json.dumps(result))
            fake.guilds.pop()
            self.assertEqual(app.dashboard_runtime()["connected_servers"], 1)
            fake.is_ready.return_value = False
            fake.latency = float("nan")
            cached = app.dashboard_runtime()
            self.assertEqual(cached["connected_servers"], 1)
            self.assertFalse(cached["server_count_current"])
            self.assertIsNone(cached["gateway_latency_ms"])
            fake.guilds.clear()
            self.assertIsNone(app.dashboard_runtime()["connected_servers"])

    async def test_dashboard_auth_attempts_are_bounded(self):
        for _ in range(10):
            response = await self.client.get("/ops", auth=BasicAuth("admin", "bad"))
            self.assertEqual(response.status, 401)
        response = await self.client.get("/ops", auth=BasicAuth("admin", "bad"))
        self.assertEqual(response.status, 429)
        response = await self.client.get("/ops", auth=BasicAuth("admin", self.key))
        self.assertEqual(response.status, 200)

    async def test_late_failure_does_not_alert_or_suppress_current_run_failure(self):
        self.job.active_run_id = uuid.uuid4().hex
        with patch.object(app, "schedule_owner_alert") as alert:
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

    async def test_non_ascii_cookie_is_rejected_without_server_error(self):
        self.job.browser_secret = "expected-cookie"
        request = Mock(cookies={app.RESUME_COOKIE_NAME: "é"})
        self.assertFalse(app.browser_cookie_matches(request, self.job.browser_secret))

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
