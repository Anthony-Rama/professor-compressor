import asyncio
import logging
import os
from collections.abc import Callable

import certifi
import discord
from aiohttp import web
from discord import app_commands

from .alerts import OwnerAlerts
from .assets import ASSET_PACKAGES, ASSET_PREFIX
from .background_tasks import BackgroundTasks
from .bot_delivery import DeliverResults, DeliveryService
from .bot_events import CommandSync, GuildEvents
from .browser_reports import ReportRoutes
from .config import MIB, Settings
from .dashboard import register_dashboard
from .discord_commands import Commands
from .dsc import DscStats
from .health import HealthService
from .lifecycle import BotLifecycle
from .metrics import RuntimeMetrics
from .observability import EventStore
from .paths import NODE_MODULES, STATIC
from .rate_limit import RateLimiter, request_ip
from .session_routes import SessionRoutes
from .sessions import SessionRegistry
from .topgg import TopGGStats
from .tracking import SessionTracker
from .upload_relay import RelayState
from .web_pages import browser_security_headers, register_public_pages


def run() -> None:
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    runtime_settings = Settings.from_environment(require_token=True)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(Runtime(runtime_settings).serve(runtime_settings.discord_token))


class Runtime:
    """Compose one process: Discord client, HTTP routes, relay, and diagnostics.

    Each service owns its own state; Runtime only builds them, wires their
    dependencies, and registers Discord events, commands, and HTTP routes.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        deliver_results: DeliverResults | None = None,
    ) -> None:
        self.settings = settings
        self.client = discord.Client(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self.client)
        self.metrics = RuntimeMetrics()
        self.event_store = EventStore(
            settings.observability_db_path,
            key=settings.observability_admin_key,
            retention_days=settings.observability_retention_days,
            max_events=settings.observability_max_events,
        )
        self.background_tasks = BackgroundTasks()
        self.relay = RelayState()
        self.tracker = SessionTracker(settings, self.event_store)
        self.alerts = OwnerAlerts(settings, self.background_tasks)
        self.rate_limiter = RateLimiter(settings, self.metrics)
        self.sessions = SessionRegistry(
            settings,
            tracker=self.tracker,
            alerts=self.alerts,
            rate_limiter=self.rate_limiter,
        )
        self.dsc_stats = DscStats(settings, self.client, self.background_tasks)
        self.topgg_stats = TopGGStats(
            self.client, settings.topgg_api_token, settings.topgg_stats_interval_seconds
        )
        self.delivery = DeliveryService(
            settings,
            client=self.client,
            relay=self.relay,
            sessions=self.sessions,
            tracker=self.tracker,
            alerts=self.alerts,
            metrics=self.metrics,
            deliver_results=deliver_results,
        )
        self.command_sync = CommandSync(settings, client=self.client, tree=self.tree)
        self.health = HealthService(
            settings,
            client=self.client,
            relay=self.relay,
            sessions=self.sessions,
            delivery=self.delivery,
            command_sync=self.command_sync,
            metrics=self.metrics,
        )
        self.session_routes = SessionRoutes(
            settings,
            sessions=self.sessions,
            rate_limiter=self.rate_limiter,
            tracker=self.tracker,
            alerts=self.alerts,
            metrics=self.metrics,
            relay=self.relay,
        )
        self.report_routes = ReportRoutes(
            settings,
            sessions=self.sessions,
            rate_limiter=self.rate_limiter,
            tracker=self.tracker,
            alerts=self.alerts,
            event_store=self.event_store,
        )
        self.commands = Commands(
            settings,
            client=self.client,
            sessions=self.sessions,
            tracker=self.tracker,
            alerts=self.alerts,
            metrics=self.metrics,
        )
        self.guild_events = GuildEvents(
            client=self.client,
            tracker=self.tracker,
            alerts=self.alerts,
            dsc_stats=self.dsc_stats,
            topgg_stats=self.topgg_stats,
        )
        self.lifecycle = BotLifecycle(
            settings,
            client=self.client,
            relay=self.relay,
            sessions=self.sessions,
            delivery=self.delivery,
            command_sync=self.command_sync,
            dsc_stats=self.dsc_stats,
            topgg_stats=self.topgg_stats,
            tasks=self.background_tasks,
            create_application=self.create_web_application,
        )
        # Entry points that tests/browser_server.py and the container smoke test
        # drive directly, without a Discord connection.
        self.jobs = self.sessions.jobs
        self.request_times = self.rate_limiter.request_times
        self.track = self.tracker.track
        self.delivery_worker = self.delivery.run_worker
        self.dashboard_runtime = self.health.dashboard_snapshot

        self.client.event(self.guild_events.on_guild_join)
        self.client.event(self.guild_events.on_guild_remove)
        self.client.event(self.lifecycle.on_ready)
        self.tree.command(
            name="compress",
            description="Privately compress up to 10 videos in your browser",
        )(self.compress)
        if settings.botstats_guild_id is not None:
            self.tree.command(
                name="botstats",
                description=(
                    "View private Professor Compressor server and usage statistics"
                ),
                guild=discord.Object(id=settings.botstats_guild_id),
            )(self.botstats)

    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    async def compress(self, interaction: discord.Interaction) -> None:
        await self.commands.compress(interaction)

    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def botstats(self, interaction: discord.Interaction) -> None:
        await self.commands.botstats(interaction)

    async def serve(self, token: str) -> None:
        await self.lifecycle.serve(token)

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
            for job in list(self.sessions.jobs.values()):
                if job.compression_started or job.relay_upload_started:
                    self.tracker.track(job, "interrupted")
            await asyncio.to_thread(self.event_store.stop)

        application.cleanup_ctx.append(diagnostic_lifecycle)
        register_dashboard(
            application,
            self.event_store,
            self.settings.observability_admin_key,
            request_ip,
            runtime_snapshot=runtime_snapshot or self.health.dashboard_snapshot,
        )
        register_public_pages(application, self.settings.public_base_url)
        application.router.add_get("/healthz", self.health.health)
        application.router.add_get("/readyz", self.health.readiness)
        application.router.add_get("/metricsz", self.health.aggregate_metrics)
        sessions = self.session_routes
        reports = self.report_routes
        application.router.add_get("/upload/{token}", sessions.upload_form)
        application.router.add_post("/upload/{token}", sessions.receive_results)
        application.router.add_get("/upload/{token}/status", sessions.session_status)
        application.router.add_post("/upload/{token}/heartbeat", sessions.renew_session)
        application.router.add_post("/upload/{token}/pause", sessions.pause_session)
        application.router.add_post(
            "/upload/{token}/progress", reports.receive_browser_progress
        )
        application.router.add_post(
            "/upload/{token}/telemetry", reports.receive_browser_telemetry
        )
        application.router.add_post(
            "/upload/{token}/events", reports.receive_diagnostic_events
        )
        application.router.add_post(
            "/upload/{token}/failure", reports.receive_browser_failure
        )
        application.router.add_static("/brand/", STATIC)
        for name, package in ASSET_PACKAGES.items():
            directory = NODE_MODULES / package / "dist/esm"
            application.router.add_static(f"{ASSET_PREFIX}/{name}/", directory)
            # Compatibility for pages opened before this release; never immutable.
            application.router.add_static(f"/assets/{name}/", directory)
        return application


def create_web_application() -> web.Application:
    """Build a fresh HTTP-only runtime for local use and packaged smoke tests."""
    return Runtime(
        Settings.from_environment(require_token=False)
    ).create_web_application()
