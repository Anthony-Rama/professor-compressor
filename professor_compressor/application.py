import asyncio
import io
import ipaddress
import logging
import math
import os
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

import certifi
import discord
from aiohttp import ClientSession, ClientTimeout, web
from discord import app_commands

from . import browser_reports, discord_commands, sessions
from .assets import ASSET_PACKAGES, ASSET_PREFIX
from .background_tasks import BackgroundTasks
from .branding import USER_AGENT
from .config import MIB, Settings
from .dashboard import register_dashboard
from .delivery_policy import (
    DeliveryFailure,
    bot_upload_limit,
    channel_delivery_problem,
    delivery_failure,
)
from .delivery_worker import run_delivery_worker
from .domain import (
    BrowserResult,
    DeliveryOutcome,
    DeliveryRequest,
    JobState,
    UploadJob,
)
from .metrics import RuntimeMetrics
from .notifications import (
    guild_alert_message,
)
from .observability import EventStore
from .paths import NODE_MODULES, STATIC
from .topgg import TopGGStats
from .upload_relay import RelayState, receive_results
from .web_pages import browser_security_headers, register_public_pages

logger = logging.getLogger(__name__)


def request_ip(request: web.Request) -> str:
    """Use Caddy's forwarded address only when the direct peer is private."""
    peer = request.remote or "unknown"
    try:
        trusted_proxy = ipaddress.ip_address(peer).is_private
    except ValueError:
        trusted_proxy = False
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    if trusted_proxy and forwarded:
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer


def run() -> None:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    runtime_settings = Settings.from_environment(require_token=True)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(Runtime(runtime_settings).serve(runtime_settings.discord_token))


