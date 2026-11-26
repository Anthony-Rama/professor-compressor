"""Process startup, background loops, graceful drain, and shutdown."""

import asyncio
import logging
import signal
import time
from collections.abc import Callable

import discord
from aiohttp import web

from .background_tasks import BackgroundTasks
from .bot_delivery import DeliveryService
from .bot_events import CommandSync
from .config import Settings
from .domain import JobState
from .dsc import DscStats
from .sessions import SessionRegistry
from .topgg import TopGGStats
from .upload_relay import RelayState

# Log name is part of the operations contract; this code came from application.py.
logger = logging.getLogger("professor_compressor.application")

EXPIRY_SWEEP_SECONDS = 60
# Shutdown grace for in-flight work; compose allows 150 s before SIGKILL.
DRAIN_TIMEOUT_SECONDS = 120


class BotLifecycle:
    """Start HTTP and workers, react to gateway readiness, and drain on signals."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: discord.Client,
        relay: RelayState,
        sessions: SessionRegistry,
        delivery: DeliveryService,
        command_sync: CommandSync,
        dsc_stats: DscStats,
        topgg_stats: TopGGStats,
        tasks: BackgroundTasks,
        create_application: Callable[[], web.Application],
    ) -> None:
        self.settings = settings
        self.client = client
        self.relay = relay
        self.sessions = sessions
        self.delivery = delivery
        self.command_sync = command_sync
        self.dsc_stats = dsc_stats
        self.topgg_stats = topgg_stats
        self.tasks = tasks
        self.create_application = create_application
        self.web_runner: web.AppRunner | None = None
        self.cleanup_task: asyncio.Task[None] | None = None
        self.command_sync_task: asyncio.Task[None] | None = None
        self.dsc_stats_task: asyncio.Task[None] | None = None
        self.topgg_stats_task: asyncio.Task[None] | None = None

    async def start_upload_server(self) -> None:
        if self.web_runner is not None:
            return
        application = self.create_application()
        # Upload URLs contain one-use credentials; keep them out of access logs.
        self.web_runner = web.AppRunner(application, access_log=None)
        try:
            await self.web_runner.setup()
            site = web.TCPSite(
                self.web_runner, self.settings.web_host, self.settings.web_port
            )
            await site.start()
        except Exception:
            await self.web_runner.cleanup()
            self.web_runner = None
            raise
        self.relay.queue = asyncio.Queue(maxsize=self.settings.delivery_queue_size)
        for worker_number in range(1, self.settings.delivery_workers + 1):
            worker = self.tasks.spawn(self.delivery.run_worker(worker_number))
            self.delivery.workers.append(worker)
        self.cleanup_task = self.tasks.spawn(self.expire_jobs_loop())
        logger.info(
            "Private browser compressor listening at %s", self.settings.public_base_url
        )

    async def expire_jobs_loop(self) -> None:
        try:
            while not self.client.is_closed():
                self.sessions.discard_expired_jobs()
                await asyncio.sleep(EXPIRY_SWEEP_SECONDS)
        except asyncio.CancelledError:
            pass

    async def on_ready(self) -> None:
        await self.start_upload_server()
        if not self.command_sync.synced and (
            self.command_sync_task is None or self.command_sync_task.done()
        ):
            self.command_sync_task = self.tasks.spawn(
                self.command_sync.sync_with_retry()
            )
        if self.settings.dsc_api_token and (
            self.dsc_stats_task is None or self.dsc_stats_task.done()
        ):
            self.dsc_stats_task = self.tasks.spawn(self.dsc_stats.run())
        if self.topgg_stats.token and (
            self.topgg_stats_task is None or self.topgg_stats_task.done()
        ):
            self.topgg_stats_task = self.tasks.spawn(self.topgg_stats.run())
        logger.info(
            "Logged in as %s in %d server(s)", self.client.user, len(self.client.guilds)
        )

    async def drain_sessions(self, timeout: float = DRAIN_TIMEOUT_SECONDS) -> None:
        """Reject new work while allowing existing browser/relay work to finish."""
        self.sessions.draining = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = self.relay.active_uploads or any(
                job.state is JobState.QUEUED or job.processing_until > time.time()
                for job in self.sessions.jobs.values()
            )
            if not busy:
                return
            await asyncio.sleep(min(0.25, max(0, deadline - time.monotonic())))
        logger.warning("Shutdown grace period ended with unfinished sessions")

    async def serve(self, token: str) -> None:
        """Start HTTP independently of command sync, and drain on SIGTERM/SIGINT."""
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        gateway = None
        stopping = None
        try:
            async with self.client:
                await self.start_upload_server()
                gateway = asyncio.create_task(self.client.start(token))
                stopping = asyncio.create_task(stop.wait())
                done, _ = await asyncio.wait(
                    {gateway, stopping}, return_when=asyncio.FIRST_COMPLETED
                )
                if gateway in done:
                    await gateway
                else:
                    await self.drain_sessions()
                await self.client.close()
        finally:
            for task in (gateway, stopping, *self.tasks):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (gateway, stopping, *self.tasks)
                    if task is not None
                ),
                return_exceptions=True,
            )
            if self.web_runner is not None:
                await self.web_runner.cleanup()
                self.web_runner = None
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)
