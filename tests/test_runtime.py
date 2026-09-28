import asyncio
import signal
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord

from professor_compressor import application as app


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_command_sync_retries(self):
        response = Mock(status=503, reason="Unavailable")
        error = discord.HTTPException(response, {"code": 0, "message": "Temporary"})
        with (
            patch.object(app, "commands_synced", False),
            patch.object(app, "BOTSTATS_GUILD_ID", None),
            patch.object(app.tree, "sync", AsyncMock(side_effect=[error, []])) as sync,
            patch.object(app.asyncio, "sleep", AsyncMock()),
        ):
            await app.sync_commands_with_retry()
            self.assertEqual(sync.await_count, 2)
            self.assertTrue(app.commands_synced)

    async def test_startup_and_signal_shutdown_close_http_and_workers(self):
        signals = {}
        gateway_closed = asyncio.Event()
        test = self

        class Client:
            closed = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                await self.close()

            def is_closed(self):
                return self.closed

            async def start(self, token):
                test.assertEqual(token, "test-only")
                test.assertIsNotNone(app.web_runner)
                test.assertEqual(len(app.delivery_workers), app.DELIVERY_WORKERS)
                signals[signal.SIGTERM]()
                await gateway_closed.wait()

            async def close(self):
                self.closed = True
                gateway_closed.set()

        loop = asyncio.get_running_loop()
        with (
            patch.object(app, "client", Client()),
            patch.object(app, "WEB_HOST", "127.0.0.1"),
            patch.object(app, "WEB_PORT", 0),
            patch.object(app, "web_runner", None),
            patch.object(app, "jobs", {}),
            patch.object(app, "background_tasks", set()),
            patch.object(app, "delivery_workers", []),
            patch.object(app, "draining", False),
            patch.object(
                loop,
                "add_signal_handler",
                side_effect=lambda sig, fn: signals.update({sig: fn}),
            ),
            patch.object(loop, "remove_signal_handler"),
        ):
            await asyncio.wait_for(app.serve("test-only"), 3)
            self.assertIsNone(app.web_runner)
            self.assertTrue(app.client.is_closed())
            self.assertTrue(all(worker.done() for worker in app.delivery_workers))
