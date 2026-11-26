import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp import BasicAuth

from tests.support.runtime import DiagnosticsTestCase


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
                "/ops/data?reference=PC-NONE&tests=0", auth=BasicAuth("admin", self.key)
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
