import json
import time
import uuid
from unittest.mock import patch

from tests.support.observability import EventStoreTestCase


class DashboardQueryTests(EventStoreTestCase):
    def test_preparation_errors_are_searchable_without_counting_compression_failures(
        self,
    ):
        self.emit("created", source="server")
        self.emit("selected", source="server")
        self.emit(
            "stage_finished",
            source="browser",
            file_index=1,
            data={
                "stage": "preparation",
                "outcome": "failed",
                "error_code": "INPUT_STORAGE_FULL",
                "input_bytes": 123,
                "filename": "PRIVATE_FILENAME",
                "raw_error": "PRIVATE_PATH",
            },
        )
        result = self.view()
        self.assertEqual(result["totals"]["blocked_before_compression"], 1)
        self.assertEqual(result["totals"]["started"], 0)
        self.assertEqual(result["sessions"][0]["failures"], 0)
        self.assertEqual(result["sessions"][0]["status"], "selected")
        self.assertEqual(
            result["groups"]["error_code"][0]["value"], "INPUT_STORAGE_FULL"
        )
        self.assertNotIn("PRIVATE_", json.dumps(result))
        self.emit("started", source="server")
        self.assertEqual(self.view()["totals"]["blocked_before_compression"], 0)
        self.emit("delivered", source="server", data={"files": 1})
        self.assertEqual(self.view()["sessions"][0]["status"], "delivered")

    def test_engine_evidence_persists_and_supports_private_grouping_and_filters(self):
        self.emit("created")
        self.emit(
            "attempt_started", source="browser", file_index=1, data={"attempt": 1}
        )
        self.emit(
            "stage_finished",
            source="browser",
            file_index=1,
            data={
                "stage": "encoding",
                "engine_signal": "oom",
                "signal_source": "engine_log",
                "error_code": "BROWSER_MEMORY",
                "video_profile": "Main 10",
                "bit_depth": 10,
            },
        )
        self.emit(
            "client_failed",
            source="browser",
            file_index=1,
            data={
                "stage": "encoding",
                "error_code": "BROWSER_MEMORY",
                "engine_signal": "oom",
            },
        )
        result = self.view(dimension="engine_signal", value="oom")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["groups"]["engine_signal"][0]["value"], "oom")
        self.assertEqual(result["groups"]["engine_signal"][0]["sessions"], 1)
        self.assertTrue(any(e["data"].get("bit_depth") == 10 for e in result["events"]))
        self.assertEqual(
            self.view(dimension="engine_signal", value="wasm_bounds")["sessions"], []
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
            "professor_compressor.observability.store.time.time",
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

    def test_session_failure_detail_uses_active_run_and_excludes_late_old_failures(
        self,
    ):
        self.emit("created")
        self.emit("started")
        self.emit(
            "client_failed",
            source="browser",
            data={
                "stage": "encoding",
                "error_code": "BROWSER_MEMORY",
                "engine_signal": "oom",
            },
        )
        self.assertEqual(
            self.view()["sessions"][0]["failure_data"]["error_code"], "BROWSER_MEMORY"
        )
        previous = self.run
        self.run = uuid.uuid4().hex
        self.emit("started")
        self.store.record(
            self.session,
            self.reference,
            "client_failed",
            source="browser",
            run_id=previous,
            data={"error_code": "UNKNOWN"},
        )
        result = self.view()["sessions"][0]
        self.assertEqual(result["status"], "processing")
        self.assertEqual(result["failure_data"], {})
