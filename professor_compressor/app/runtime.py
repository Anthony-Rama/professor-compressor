"""Composition root: builds the runtime services and wires them together."""

import asyncio
import logging
import os
from collections.abc import Callable

import certifi
import discord
from aiohttp import web
from discord import app_commands

from ..bot.commands import Commands
from ..bot.delivery import DeliverResults, DeliveryService
from ..bot.events import CommandSync, GuildEvents
from ..config import Settings
from ..integrations.alerts import OwnerAlerts
from ..integrations.dsc import DscStats
from ..integrations.topgg import TopGGStats
from ..observability.metrics import RuntimeMetrics
from ..observability.store import EventStore
from ..observability.tracking import SessionTracker
from ..relay.upload import RelayState
from ..sessions.registry import SessionRegistry
from ..web.app import build_web_application
from ..web.health import HealthService
from ..web.rate_limit import RateLimiter
from ..web.report_routes import ReportRoutes
from ..web.session_routes import SessionRoutes
from .lifecycle import BotLifecycle
from .tasks import BackgroundTasks


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
        # Entry points that tests/harness/browser_server.py and the container smoke test
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
        return build_web_application(
            self.settings,
            event_store=self.event_store,
            sessions=self.sessions,
            tracker=self.tracker,
            health=self.health,
            session_routes=self.session_routes,
            report_routes=self.report_routes,
            runtime_snapshot=runtime_snapshot or self.health.dashboard_snapshot,
        )


def create_web_application() -> web.Application:
    """Build a fresh HTTP-only runtime for local use and packaged smoke tests."""
    return Runtime(
        Settings.from_environment(require_token=False)
    ).create_web_application()
