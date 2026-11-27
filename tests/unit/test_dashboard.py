import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp import BasicAuth

from tests.support.runtime import DiagnosticsTestCase
from tests.support.waiting import wait_until


class DashboardTests(DiagnosticsTestCase):
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
        with patch.object(self.runtime.health, "client", fake):
            response = await self.client.get(
                "/ops/data?reference=PC-000000000000&tests=0",
                auth=BasicAuth("admin", self.key),
            )
            result = await response.json()
            self.assertEqual(result["runtime"]["connected_servers"], 2)
            self.assertTrue(result["runtime"]["server_count_current"])
            self.assertEqual(result["runtime"]["unavailable_servers"], 1)
            self.assertEqual(result["runtime"]["gateway_latency_ms"], 42)
            self.assertNotIn("private-guild", json.dumps(result))
            fake.guilds.pop()
            self.assertEqual(
                self.runtime.health.dashboard_snapshot()["connected_servers"], 1
            )
            fake.is_ready.return_value = False
            fake.latency = float("nan")
            cached = self.runtime.health.dashboard_snapshot()
            self.assertEqual(cached["connected_servers"], 1)
            self.assertFalse(cached["server_count_current"])
            self.assertIsNone(cached["gateway_latency_ms"])
            fake.guilds.clear()
            self.assertIsNone(
                self.runtime.health.dashboard_snapshot()["connected_servers"]
            )

    async def test_dashboard_auth_attempts_are_bounded(self):
        for _ in range(10):
            response = await self.client.get("/ops", auth=BasicAuth("admin", "bad"))
            self.assertEqual(response.status, 401)
        response = await self.client.get("/ops", auth=BasicAuth("admin", "bad"))
        self.assertEqual(response.status, 429)
        response = await self.client.get("/ops", auth=BasicAuth("admin", self.key))
        self.assertEqual(response.status, 200)

    async def test_data_has_schema_version_summary_and_strict_reference_lookup(self):
        auth = BasicAuth("admin", self.key)
        self.assertTrue(wait_until(lambda: self.store.ready, timeout=3))
        self.runtime.track(self.job, "created")
        self.runtime.track(
            self.job,
            "client_failed",
            source="browser",
            data={"error_code": "BROWSER_MEMORY", "stage": "encoding"},
        )
        self.store._queue.join()
        response = await self.client.get("/ops/data?tests=1", auth=auth)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        result = await response.json()
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["window"]["days"], 7)
        self.assertTrue(result["window"]["include_tests"])
        for key in ("funnel", "failures", "percentiles", "growth", "store"):
            self.assertIn(key, result)
        summary = result["summary"]
        self.assertIn(self.job.diagnostic_reference, summary)
        for private in (
            "private-page-secret",
            self.job.diagnostic_id,
            "diagnostic-test",
            result["sessions"][0]["user_key"],
            result["sessions"][0]["guild_key"],
        ):
            self.assertNotIn(private, summary)
        for bad in (
            "PC-NONE",
            "PC-12345",
            "PC-0123456789AG",
            "x" * 64,
            "PC-0123456789ABC",
        ):
            response = await self.client.get(
                "/ops/data", params={"reference": bad}, auth=auth
            )
            self.assertEqual(response.status, 400, bad)
            self.assertEqual(response.headers["Cache-Control"], "no-store")
        response = await self.client.get(
            "/ops/data?tests=1&reference=PC-000000000000", auth=auth
        )
        missing = await response.json()
        self.assertFalse(missing["lookup"]["found"])
        self.assertEqual(missing["lookup"]["events"], [])
        response = await self.client.get(
            "/ops/data",
            params={
                "tests": "1",
                "reference": " " + self.job.diagnostic_reference.lower() + " ",
            },
            auth=auth,
        )
        lookup = (await response.json())["lookup"]
        self.assertTrue(lookup["found"])
        self.assertEqual(lookup["reference"], self.job.diagnostic_reference)
        self.assertEqual(
            [event["event"] for event in lookup["events"]],
            ["created", "client_failed"],
        )
        self.assertNotIn("user_key", json.dumps(lookup))
