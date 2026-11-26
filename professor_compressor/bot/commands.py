"""Discord command workflows for session creation and operator status."""

import io
import math
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

import discord

from ..branding import BOT_INVITE_URL
from ..config import Settings
from ..domain import JobState, UploadJob
from ..integrations.alerts import OwnerAlerts
from ..observability.metrics import RuntimeMetrics
from ..observability.tracking import SessionTracker
from ..sessions.registry import SessionRegistry
from .delivery_policy import bot_upload_limit, channel_delivery_problem
from .messages import (
    botstats_report,
    compression_progress_alert,
    identity_lines,
)

PAGE_EXIT_GRACE_SECONDS = 5
# Longer status reports are attached as a file, below Discord's 2000 limit.
MAX_INLINE_REPORT_CHARACTERS = 1900


def cooldown_key(interaction: discord.Interaction) -> tuple[int, int]:
    return (interaction.guild_id or 0, interaction.user.id)


@dataclass(frozen=True, slots=True)
class Refusal:
    """An ephemeral reply explaining why /compress did not create a link."""

    message: str
    view: discord.ui.View | None = None

    async def send(self, interaction: discord.Interaction) -> None:
        if self.view is None:
            await interaction.response.send_message(self.message, ephemeral=True)
        else:
            await interaction.response.send_message(
                self.message, view=self.view, ephemeral=True
            )


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
        guild = (
            self.client.get_guild(interaction.guild_id)
            if interaction.guild_id is not None
            else None
        )
        refusal = self.channel_refusal(interaction, guild)
        if refusal is None:
            self.sessions.discard_expired_jobs()
            now = time.time()
            refusal = self.member_refusal(interaction, now)
        if refusal is not None:
            await refusal.send(interaction)
            return
        assert guild is not None
        await self.open_session(interaction, guild, now)

    def channel_refusal(
        self, interaction: discord.Interaction, guild: discord.Guild | None
    ) -> Refusal | None:
        """Checks on the service, server, and channel, in the order users see them."""
        if self.sessions.draining:
            return Refusal(
                "Professor Compressor is finishing existing work before maintenance. "
                "Please try again shortly."
            )
        if (
            self.settings.allowed_guild_ids
            and interaction.guild_id not in self.settings.allowed_guild_ids
        ):
            return Refusal("Professor Compressor is not enabled in this server.")
        if interaction.channel_id is None:
            return Refusal("Use this command inside a server channel.")
        if guild is None:
            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(label="Add bot to server", url=BOT_INVITE_URL)
            )
            return Refusal(
                "Professor Compressor must be installed in this server to send "
                "your videos back here. Ask a server admin or someone with Manage "
                "Server permission to add the bot to this server, then run "
                "/compress again.",
                view=view,
            )
        problem = channel_delivery_problem(
            interaction.channel, interaction.app_permissions
        )
        if problem:
            return Refusal(problem)
        return None

    def member_refusal(
        self, interaction: discord.Interaction, now: float
    ) -> Refusal | None:
        """Checks on the member's existing sessions, cooldown, and global capacity."""
        user_jobs = [
            job
            for job in self.sessions.jobs.values()
            if job.user_id == interaction.user.id
        ]
        if any(job.state in {JobState.UPLOADING, JobState.QUEUED} for job in user_jobs):
            return Refusal(
                "Your existing session is sending files to Discord. Check the original "
                "channel for the result before starting another session. "
                "It has not been replaced."
            )
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
            cooldown_ends = (
                self.sessions.last_job_at.get(cooldown_key(interaction), 0.0)
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
            return Refusal(
                "Your existing compressor session may still be active. "
                "Return to its tab "
                f"or wait {seconds} seconds and run /compress again. The current "
                "session has not been replaced."
            )
        previous_job_at = self.sessions.last_job_at.get(cooldown_key(interaction), 0.0)
        remaining = self.settings.user_cooldown_seconds - (now - previous_job_at)
        if remaining > 0:
            return Refusal(
                f"Please wait {max(1, int(remaining + 0.999))} seconds before "
                "creating another upload link."
            )
        if len(self.sessions.jobs) >= self.settings.max_active_jobs:
            return Refusal(
                "Professor Compressor is handling too many upload sessions right "
                "now. Please try again in a few minutes."
            )
        return None

    async def open_session(
        self, interaction: discord.Interaction, guild: discord.Guild, now: float
    ) -> None:
        """Replace the member's older link, create a new one, and send it."""
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
        self.sessions.last_job_at[cooldown_key(interaction)] = now

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