class Runtime:
    """Own the process-local Discord, relay, and diagnostics state for one app."""

    def __init__(
        self,
        settings: Settings,
        *,
        deliver_results: Callable[[UploadJob, list[BrowserResult], str], Awaitable[str]]
        | None = None,
    ) -> None:
        self.settings = settings
        self.client = discord.Client(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self.client)
        self.topgg_stats = TopGGStats(
            self.client, settings.topgg_api_token, settings.topgg_stats_interval_seconds
        )
        self.jobs: dict[str, UploadJob] = {}
        self.outcomes: dict[str, DeliveryOutcome] = {}
        self.last_job_at: dict[tuple[int, int], float] = {}
        self.request_times: dict[tuple[str, str], deque[float]] = {}
        self.diagnostic_event_counts: dict[str, int] = {}
        self.relay = RelayState()
        self.delivery_workers: list[asyncio.Task[None]] = []
        self.background_tasks = BackgroundTasks()
        self.web_runner: web.AppRunner | None = None
        self.cleanup_task: asyncio.Task[None] | None = None
        self.command_sync_task: asyncio.Task[None] | None = None
        self.dsc_stats_task: asyncio.Task[None] | None = None
        self.topgg_stats_task: asyncio.Task[None] | None = None
        self.draining = False
        self.commands_synced = False
        self.application_operator_ids: frozenset[int] | None = None
        self.effective_allowed_guild_ids = settings.allowed_guild_ids
        self.metrics = RuntimeMetrics()
        self.event_store = EventStore(
            settings.observability_db_path,
            key=settings.observability_admin_key,
            retention_days=settings.observability_retention_days,
            max_events=settings.observability_max_events,
        )
        self._deliver_results = deliver_results
        self.client.event(self.on_guild_join)
        self.client.event(self.on_guild_remove)
        self.client.event(self.on_ready)
        self.tree.command(
            name="compress",
            description="Privately compress up to 10 videos in your browser",
        )(self.compress)
        if settings.botstats_guild_id is not None:
            self.tree.command(
                name="botstats",
                description="View private Professor Compressor server and usage statistics",
                guild=discord.Object(id=settings.botstats_guild_id),
            )(self.botstats)

    def track(
        self,
        job: UploadJob,
        event: str,
        *,
        data: object = None,
        run_id: str = "",
        file_index: int = 0,
        source: str = "server",
        event_id: str = "",
    ) -> None:
        """Optional diagnostics never participate in application correctness."""
        try:
            self.event_store.record(
                job.diagnostic_id,
                job.diagnostic_reference,
                event,
                run_id=run_id or job.active_run_id,
                file_index=file_index,
                source=source,
                data=data,
                revision=self.settings.app_revision,
                user_key=self.event_store.pseudonym("user", job.user_id),
                guild_key=self.event_store.pseudonym("guild", job.guild_id),
                is_test=job.guild_id in self.settings.diagnostic_test_guild_ids,
                event_id=event_id,
            )
        except Exception:
            logger.warning("Optional session diagnostic could not be recorded")

    async def send_owner_alert(self, message: str) -> None:
        """Send a best-effort private alert without affecting the user workflow."""
        if not self.settings.alert_webhook_url:
            return
        try:
            timeout = ClientTimeout(total=5)
            async with ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.settings.alert_webhook_url,
                    headers={"User-Agent": USER_AGENT},
                    json={
                        "content": message,
                        "allowed_mentions": {"parse": []},
                    },
                ) as response:
                    response.raise_for_status()
        except Exception as error:
            logger.warning("Owner notification failed (%s)", type(error).__name__)

    def schedule_owner_alert(self, message: str) -> None:
        """Run an alert in the background so Discord commands remain responsive."""
        if not self.settings.alert_webhook_url:
            return
        self.background_tasks.spawn(self.send_owner_alert(message))

    async def report_dsc_stats(self) -> bool:
        """Publish the current server count to dsc.sh without blocking bot work."""
        if not self.settings.dsc_api_token or self.client.user is None:
            return False
        try:
            timeout = ClientTimeout(total=10)
            async with ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"https://dsc.sh/api/bots/{self.client.user.id}/stats",
                    headers={
                        "Authorization": self.settings.dsc_api_token,
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                    json={"server_count": len(self.client.guilds)},
                ) as response:
                    response.raise_for_status()
            logger.info("Published dsc.sh server count: %d", len(self.client.guilds))
            return True
        except Exception as error:
            logger.warning("dsc.sh statistics update failed (%s)", type(error).__name__)
            return False

    def schedule_dsc_stats_update(self) -> None:
        """Schedule a best-effort dsc.sh update after a server-count change."""
        if not self.settings.dsc_api_token:
            return
        self.background_tasks.spawn(self.report_dsc_stats())

    async def dsc_stats_loop(self) -> None:
        """Refresh dsc.sh periodically so the public listing stays current."""
        while not self.client.is_closed():
            await self.report_dsc_stats()
            await asyncio.sleep(self.settings.dsc_stats_interval_seconds)

    def interaction_guild_name(self, interaction: discord.Interaction) -> str:
        """Return a usable guild name even when Discord sends partial guild data."""
        guild = interaction.guild
        if guild is None and interaction.guild_id is not None:
            guild = self.client.get_guild(interaction.guild_id)
        guild_name = getattr(guild, "name", "").strip()
        return guild_name or "Unknown server"

    async def get_application_operator_ids(self) -> frozenset[int]:
        """Return the application owner and team members allowed to view status."""
        if self.application_operator_ids is None:
            application = await self.client.application_info()
            operator_ids = {application.owner.id}
            if application.team is not None:
                operator_ids.update(member.id for member in application.team.members)
            self.application_operator_ids = frozenset(operator_ids)
        return self.application_operator_ids

    def active_job(self, token: str) -> UploadJob | None:
        return sessions.active_job(self, token)

    def expire_job(self, token: str, job: UploadJob) -> None:
        sessions.expire_job(self, token, job)

    def rate_limit_retry_after(
        self, request: web.Request, bucket: str, *, limit: int | None = None
    ) -> int | None:
        now = time.monotonic()
        key = (bucket, request_ip(request))
        timestamps = self.request_times.setdefault(key, deque())
        while timestamps and now - timestamps[0] >= 60:
            timestamps.popleft()
        if len(timestamps) >= (limit or self.settings.upload_rate_limit_per_minute):
            self.metrics.increment("rate_limited_requests")
            return max(1, int(60 - (now - timestamps[0])))
        timestamps.append(now)
        return None

    def discard_expired_jobs(self) -> None:
        sessions.discard_expired_jobs(self)

    async def expire_jobs_loop(self) -> None:
        try:
            while not self.client.is_closed():
                self.discard_expired_jobs()
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    async def health(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "status": "ok",
                "revision": self.settings.app_revision[:64],
                "discord_ready": self.client.is_ready(),
                "active_upload_links": len(self.jobs),
                "active_relay_uploads": self.relay.active_uploads,
                "delivery_queue_depth": self.relay.queue.qsize()
                if self.relay.queue
                else 0,
                "delivery_queue_capacity": self.settings.delivery_queue_size,
                "delivery_buffer_mib": round(self.relay.buffered_bytes / MIB, 1),
                "delivery_buffer_limit_mib": round(
                    self.settings.max_delivery_buffer_bytes / MIB, 1
                ),
            }
        )

    async def readiness(self, request: web.Request) -> web.Response:
        del request
        workers_ready = len(
            self.delivery_workers
        ) == self.settings.delivery_workers and all(
            not worker.done() for worker in self.delivery_workers
        )
        ready = (
            self.client.is_ready()
            and self.commands_synced
            and workers_ready
            and not self.draining
        )
        return web.json_response(
            {
                "status": "ready" if ready else "not_ready",
                "discord_ready": self.client.is_ready(),
                "workers_ready": workers_ready,
                "commands_synced": self.commands_synced,
                "draining": self.draining,
            },
            status=200 if ready else 503,
            headers={"Cache-Control": "no-store"},
        )

    async def aggregate_metrics(self, request: web.Request) -> web.Response:
        del request
        return web.json_response(
            {
                "privacy": "aggregate_process_counters_only",
                **self.metrics.snapshot(),
            },
            headers={"Cache-Control": "no-store"},
        )

    def record_outcome(
        self, job: UploadJob, status: int, body: dict[str, object]
    ) -> None:
        sessions.record_outcome(self, job, status, body)

    def session_access(
        self, request: web.Request
    ) -> tuple[UploadJob | None, web.Response | None]:
        return sessions.session_access(self, request)

    async def receive_results(self, request: web.Request) -> web.Response:
        return await receive_results(
            request,
            state=self.relay,
            limits=self.settings,
            authorize=self.session_access,
            rate_limit=self.rate_limit_retry_after,
            track=self.track,
            notify=self.schedule_owner_alert,
            metrics=self.metrics,
        )

    async def get_channel(self, job: UploadJob) -> discord.abc.Messageable | None:
        cached = self.client.get_channel(job.channel_id)
        if cached is not None:
            return cached
        try:
            return await self.client.fetch_channel(job.channel_id)
        except (discord.Forbidden, discord.NotFound):
            return None

    async def send_result_batch(
        self,
        sender: Any,
        results: list[BrowserResult],
        message: str,
    ) -> None:
        attachments = [
            discord.File(io.BytesIO(result.data), filename=result.name)
            for result in results
        ]
        try:
            await sender(
                content=message,
                files=attachments,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=True
                ),
            )
        finally:
            for attachment in attachments:
                attachment.close()

    async def deliver_browser_results(
        self,
        job: UploadJob,
        results: list[BrowserResult],
        caption: str = "",
    ) -> str:
        channel: discord.abc.Messageable | None = None
        try:
            channel = await self.get_channel(job)
            clip_label = "Clip" if len(results) == 1 else "Clips"
            message = f"🎬 **{clip_label} from** <@{job.user_id}>"
            if caption:
                message += "\n\n> " + caption.replace("\n", "\n> ")
            if channel is None:
                raise DeliveryFailure(
                    "The bot cannot access the channel where compression started. Ask a server admin to restore access, then run /compress again.",
                    "DELIVERY_ACCESS_DENIED",
                )
            guild = (
                self.client.get_guild(job.guild_id)
                if job.guild_id is not None
                else None
            )
            if guild is None or guild.me is None:
                raise DeliveryFailure(
                    "The bot is no longer a member of the destination server. Ask a server admin to invite it again.",
                    "DELIVERY_ACCESS_DENIED",
                )
            problem = channel_delivery_problem(
                channel, channel.permissions_for(guild.me)
            )
            if problem:
                raise DeliveryFailure(problem, "DELIVERY_ACCESS_DENIED")
            if any(
                len(result.data)
                > bot_upload_limit(guild, self.settings.bot_base_upload_bytes)
                for result in results
            ):
                raise DeliveryFailure(
                    "The server's bot upload allowance changed. Run /compress again to prepare smaller files.",
                    "DELIVERY_LIMIT_CHANGED",
                )
            await self.send_result_batch(channel.send, results, message)
            return "Your compressed videos were delivered to Discord."
        except Exception as error:
            logger.warning(
                "Result delivery failed (%s; HTTP=%s; Discord code=%s)",
                type(error).__name__,
                getattr(error, "status", None),
                getattr(error, "code", None),
            )
            if isinstance(error, DeliveryFailure):
                raise
            if not isinstance(error, (discord.Forbidden, discord.NotFound)) and (
                not isinstance(error, discord.HTTPException) or error.status >= 500
            ):
                raise delivery_failure(error) from error
            raise DeliveryFailure(
                "Discord rejected the files. Confirm that the bot can view the "
                "channel, send messages, and attach files, then run /compress again.",
                "DELIVERY_ACCESS_DENIED"
                if isinstance(error, (discord.Forbidden, discord.NotFound))
                else "DISCORD_REJECTED",
            ) from error

    def finish_delivery(self, delivery: DeliveryRequest) -> None:
        self.relay.release(sum(len(result.data) for result in delivery.results))
        delivery.job.state = JobState.DONE
        self.jobs.pop(delivery.job.token, None)

    async def delivery_worker(self, worker_number: int) -> None:
        assert self.relay.queue is not None
        await run_delivery_worker(
            worker_number,
            queue=self.relay.queue,
            send=self._deliver_results or self.deliver_browser_results,
            is_closed=self.client.is_closed,
            track=self.track,
            metrics=self.metrics,
            record_outcome=self.record_outcome,
            notify=self.schedule_owner_alert,
            finish=self.finish_delivery,
            batch_limit=min(
                self.settings.max_result_total_bytes,
                self.settings.max_delivery_buffer_bytes,
            ),
        )

    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    async def compress(self, interaction: discord.Interaction) -> None:
        await discord_commands.compress(self, interaction)

    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def botstats(self, interaction: discord.Interaction) -> None:
        await discord_commands.botstats(self, interaction)

    def dashboard_runtime(self) -> dict:
        """Cached Discord/process facts; no API requests or private identifiers."""
        guilds = self.client.guilds
        connected = self.client.is_ready()
        workers_ready = len(
            self.delivery_workers
        ) == self.settings.delivery_workers and all(
            not worker.done() for worker in self.delivery_workers
        )
        latency = self.client.latency
        return {
            "connected_servers": len(guilds) if connected or guilds else None,
            "server_count_current": connected,
            "unavailable_servers": sum(bool(guild.unavailable) for guild in guilds),
            "discord_ready": connected,
            "ready": connected
            and self.commands_synced
            and workers_ready
            and not self.draining,
            "commands_synced": self.commands_synced,
            "draining": self.draining,
            "gateway_latency_ms": round(latency * 1000)
            if math.isfinite(latency)
            else None,
            "revision": self.settings.app_revision[:64],
            "active_sessions": len(self.jobs),
            "active_uploads": self.relay.active_uploads,
            "queue_depth": self.relay.queue.qsize() if self.relay.queue else 0,
            "queue_capacity": self.settings.delivery_queue_size,
            "delivery_workers": sum(
                not worker.done() for worker in self.delivery_workers
            ),
            "worker_capacity": self.settings.delivery_workers,
            "buffer_bytes": self.relay.buffered_bytes,
            "buffer_limit_bytes": self.settings.max_delivery_buffer_bytes,
            "counters": self.metrics.snapshot(),
        }

    def create_web_application(
        self, *, runtime_snapshot: Callable[[], dict] | None = None
    ) -> web.Application:
        """Build the HTTP application so routes can be smoke-tested independently."""
        application = web.Application(
            client_max_size=self.settings.max_result_total_bytes + 2 * MIB,
            middlewares=[browser_security_headers],
        )

        async def diagnostic_lifecycle(_application):
            self.event_store.start()
            yield
            for job in list(self.jobs.values()):
                if job.compression_started or job.relay_upload_started:
                    self.track(job, "interrupted")
            await asyncio.to_thread(self.event_store.stop)

        application.cleanup_ctx.append(diagnostic_lifecycle)
        register_dashboard(
            application,
            self.event_store,
            self.settings.observability_admin_key,
            request_ip,
            runtime_snapshot=runtime_snapshot or self.dashboard_runtime,
        )
        register_public_pages(application, self.settings.public_base_url)
        application.router.add_get("/healthz", self.health)
        application.router.add_get("/readyz", self.readiness)
        application.router.add_get("/metricsz", self.aggregate_metrics)
        application.router.add_get(
            "/upload/{token}", partial(sessions.upload_form, self)
        )
        application.router.add_post("/upload/{token}", self.receive_results)
        application.router.add_get(
            "/upload/{token}/status", partial(sessions.session_status, self)
        )
        application.router.add_post(
            "/upload/{token}/heartbeat", partial(sessions.renew_session, self)
        )
        application.router.add_post(
            "/upload/{token}/pause", partial(sessions.pause_session, self)
        )
        application.router.add_post(
            "/upload/{token}/progress",
            partial(browser_reports.receive_browser_progress, self),
        )
        application.router.add_post(
            "/upload/{token}/telemetry",
            partial(browser_reports.receive_browser_telemetry, self),
        )
        application.router.add_post(
            "/upload/{token}/events",
            partial(browser_reports.receive_diagnostic_events, self),
        )
        application.router.add_post(
            "/upload/{token}/failure",
            partial(browser_reports.receive_browser_failure, self),
        )
        application.router.add_static("/brand/", STATIC)
        for name, package in ASSET_PACKAGES.items():
            directory = NODE_MODULES / package / "dist/esm"
            application.router.add_static(f"{ASSET_PREFIX}/{name}/", directory)
            # Compatibility for pages opened before this release; never immutable.
            application.router.add_static(f"/assets/{name}/", directory)
        return application

    async def start_upload_server(self) -> None:
        if self.web_runner is not None:
            return
        application = self.create_web_application()
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
            worker = self.background_tasks.spawn(self.delivery_worker(worker_number))
            self.delivery_workers.append(worker)
        self.cleanup_task = self.background_tasks.spawn(self.expire_jobs_loop())
        logger.info(
            "Private browser compressor listening at %s", self.settings.public_base_url
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.track(
            UploadJob("", 0, 0, time.time(), 0, None, guild_id=guild.id),
            "installed",
            data={"connected_servers": len(self.client.guilds)},
        )
        self.schedule_owner_alert(
            guild_alert_message(
                "🟢 **Professor Compressor installed**",
                guild,
                server_count=len(self.client.guilds),
            )
        )
        self.schedule_dsc_stats_update()
        self.topgg_stats.notify_change()

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.track(
            UploadJob("", 0, 0, time.time(), 0, None, guild_id=guild.id),
            "removed",
            data={"connected_servers": len(self.client.guilds)},
        )
        self.schedule_owner_alert(
            guild_alert_message(
                "🔴 **Professor Compressor removed**",
                guild,
                server_count=len(self.client.guilds),
            )
        )
        self.schedule_dsc_stats_update()
        self.topgg_stats.notify_change()

    async def sync_commands(self) -> None:
        if not self.commands_synced:
            synced_commands = await self.tree.sync()
            logger.info(
                "Synced global commands: "
                + ", ".join(command.name for command in synced_commands)
            )
            if self.settings.botstats_guild_id is not None:
                owner_guild = discord.Object(id=self.settings.botstats_guild_id)
                owner_commands = await self.tree.sync(guild=owner_guild)
                logger.info(
                    f"Synced private commands to server {self.settings.botstats_guild_id}: "
                    + ", ".join(command.name for command in owner_commands)
                )
            if not self.client.guilds:
                logger.warning(
                    "Warning: the bot is not installed as a member of any server. "
                    "Install it to your server so long compression jobs can fall "
                    "back to normal channel messages after interactions expire."
                )
            if self.effective_allowed_guild_ids:
                logger.info(
                    "Commands restricted to Discord servers "
                    + ", ".join(
                        str(guild_id)
                        for guild_id in sorted(self.effective_allowed_guild_ids)
                    )
                )
            else:
                logger.info("Commands are available in all Discord servers.")
            self.commands_synced = True

    async def sync_commands_with_retry(self) -> None:
        while not self.client.is_closed() and not self.commands_synced:
            try:
                await self.sync_commands()
            except discord.HTTPException as error:
                logger.warning(
                    "Command sync failed (HTTP=%s; Discord code=%s); retrying",
                    error.status,
                    error.code,
                )
                await asyncio.sleep(30)

    async def on_ready(self) -> None:
        await self.start_upload_server()
        if not self.commands_synced and (
            self.command_sync_task is None or self.command_sync_task.done()
        ):
            self.command_sync_task = self.background_tasks.spawn(
                self.sync_commands_with_retry()
            )
        if self.settings.dsc_api_token and (
            self.dsc_stats_task is None or self.dsc_stats_task.done()
        ):
            self.dsc_stats_task = self.background_tasks.spawn(self.dsc_stats_loop())
        if self.topgg_stats.token and (
            self.topgg_stats_task is None or self.topgg_stats_task.done()
        ):
            self.topgg_stats_task = self.background_tasks.spawn(self.topgg_stats.run())
        logger.info(
            "Logged in as %s in %d server(s)", self.client.user, len(self.client.guilds)
        )

    async def drain_sessions(self, timeout: float = 120) -> None:
        """Reject new work while allowing existing browser/relay work to finish."""
        self.draining = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = self.relay.active_uploads or any(
                job.state is JobState.QUEUED or job.processing_until > time.time()
                for job in self.jobs.values()
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
            for task in (gateway, stopping, *self.background_tasks):
                if task is not None:
                    task.cancel()
            await asyncio.gather(
                *(
                    task
                    for task in (gateway, stopping, *self.background_tasks)
                    if task is not None
                ),
                return_exceptions=True,
            )
            if self.web_runner is not None:
                await self.web_runner.cleanup()
                self.web_runner = None
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.remove_signal_handler(sig)


def create_web_application() -> web.Application:
    """Build a fresh HTTP-only runtime for local use and packaged smoke tests."""
    return Runtime(
        Settings.from_environment(require_token=False)
    ).create_web_application()
