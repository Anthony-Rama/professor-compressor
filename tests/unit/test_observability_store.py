import json
import sqlite3
import time
import uuid
from pathlib import Path
from unittest.mock import patch

from professor_compressor.observability.contract import sanitize
from professor_compressor.observability.store import EventStore
from tests.support.observability import EventStoreTestCase
from tests.support.waiting import wait_until


class EventStoreTests(EventStoreTestCase):
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

    def test_upload_reset_facts_are_allowlisted_without_raw_connection_details(self):
        facts = {
            "stage": "upload",
            "error_code": "UPLOAD_CONNECTION_RESET",
            "received_bytes": 1024,
            "receive_idle_seconds": 12.5,
            "upload_attempts": 2,
        }
        self.assertEqual(
            sanitize({**facts, "raw_error": "secret", "filename": "private.mp4"}), facts
        )
        self.assertEqual(
            sanitize({"error_code": "TARGET_NOT_FEASIBLE"}),
            {"error_code": "TARGET_NOT_FEASIBLE"},
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

    def test_engine_details_are_bounded_and_never_accept_raw_logs_or_metadata(self):
        details = {
            "engine_signal": "oom",
            "signal_source": "engine_log",
            "exception_kind": "TypeError",
            "video_profile": "Main 10",
            "pixel_format": "yuv420p10le",
            "bit_depth": 10,
            "decoder_mode": "auto",
            "encoder_threads": 4,
            "exit_code": 0,
            "page_hidden": False,
            "cross_origin_isolated": True,
            "asset_build": "617df09cbdea3add",
        }
        self.assertEqual(sanitize(details), details)
        self.assertEqual(
            sanitize(
                {
                    "engine_signal": "private filename",
                    "video_profile": "<script>private</script>",
                    "pixel_format": "private",
                    "raw_log": "Aborted(OOM) secret.mp4",
                    "tags": {"title": "private"},
                    "device_memory": 8,
                    "hardware_concurrency": 16,
                    "asset_build": "https://secret.invalid",
                    "decoder_threads": -1,
                    "encoder_threads": True,
                    "bit_depth": 100,
                    "exit_code": 256,
                    "client_sequence": float("nan"),
                    "page_hidden": "false",
                }
            ),
            {},
        )

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

    def test_late_post_delivery_failure_is_not_a_recovery(self):
        self.emit("delivered", data={"files": 1})
        self.emit("client_failed", source="browser")
        self.assertEqual(self.view()["totals"]["recovered"], 0)

    def test_late_browser_failure_preserves_server_delivery_failure(self):
        self.emit("started")
        self.emit(
            "delivery_failed",
            source="server",
            data={"stage": "delivery", "error_code": "DELIVERY_UNCONFIRMED"},
        )
        # The original page observes the receipt as HTTP 502 and reports it.
        self.emit("client_failed", source="browser", data={"error_code": "HTTP"})
        result = self.view()
        self.assertEqual(result["sessions"][0]["status"], "delivery_failed")
        self.assertEqual(
            result["sessions"][0]["failure_data"]["error_code"],
            "DELIVERY_UNCONFIRMED",
        )
        self.assertEqual(result["events"][-1]["data"]["error_code"], "HTTP")
        # A later authoritative success still wins; prior failures remain history.
        self.emit("delivered", source="server", data={"files": 1})
        self.assertEqual(self.view()["sessions"][0]["status"], "delivered")
        # A different run must not inherit the old delivery failure details.
        self.run = uuid.uuid4().hex
        self.emit("started")
        self.emit("client_failed", source="browser", data={"error_code": "NETWORK"})
        self.assertEqual(
            self.view()["sessions"][0]["failure_data"]["error_code"], "NETWORK"
        )

    def test_restart_preserves_history_and_marks_incomplete_work_unknown(self):
        self.emit("created")
        self.emit("started")
        self.view()
        self.store.stop()
        reopened = EventStore(self.store.path)
        reopened.start()
        self.addCleanup(reopened.stop)
        wait_until(lambda: reopened.ready, timeout=3)
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
                "UPDATE sessions SET updated=?", (time.time() - 100 * 86400,)
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

    def test_stall_is_diagnostic_not_a_failure_and_worker_codes_are_preserved(self):
        self.emit("created")
        self.emit("started")
        self.emit(
            "processing_stalled",
            source="browser",
            data={"stage": "encoding", "progress_idle_seconds": 125},
        )
        result = self.view()
        self.assertEqual(result["sessions"][0]["status"], "processing")
        self.assertEqual(result["sessions"][0]["failures"], 0)
        self.emit(
            "client_failed",
            source="browser",
            data={"error_code": "WORKER_CRASH", "raw_error": "private details"},
        )
        self.assertEqual(
            self.view()["sessions"][0]["failure_data"],
            {"error_code": "WORKER_CRASH"},
        )

    def test_ninety_day_window_preserves_older_history_and_prunes_expired_history(self):
        self.emit("created")
        self.view()
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            old = time.time() - 65 * 86400
            connection.execute("UPDATE sessions SET created=?,updated=?", (old, old))
            connection.execute("UPDATE events SET at=?", (old,))
            self.store._prune(connection)
        self.assertEqual(self.view(days=30)["totals"]["sessions"], 0)
        self.assertEqual(self.view(days=90)["totals"]["sessions"], 1)
        with sqlite3.connect(self.store.path) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "UPDATE sessions SET updated=?", (time.time() - 91 * 86400,)
            )
            self.store._prune(connection)
        self.assertEqual(self.view(days=90)["totals"]["sessions"], 0)

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
                "professor_compressor.observability.store.time.monotonic",
                side_effect=[0, 61],
            ),
        ):
            worker._worker()
        with sqlite3.connect(self.store.path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1
            )
        self.assertEqual(worker.errors, 1)
