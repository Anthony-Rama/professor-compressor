"""Discord channel delivery of relayed browser results."""

import asyncio
import io
import logging
from collections.abc import Awaitable, Callable

import discord

from ..config import Settings
from ..domain import BrowserResult, DeliveryRequest, JobState, UploadJob
from ..integrations.alerts import OwnerAlerts
from ..observability.metrics import RuntimeMetrics
from ..observability.tracking import SessionTracker
from ..relay.upload import RelayState
from ..relay.worker import run_delivery_worker
from ..sessions.registry import SessionRegistry
from .delivery_policy import (
    DeliveryFailure,
    bot_upload_limit,
    channel_delivery_problem,
    delivery_failure,
)

# Log name is part of the operations contract; this code came from application.py.
logger = logging.getLogger("professor_compressor.application")

DeliverResults = Callable[[UploadJob, list[BrowserResult], str], Awaitable[str]]
DeliveryChannel = discord.abc.GuildChannel | discord.Thread | discord.abc.PrivateChannel


class DeliveryService:
    """Send queued batches as new channel messages and settle their sessions."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: discord.Client,
        relay: RelayState,
        sessions: SessionRegistry,
        tracker: SessionTracker,
        alerts: OwnerAlerts,
        metrics: RuntimeMetrics,
        deliver_results: DeliverResults | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.relay = relay
        self.sessions = sessions
        self.tracker = tracker
        self.alerts = alerts
        self.metrics = metrics
        # Test harnesses replace Discord delivery; production leaves this unset.
        self._deliver_results = deliver_results
        self.workers: list[asyncio.Task[None]] = []

    def workers_ready(self) -> bool:
        """Every configured worker is running."""
        return len(self.workers) == self.settings.delivery_workers and all(
            not worker.done() for worker in self.workers
        )

    async def get_channel(self, job: UploadJob) -> DeliveryChannel | None:
        cached = self.client.get_channel(job.channel_id)
        if cached is not None:
            return cached
        try:
            return await self.client.fetch_channel(job.channel_id)
        except (discord.Forbidden, discord.NotFound):
            return None

    async def send_result_batch(
        self,
        sender: Callable[..., Awaitable[object]],
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
        channel: DeliveryChannel | None = None
        try:
            channel = await self.get_channel(job)
            clip_label = "Clip" if len(results) == 1 else "Clips"
            message = f"🎬 **{clip_label} from** <@{job.user_id}>"
            if caption:
                message += "\n\n> " + caption.replace("\n", "\n> ")
            if channel is None:
                raise DeliveryFailure(
                    "The bot cannot access the channel where compression started. "
                    "Ask a server admin to restore access, then run /compress again.",
                    "DELIVERY_ACCESS_DENIED",
                )
            guild = (
                self.client.get_guild(job.guild_id)
                if job.guild_id is not None
                else None
            )
            if guild is None or guild.me is None:
                raise DeliveryFailure(
                    "The bot is no longer a member of the destination server. "
                    "Ask a server admin to invite it again.",
                    "DELIVERY_ACCESS_DENIED",
                )
            # Channel capabilities are checked at runtime by channel_delivery_problem.
            permissions = channel.permissions_for(guild.me)  # type: ignore[union-attr]
            problem = channel_delivery_problem(channel, permissions)
            if problem:
                raise DeliveryFailure(problem, "DELIVERY_ACCESS_DENIED")
            if any(
                len(result.data)
                > bot_upload_limit(guild, self.settings.bot_base_upload_bytes)
                for result in results
            ):
                raise DeliveryFailure(
                    "The server's bot upload allowance changed. "
                    "Run /compress again to prepare smaller files.",
                    "DELIVERY_LIMIT_CHANGED",
                )
            send = channel.send  # type: ignore[union-attr]
            await self.send_result_batch(send, results, message)
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
        self.sessions.jobs.pop(delivery.job.token, None)

    async def run_worker(self, worker_number: int) -> None:
        assert self.relay.queue is not None
        await run_delivery_worker(
            worker_number,
            queue=self.relay.queue,
            send=self._deliver_results or self.deliver_browser_results,
            is_closed=self.client.is_closed,
            track=self.tracker.track,
            metrics=self.metrics,
            record_outcome=self.sessions.record_outcome,
            notify=self.alerts.schedule,
            finish=self.finish_delivery,
            batch_limit=min(
                self.settings.max_result_total_bytes,
                self.settings.max_delivery_buffer_bytes,
            ),
        )
