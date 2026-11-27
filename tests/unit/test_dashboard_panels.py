import json
import time
import uuid
from unittest.mock import patch

from professor_compressor.observability.contract import diagnostic_reference
from professor_compressor.observability.queries import likely_reconnect, query_dashboard
from professor_compressor.observability.report import anomalies, summary_markdown
from tests.support.observability import EventStoreTestCase

USER = "a" * 32
GUILD = "b" * 32


class DashboardPanelTests(EventStoreTestCase):
    """Funnel, failure groups, percentiles and growth over seeded sessions."""

    def record_session(self, *events, at=None, **identity):
        """Record one session's events and return its reference."""
        session = uuid.uuid4().hex
        run = uuid.uuid4().hex
        for item in events:
            event, options = (item, {}) if isinstance(item, str) else item
            with patch(
                "professor_compressor.observability.store.time.time",
                return_value=at or time.time(),
            ):
                self.store.record(
                    session,
                    diagnostic_reference(session),
                    event,
                    run_id=run,
                    user_key=identity.get("user_key", USER),
                    guild_key=identity.get("guild_key", GUILD),
                    is_test=identity.get("is_test", False),
                    **options,
                )
        return diagnostic_reference(session)

    def all(self, **kwargs):
        self.store._queue.join()
        return self.store.query(**kwargs)

    def failed(self, code, stage, browser="Chrome", device="desktop", **extra):
        return (
            "client_failed",
            {
                "source": "browser",
                "file_index": 1,
                "data": {
                    "error_code": code,
                    "stage": stage,
                    "browser": browser,
                    "device": device,
                    **extra,
                },
            },
        )

    def test_funnel_steps_outcomes_and_conversion(self):
        delivered = ("created", "opened", "selected", "started", "received")
        self.record_session(*delivered, ("delivered", {"data": {"files": 1}}))
        self.record_session(*delivered, ("delivered", {"data": {"files": 2}}))
        # An older client skipped "selected": later steps still imply it.
        self.record_session(
            "created", "opened", "started", self.failed("ENCODE_FAILED", "encoding")
        )
        self.record_session("created", "opened", "selected", "cancelled")
        self.record_session("created", "opened", "expired")
        self.record_session("created", "expired")
        old = time.time() - 3 * 3600
        self.record_session("created", "opened", "selected", "started", at=old)
        funnel = self.all()["funnel"]
        steps = {step["step"]: step for step in funnel["steps"]}
        self.assertEqual(
            [step["sessions"] for step in funnel["steps"]], [7, 6, 5, 4, 2, 2]
        )
        self.assertIsNone(steps["created"]["step_percent"])
        self.assertEqual(steps["opened"]["step_percent"], 85.7)
        self.assertEqual(steps["delivered"]["step_percent"], 100.0)
        self.assertEqual(steps["delivered"]["overall_percent"], 28.6)
        self.assertEqual(
            funnel["outcomes"],
            {
                "delivered": 2,
                "failed": 1,
                "cancelled": 1,
                "in_progress": 0,
                "unknown": 1,
                "abandoned": 1,
                "expired": 1,
            },
        )
        self.assertEqual(funnel["failure_percent"], 33.3)
        self.assertEqual(funnel["bucket_seconds"], 86400)
        self.assertEqual(sum(point["delivered"] for point in funnel["trend"]), 2)
        self.assertEqual(sum(point["failed"] for point in funnel["trend"]), 1)
        hourly = self.all(days=1)["funnel"]
        self.assertEqual(hourly["bucket_seconds"], 3600)
        self.assertEqual(sum(point["sessions"] for point in hourly["trend"]), 7)

    def test_previous_window_comparison_and_failure_spike(self):
        earlier = time.time() - 1.5 * 86400
        for _ in range(9):
            self.record_session(
                "created", "started", ("delivered", {"data": {"files": 1}}), at=earlier
            )
        self.record_session(
            "created", "started", self.failed("UNKNOWN", "encoding"), at=earlier
        )
        for _ in range(5):
            self.record_session(
                "created", "started", ("delivered", {"data": {"files": 1}})
            )
        for _ in range(3):
            self.record_session(
                "created", "started", self.failed("WASM_TRAP", "encoding")
            )
        result = self.all(days=1)
        self.assertEqual(result["comparison"]["failure_percent"], 10.0)
        self.assertEqual(result["comparison"]["error_codes"], {"UNKNOWN": 1})
        self.assertEqual(result["funnel"]["failure_percent"], 37.5)
        codes = {item["code"] for item in anomalies(result)}
        self.assertIn("failure_rate_spike", codes)
        self.assertIn("new_error_code", codes)
        self.assertNotIn("failure_rate_spike", {a["code"] for a in anomalies({})})

    def test_failures_group_by_code_and_stage_with_mix_and_references(self):
        references = [
            self.record_session(
                "created", "started", self.failed("BROWSER_MEMORY", "encoding")
            )
            for _ in range(4)
        ]
        self.record_session(
            "created",
            "started",
            self.failed(
                "BROWSER_MEMORY", "encoding", browser="Safari", device="mobile"
            ),
            self.failed(
                "BROWSER_MEMORY", "encoding", browser="Safari", device="mobile"
            ),
            ("started", {}),
            ("delivered", {"data": {"files": 1}}),
        )
        # A server-side failure takes its browser from the session's reports.
        self.record_session(
            "created",
            ("stage_started", {"source": "browser", "data": {"browser": "Firefox"}}),
            ("upload_failed", {"data": {"error_code": "UPLOAD_TIMEOUT"}}),
        )
        self.record_session(
            "created",
            (
                "stage_finished",
                {
                    "source": "browser",
                    "data": {
                        "stage": "preparation",
                        "outcome": "failed",
                        "error_code": "INPUT_STORAGE_FULL",
                    },
                },
            ),
        )
        self.record_session("created", self.failed("CANCELLED", "encoding"))
        failures = self.all()["failures"]
        self.assertEqual(failures["sessions"], 7)
        self.assertFalse(failures["truncated"])
        top = failures["groups"][0]
        self.assertEqual(
            (top["error_code"], top["stage"]), ("BROWSER_MEMORY", "encoding")
        )
        self.assertEqual(top["sessions"], 5)
        self.assertEqual(top["reports"], 6)
        self.assertEqual(top["recovered"], 1)
        self.assertEqual(top["share_percent"], 71.4)
        self.assertEqual(top["browsers"], {"Chrome": 4, "Safari": 1})
        self.assertEqual(top["devices"], {"desktop": 4, "mobile": 1})
        self.assertEqual(len(top["references"]), 3)
        self.assertTrue(set(top["references"][1:]) <= set(references))
        groups = {(g["error_code"], g["stage"]): g for g in failures["groups"]}
        self.assertEqual(groups["UPLOAD_TIMEOUT", "upload"]["browsers"], {"Firefox": 1})
        self.assertIn(("INPUT_STORAGE_FULL", "preparation"), groups)
        self.assertNotIn("CANCELLED", {code for code, _ in groups})

    def test_percentiles_for_speed_delivery_and_sizes(self):
        for index, (duration, encoding) in enumerate(
            ((60, 30), (60, 20), (60, 10), (60, 60), (60, 5)), 1
        ):
            self.record_session(
                "created",
                (
                    "file_ready",
                    {
                        "source": "browser",
                        "file_index": 1,
                        "data": {
                            "duration_seconds": duration,
                            "encoding_seconds": encoding,
                            "path": "compressed",
                        },
                    },
                ),
                (
                    "file_received",
                    {
                        "file_index": 1,
                        "data": {
                            "input_bytes": index * 1000,
                            "output_bytes": index * 100,
                        },
                    },
                ),
                ("received", {"data": {"relay_receive_seconds": index}}),
            )
        self.record_session(
            "created",
            (
                "file_ready",
                {
                    "source": "browser",
                    "data": {
                        "duration_seconds": 60,
                        "encoding_seconds": 0.01,
                        "path": "remuxed",
                    },
                },
            ),
        )
        start = time.time() - 100
        session = uuid.uuid4().hex
        reference = diagnostic_reference(session)
        for event, at in (("started", start), ("delivered", start + 40)):
            with patch(
                "professor_compressor.observability.store.time.time", return_value=at
            ):
                self.store.record(session, reference, event, data={"queue_seconds": 2})
        result = self.all()
        percentiles = result["percentiles"]
        self.assertEqual(
            percentiles["encode_speed"], {"samples": 5, "p50": 3.0, "p90": 12.0}
        )
        self.assertEqual(percentiles["time_to_delivery_seconds"]["p50"], 40)
        self.assertEqual(
            percentiles["relay_receive_seconds"], {"samples": 5, "p50": 3, "p90": 5}
        )
        self.assertEqual(percentiles["input_bytes"]["p90"], 5000)
        self.assertEqual(percentiles["output_bytes"]["p50"], 300)
        self.assertEqual(percentiles["queue_seconds"]["samples"], 1)
        self.assertEqual(result["timings"]["relay_receive_seconds"]["p90"], 5)

    def test_reconnect_removals_are_marked_without_changing_history(self):
        start = time.time() - 600
        self.record_session(
            ("installed", {"data": {"connected_servers": 10}}), at=start - 900
        )
        self.record_session(
            ("removed", {"data": {"connected_servers": 9}}), at=start - 800
        )
        for offset in (5, 9):
            self.record_session(
                ("removed", {"data": {"connected_servers": 8}}), at=start + offset
            )
        result = self.all(include_tests=True)
        growth = self.store.query(days=1)  # Same view without a process start.
        self.assertEqual(growth["growth"]["likely_reconnect_removals"], 0)
        marked = query_dashboard(
            self.store.path, self.store.health, days=1, process_started=start
        )["growth"]
        self.assertEqual(marked["installs"], 1)
        self.assertEqual(marked["removals"], 3)
        self.assertEqual(marked["likely_reconnect_removals"], 2)
        self.assertEqual(marked["net_change"], 0)
        self.assertEqual(
            [item["likely_reconnect"] for item in marked["events"]],
            [False, False, True, True],
        )
        self.assertEqual(marked["daily"][-1]["connected_servers"], 8)
        self.assertEqual(result["growth"]["removals"], 3)

    def test_reconnect_rules(self):
        self.assertTrue(likely_reconnect(100, [100], [50], [], []))
        self.assertFalse(likely_reconnect(200, [200], [50], [], []))
        self.assertTrue(likely_reconnect(150, [150], [], [], [10]))
        # Near a release's first event, only a burst counts.
        self.assertFalse(likely_reconnect(100, [100], [], [90], []))
        self.assertTrue(likely_reconnect(100, [100, 104], [], [90], []))
        self.assertFalse(likely_reconnect(100, [100, 104], [], [], []))

    def test_lookup_returns_one_private_safe_timeline(self):
        reference = self.record_session(
            "created",
            "started",
            self.failed("BROWSER_MEMORY", "encoding", filename="PRIVATE_NAME"),
        )
        lookup = self.all(reference=reference)["lookup"]
        self.assertTrue(lookup["found"])
        self.assertEqual(lookup["reference"], reference)
        self.assertEqual(
            [event["event"] for event in lookup["events"]],
            ["created", "started", "client_failed"],
        )
        self.assertEqual(lookup["session"]["status"], "client_failed")
        text = json.dumps(lookup)
        for private in (USER, GUILD, "PRIVATE_NAME", "user_key", "guild_key"):
            self.assertNotIn(private, text)
        missing = self.all(reference="PC-000000000000")["lookup"]
        self.assertEqual(
            missing,
            {
                "reference": "PC-000000000000",
                "found": False,
                "session": None,
                "events": [],
            },
        )
        self.assertNotIn("lookup", self.all())

    def test_store_panel_reports_size_and_age(self):
        self.record_session("created", at=time.time() - 3600)
        store = self.all()["store"]
        self.assertEqual(store["events"], 1)
        self.assertEqual(store["sessions"], 1)
        self.assertLess(store["oldest_event"], time.time() - 3000)
        self.assertGreater(store["database_bytes"], 0)

    def test_summary_is_concise_and_has_no_private_fields(self):
        reference = self.record_session(
            "created",
            "started",
            self.failed("BROWSER_MEMORY", "encoding"),
            user_key=USER,
            guild_key=GUILD,
        )
        result = self.all()
        result.update(
            schema_version=1,
            runtime={
                "ready": True,
                "discord_ready": True,
                "revision": "abc123",
                "connected_servers": 12,
                "queue_depth": 0,
                "queue_capacity": 8,
                "counters": {"uptime_seconds": 7200},
            },
        )
        result["anomalies"] = anomalies(result)
        summary = summary_markdown(result)
        self.assertIn("## Funnel", summary)
        self.assertIn("| BROWSER_MEMORY | encoding | 1 |", summary)
        self.assertIn(reference, summary)
        self.assertIn("Connected servers 12", summary)
        session_id = result["sessions"][0]["id"]
        for private in (USER, GUILD, session_id, "user_key", "guild_key"):
            self.assertNotIn(private, summary)
        self.assertLess(len(summary), 8000)

    def test_summary_handles_unavailable_storage(self):
        summary = summary_markdown(
            {"storage": {"configured": True, "ready": False}, "schema_version": 1}
        )
        self.assertIn("Diagnostic store not ready", summary)
        self.assertIn("No failures reported", summary)
        self.assertEqual(
            anomalies({"storage": {"configured": True, "ready": False}})[0]["code"],
            "storage_unavailable",
        )
