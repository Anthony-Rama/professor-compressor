"""Discord command workflows for session creation and operator status."""

import io
import math
import secrets
import time
from urllib.parse import quote

import discord

from .alerts import OwnerAlerts
from .branding import BOT_INVITE_URL
from .config import Settings
from .delivery_policy import bot_upload_limit, channel_delivery_problem
from .domain import JobState, UploadJob
from .metrics import RuntimeMetrics
from .notifications import (
    botstats_report,
    compression_progress_alert,
    identity_lines,
)
from .sessions import SessionRegistry
from .tracking import SessionTracker

PAGE_EXIT_GRACE_SECONDS = 5
# Longer status reports are attached as a file, below Discord's 2000 limit.
MAX_INLINE_REPORT_CHARACTERS = 1900


class Commands:
    """The /compress and /botstats command workflows for one runtime."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: discord.Client,
        sessions: SessionRegistry,
        tracker: SessionTracker,
        alerts: OwnerAlerts,
        metrics: RuntimeMetrics,
    ) -> None:
        self.settings = settings
        self.client = client
        self.sessions = sessions
        self.tracker = tracker
        self.alerts = alerts
        self.metrics = metrics
        self.application_operator_ids: frozenset[int] | None = None

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

    async def compress(self, interaction: discord.Interaction) -> None:
        if self.sessions.draining:
            await interaction.response.send_message(
                "Professor Compressor is finishing existing work before maintenance. "
                "Please try again shortly.",
                ephemeral=True,
            )
            return
        if (
            self.settings.allowed_guild_ids
            and interaction.guild_id not in self.settings.allowed_guild_ids
        ):
            await interaction.response.send_message(
                "Professor Compressor is not enabled in this server.",
                ephemeral=True,
            )
            return
        if interaction.channel_id is None:
            await interaction.response.send_message(
                "Use this command inside a server channel.",
                ephemeral=True,
            )
            return
        guild = (
            self.client.get_guild(interaction.guild_id)
            if interaction.guild_id is not None
            else None
        )
        if guild is None:
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(label="Add bot to server", url=BOT_INVITE_URL)
            )
            await interaction.response.send_message(
                "Professor Compressor must be installed in this server to send "
                "your videos back here. Ask a server admin or someone with Manage "
                "Server permission to add the bot to this server, then run "
                "/compress again.",
                view=view,
                ephemeral=True,
            )
            return
        problem = channel_delivery_problem(
            interaction.channel, interaction.app_permissions
        )
        if problem:
            await interaction.response.send_message(problem, ephemeral=True)
            return
        self.sessions.discard_expired_jobs()
        user_jobs = [
            job
            for job in self.sessions.jobs.values()
            if job.user_id == interaction.user.id
        ]
        if any(job.state in {JobState.UPLOADING, JobState.QUEUED} for job in user_jobs):
            await interaction.response.send_message(
                "Your existing session is sending files to Discord. Check the original "
                "channel for the result before starting another session. "
                "It has not been replaced.",
                ephemeral=True,
            )
            return
        now = time.time()
        waiting_jobs = [
            job
            for job in user_jobs
            if job.state is JobState.CLAIMED
            and job.processing_until > now
            and (
                job.page_left_at is None
                or now < job.page_left_at + PAGE_EXIT_GRACE_SECONDS
            )
        ]
        if waiting_jobs:
            cooldown_key = (interaction.guild_id or 0, interaction.user.id)
            cooldown_ends = (
                self.sessions.last_job_at.get(cooldown_key, 0.0)
                + self.settings.user_cooldown_seconds
            )
            wait_until = max(
                cooldown_ends,
                *(
                    min(
                        job.processing_until,
                        job.page_left_at + PAGE_EXIT_GRACE_SECONDS
                        if job.page_left_at is not None
                        else job.processing_until,
                    )
                    for job in waiting_jobs
                ),
            )
            seconds = max(1, math.ceil(wait_until - now))
            await interaction.response.send_message(
                "Your existing compressor session may still be active. "
                "Return to its tab "
                f"or wait {seconds} seconds and run /compress again. The current "
                "session has not been replaced.",
                ephemeral=True,
            )
            return
        cooldown_key = (interaction.guild_id or 0, interaction.user.id)
        previous_job_at = self.sessions.last_job_at.get(cooldown_key, 0.0)
        remaining = self.settings.user_cooldown_seconds - (now - previous_job_at)
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {max(1, int(remaining + 0.999))} seconds before "
                "creating another upload link.",
                ephemeral=True,
            )
            return
        if len(self.sessions.jobs) >= self.settings.max_active_jobs:
            await interaction.response.send_message(
                "Professor Compressor is handling too many upload sessions right "
                "now. Please try again in a few minutes.",
                ephemeral=True,
            )
            return

        # One active link per member. A new command invalidates their older link.
        for old_token, old_job in list(self.sessions.jobs.items()):
            if old_job.user_id == interaction.user.id:
                self.sessions.jobs.pop(old_token, None)
                self.tracker.track(old_job, "replaced")
                self.alerts.schedule(
                    compression_progress_alert(
                        old_job,
                        "🔄 **Compression session replaced**",
                        "A new /compress command replaced this unfinished link.",
                    )
                )
        self.sessions.last_job_at[cooldown_key] = now

        token = secrets.token_urlsafe(32)
        self.sessions.jobs[token] = UploadJob(
            token=token,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
            expires_at=time.time() + self.settings.job_ttl_seconds,
            discord_limit=min(
                bot_upload_limit(guild, self.settings.bot_base_upload_bytes),
                interaction.filesize_limit,
            ),
            interaction=interaction,
            guild_id=interaction.guild_id,
            guild_name=self.interaction_guild_name(interaction),
            username=interaction.user.name,
        )
        self.metrics.increment("sessions_created")
        self.tracker.track(
            self.sessions.jobs[token],
            "created",
            data={"discord_limit": self.sessions.jobs[token].discord_limit},
        )
        upload_url = f"{self.settings.public_base_url}/upload/{quote(token)}"
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Open compressor", url=upload_url))
        await interaction.response.send_message(
            f"Choose up to {self.settings.max_clips} videos. Keep the compressor "
            "page open until they are sent back here. Compression runs locally. "
            f"The link expires in {self.settings.job_ttl_seconds // 60} minutes.",
            view=view,
            ephemeral=True,
        )
        guild_name = self.interaction_guild_name(interaction)
        self.alerts.schedule(
            "\n".join(
                (
                    "⚙️ **Compression session created**",
                    *identity_lines(
                        guild_name=guild_name,
                        guild_id=interaction.guild_id,
                        username=interaction.user.name,
                        user_id=interaction.user.id,
                    ),
                    f"Connected servers: `{len(self.client.guilds)}`",
                    "Sessions since restart: "
                    f"`{self.metrics.snapshot()['sessions_created']}`",
                    f"Reference: `{self.sessions.jobs[token].diagnostic_reference}`",
                )
            )
        )

    async def botstats(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            operator_ids = await self.get_application_operator_ids()
        except discord.HTTPException:
            await interaction.followup.send(
                "Discord could not verify the application owner. Try again shortly.",
                ephemeral=True,
            )
            return
        if interaction.user.id not in operator_ids:
            await interaction.followup.send(
                "This command is restricted to the application owner.",
                ephemeral=True,
            )
            return

        report = botstats_report(self.client.guilds, self.metrics.snapshot())
        if len(report) <= MAX_INLINE_REPORT_CHARACTERS:
            await interaction.followup.send(report, ephemeral=True)
            return
        report_file = discord.File(
            io.BytesIO(report.encode("utf-8")),
            filename="professor-compressor-status.txt",
        )
        await interaction.followup.send(
            "The live server list is attached.",
            file=report_file,
            ephemeral=True,
        )
