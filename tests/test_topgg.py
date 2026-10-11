import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from professor_compressor import application as app
from professor_compressor.topgg import METRICS_URL, TopGGStats, retry_delay


class TopGGTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = Mock()
        self.client.user = SimpleNamespace(id=123)
        self.client.guilds = [object(), object()]
        self.client.is_ready.return_value = True
        self.client.is_closed.return_value = False
        self.client.wait_until_ready = AsyncMock()
        self.stats = TopGGStats(self.client, "private-test-token")

    def session(self, status=204, headers=None):
        response = AsyncMock()
        response.status = status
        response.headers = headers or {}
        request = AsyncMock()
        request.__aenter__.return_value = response
        session = AsyncMock()
        session.patch = Mock(return_value=request)
        factory = Mock()
        factory.return_value.__aenter__ = AsyncMock(return_value=session)
        factory.return_value.__aexit__ = AsyncMock()
        return factory, session

    async def test_v1_payload_contains_only_aggregate_count(self):
        factory, session = self.session()
        with patch("professor_compressor.topgg.ClientSession", factory):
            self.assertEqual(await self.stats.publish(), 60)
        session.patch.assert_called_once_with(
            METRICS_URL,
            headers={"Authorization": "Bearer private-test-token"},
            json={"server_count": 2},
            allow_redirects=False,
        )
        self.assertEqual(factory.call_args.kwargs["timeout"].total, 10)

    async def test_zero_servers_is_reported(self):
        self.client.guilds = []
        factory, session = self.session()
        with patch("professor_compressor.topgg.ClientSession", factory):
            await self.stats.publish()
        self.assertEqual(session.patch.call_args.kwargs["json"], {"server_count": 0})

    async def test_disabled_or_disconnected_never_calls_api(self):
        with patch("professor_compressor.topgg.ClientSession") as factory:
            self.stats.token = ""
            await self.stats.publish()
            await self.stats.run()
            self.stats.notify_change()
            self.assertFalse(self.stats.changed.is_set())
            self.stats.token = "token"
            self.client.is_ready.return_value = False
            await self.stats.publish()
            self.client.is_ready.return_value = True
            self.client.user = None
            await self.stats.publish()
        factory.assert_not_called()

    async def test_errors_do_not_raise_or_log_credentials(self):
        for status in (301, 401, 403, 500):
            factory, _ = self.session(status)
            with (
                patch("professor_compressor.topgg.ClientSession", factory),
                self.assertLogs("professor_compressor.topgg", level="WARNING") as logs,
            ):
                self.assertEqual(await self.stats.publish(), 3600)
            self.assertNotIn("private-test-token", str(logs.output))
        with (
            patch(
                "professor_compressor.topgg.ClientSession",
                side_effect=TimeoutError("private-test-token"),
            ),
            self.assertLogs("professor_compressor.topgg", level="WARNING") as logs,
        ):
            self.assertEqual(await self.stats.publish(), 3600)
        self.assertNotIn("private-test-token", str(logs.output))

    async def test_rate_limit_honors_retry_after(self):
        factory, _ = self.session(429, {"Retry-After": "7200"})
        with (
            patch("professor_compressor.topgg.ClientSession", factory),
            self.assertLogs("professor_compressor.topgg", level="WARNING"),
        ):
            self.assertEqual(await self.stats.publish(), 7200)
        for value in (None, "bad", "nan", "inf", "-20", "10"):
            self.assertEqual(retry_delay(value), 3600)
        date = format_datetime(datetime.now(UTC) + timedelta(hours=2))
        self.assertGreater(retry_delay(date), 7100)

    async def test_cancellation_propagates(self):
        with patch(
            "professor_compressor.topgg.ClientSession",
            side_effect=asyncio.CancelledError,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await self.stats.publish()

    async def test_changes_coalesce_and_cannot_bypass_backoff(self):
        delays = []

        async def sleep(delay):
            delays.append(delay)
            self.client.is_closed.return_value = True

        async def publish():
            self.stats.notify_change()
            self.stats.notify_change()
            return 7200

        with (
            patch.object(self.stats, "publish", AsyncMock(side_effect=publish)) as post,
            patch("professor_compressor.topgg.asyncio.sleep", side_effect=sleep),
        ):
            await self.stats.run()
        self.assertEqual(post.await_count, 1)
        self.assertGreater(delays[0], 7199)

    async def test_periodic_update_waits_for_readiness(self):
        async def wait_for(awaitable, timeout):
            self.assertEqual(timeout, 3600)
            awaitable.close()
            raise TimeoutError

        async def sleep(delay):
            self.client.is_closed.return_value = True

        with (
            patch.object(self.stats, "publish", AsyncMock(return_value=60)) as post,
            patch("professor_compressor.topgg.asyncio.wait_for", side_effect=wait_for),
            patch("professor_compressor.topgg.asyncio.sleep", side_effect=sleep),
        ):
            await self.stats.run()
        self.client.wait_until_ready.assert_awaited_once()
        post.assert_awaited_once()

    async def test_ready_reconnect_does_not_duplicate_worker(self):
        blocker = asyncio.Event()
        stats = Mock(token="test")
        stats.run = AsyncMock(side_effect=blocker.wait)
        tasks = set()
        with (
            patch.object(app, "topgg_stats", stats),
            patch.object(app, "topgg_stats_task", None),
            patch.object(app, "background_tasks", tasks),
            patch.object(app, "commands_synced", True),
            patch.object(app, "DSC_API_TOKEN", ""),
            patch.object(app, "start_upload_server", AsyncMock()),
        ):
            try:
                await app.on_ready()
                first = app.topgg_stats_task
                await app.on_ready()
                self.assertIs(app.topgg_stats_task, first)
                await asyncio.sleep(0)
                stats.run.assert_awaited_once()
            finally:
                for task in list(tasks):
                    task.cancel()
                await asyncio.gather(*list(tasks), return_exceptions=True)

    async def test_guild_events_signal_worker(self):
        guild = SimpleNamespace(id=123, name="Test", member_count=3)
        with (
            patch.object(app, "topgg_stats") as stats,
            patch.object(app, "schedule_owner_alert"),
            patch.object(app, "schedule_dsc_stats_update"),
        ):
            await app.on_guild_join(guild)
            await app.on_guild_remove(guild)
        self.assertEqual(stats.notify_change.call_count, 2)
