"""Discord gateway events: guild membership changes and command registration."""

import asyncio
import logging
import time

import discord
from discord import app_commands

from .alerts import OwnerAlerts
from .config import Settings
from .domain import UploadJob
from .dsc import DscStats
from .notifications import guild_alert_message
from .topgg import TopGGStats
from .tracking import SessionTracker

# Log name is part of the operations contract; this code came from application.py.
logger = logging.getLogger("professor_compressor.application")

COMMAND_SYNC_RETRY_SECONDS = 30


class CommandSync:
    """Register slash commands once per process, retrying transient failures."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: discord.Client,
        tree: app_commands.CommandTree,
    ) -> None:
        self.settings = settings
        self.client = client
        self.tree = tree
        self.synced = False

    async def sync(self) -> None:
        if not self.synced:
            synced_commands = await self.tree.sync()
            logger.info(
                "Synced global commands: "
                + ", ".join(command.name for command in synced_commands)
            )
            if self.settings.botstats_guild_id is not None:
                owner_guild = discord.Object(id=self.settings.botstats_guild_id)
                owner_commands = await self.tree.sync(guild=owner_guild)
                logger.info(
                    "Synced private commands to server "
                    f"{self.settings.botstats_guild_id}: "
                    + ", ".join(command.name for command in owner_commands)
                )
            if not self.client.guilds:
                logger.warning(
                    "Warning: the bot is not installed as a member of any server. "
                    "Install it to your server so long compression jobs can fall "
                    "back to normal channel messages after interactions expire."
                )
            if self.settings.allowed_guild_ids:
                logger.info(
                    "Commands restricted to Discord servers "
                    + ", ".join(
                        str(guild_id)
                        for guild_id in sorted(self.settings.allowed_guild_ids)
                    )
                )
            else:
                logger.info("Commands are available in all Discord servers.")
            self.synced = True

    async def sync_with_retry(self) -> None:
        while not self.client.is_closed() and not self.synced:
            try:
                await self.sync()
            except discord.HTTPException as error:
                logger.warning(
                    "Command sync failed (HTTP=%s; Discord code=%s); retrying",
                    error.status,
                    error.code,
                )
                await asyncio.sleep(COMMAND_SYNC_RETRY_SECONDS)


class GuildEvents:
    """Record installs and removals, alert the operator, and refresh listings."""

    def __init__(
        self,
        *,
        client: discord.Client,
        tracker: SessionTracker,
        alerts: OwnerAlerts,
        dsc_stats: DscStats,
        topgg_stats: TopGGStats,
    ) -> None:
        self.client = client
        self.tracker = tracker
        self.alerts = alerts
        self.dsc_stats = dsc_stats
        self.topgg_stats = topgg_stats

    async def on_guild_join(self, guild: discord.Guild) -> None:
        self._membership_changed(
            guild, "installed", "🟢 **Professor Compressor installed**"
        )

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self._membership_changed(
            guild, "removed", "🔴 **Professor Compressor removed**"
        )

    def _membership_changed(self, guild: discord.Guild, event: str, title: str) -> None:
        self.tracker.track(
            UploadJob("", 0, 0, time.time(), 0, None, guild_id=guild.id),
            event,
            data={"connected_servers": len(self.client.guilds)},
        )
        self.alerts.schedule(
            guild_alert_message(title, guild, server_count=len(self.client.guilds))
        )
        self.dsc_stats.schedule_update()
        self.topgg_stats.notify_change()
