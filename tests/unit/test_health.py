from unittest.mock import Mock, patch

from tests.support.runtime import RelayTestCase


class HealthTests(RelayTestCase):
    async def test_readiness_checks_discord_workers_sync_and_drain(self):
        healthy_workers = [
            Mock(done=Mock(return_value=False))
            for _ in range(self.runtime.settings.delivery_workers)
        ]
        for discord_ready, synced, workers, draining, expected in (
            (False, True, healthy_workers, False, 503),
            (True, False, healthy_workers, False, 503),
            (True, True, [], False, 503),
            (True, True, healthy_workers, True, 503),
            (True, True, healthy_workers, False, 200),
        ):
            with (
                patch.object(
                    self.runtime.client, "is_ready", return_value=discord_ready
                ),
                patch.object(self.runtime.command_sync, "synced", synced),
                patch.object(self.runtime.delivery, "workers", workers),
                patch.object(self.runtime.sessions, "draining", draining),
            ):
                response = await self.client.get("/readyz")
                self.assertEqual(response.status, expected)
                self.assertIn("noindex", response.headers["X-Robots-Tag"])
                live = await self.client.get("/healthz")
                self.assertEqual(live.status, 200)
