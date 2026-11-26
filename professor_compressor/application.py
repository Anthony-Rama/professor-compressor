import asyncio
import io
import ipaddress
import json
import logging
import math
import os
import secrets
import signal
import time
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

import certifi
import discord
from aiohttp import ClientSession, ClientTimeout, web
from discord import app_commands

from .assets import ASSET_PACKAGES, ASSET_PREFIX, NODE_MODULES
from .config import MIB, Settings
from .dashboard import register_dashboard
from .delivery_policy import (
    DeliveryFailure,
    bot_upload_limit,
    channel_delivery_problem,
    delivery_failure,
)
from .domain import (
    BrowserResult,
    CompressionDiagnostic,
    DeliveryOutcome,
    DeliveryRequest,
    JobState,
    UploadJob,
)
from .landing_pages import BOT_INVITE_URL
from .media_validation import valid_mp4_signature
from .metrics import RuntimeMetrics
from .notifications import (
    botstats_report,
    compression_outcome_alert,
    compression_progress_alert,
    guild_alert_message,
    safe_alert_text,
)
from .observability import EventStore
from .observability_contract import CLIENT_EVENTS, sanitize, valid_id
from .relay_diagnostics import parse_compression_diagnostics, sanitize_telemetry
from .topgg import TopGGStats
from .web_pages import browser_security_headers, page, register_public_pages
from .web_ui import browser_compressor, browser_delivery_status

logger = logging.getLogger(__name__)

OUTCOME_TTL_SECONDS = 3600
RESUME_COOKIE_NAME = "__Secure-pc-browser"
RESUME_COOKIE_MAX_AGE_SECONDS = 3 * 3600
PAGE_EXIT_GRACE_SECONDS = 5
DELIVERY_RESPONSE_WAIT_SECONDS = 2


def compression_target(discord_limit: int) -> int:
    """Reserve two percent for delivery while preserving output quality."""
    return max(1 * MIB, int(discord_limit * 0.98))


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


def json_error(message: str, status: int, code: str, **extra: Any) -> web.Response:
    return web.json_response(
        {"ok": False, "error": message, "code": code, **extra},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def browser_cookie_matches(request: web.Request, secret: str | None) -> bool:
    supplied = request.cookies.get(RESUME_COOKIE_NAME, "")
    return bool(
        secret
        and supplied
        and secrets.compare_digest(supplied.encode(), secret.encode())
    )


def set_browser_cookie(response: web.Response, token: str, secret: str) -> None:
    response.set_cookie(
        RESUME_COOKIE_NAME,
        secret,
        max_age=RESUME_COOKIE_MAX_AGE_SECONDS,
        path=f"/upload/{quote(token)}",
        secure=True,
        httponly=True,
        samesite="Lax",
    )


def safe_result_name(name: str | None, number: int) -> str:
    raw = (name or f"clip-{number}.mp4").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character for character in raw if character.isalnum() or character in " ._-"
    ).strip(" .")
    stem = cleaned[:-4] if cleaned.lower().endswith(".mp4") else cleaned
    stem = stem[:120].rstrip(" .")
    return f"{stem or f'clip-{number}'}.mp4"


def pending_response() -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "pending": True,
            "retry_after": 2,
            "message": "Delivery is still being processed. Do not start another session.",
        },
        status=202,
        headers={"Cache-Control": "no-store"},
    )


def delivery_timing(delivery: DeliveryRequest, started: float) -> dict:
    return {
        "relay_receive_seconds": round(delivery.received_seconds, 3),
        "queue_seconds": round(max(0, started - delivery.queued_at), 3),
        "discord_delivery_seconds": round(max(0, time.monotonic() - started), 3),
    }


def log_delivery_diagnostics(
    delivery: DeliveryRequest, started: float, outcome: str
) -> None:
    files = []
    for index, diagnostic in enumerate(delivery.diagnostics):
        item = {
            "file": index + 1,
            **diagnostic.telemetry,
            "encoding_seconds": diagnostic.encoding_seconds,
            "duration_seconds": diagnostic.duration_seconds,
            "input_bytes": diagnostic.input_bytes,
            "output_bytes": len(delivery.results[index].data),
            "input_format": diagnostic.input_format,
            "video_kbps": diagnostic.video_kbps,
        }
        if diagnostic.encoding_seconds and diagnostic.duration_seconds:
            speed = diagnostic.duration_seconds / diagnostic.encoding_seconds
            if math.isfinite(speed):
                item["encoding_speed"] = round(speed, 3)
        files.append(item)
    logger.info(
        "Processing diagnostics %s",
        json.dumps(
            {
                "outcome": outcome,
                "diagnostic_id": delivery.job.diagnostic_id,
                "reference": delivery.job.diagnostic_reference,
                "run_id": delivery.run_id,
                "files": files,
                **delivery_timing(delivery, started),
            },
            allow_nan=False,
        ),
    )


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
        self.delivery_queue: asyncio.Queue[DeliveryRequest] | None = None
        self.delivery_workers: list[asyncio.Task[None]] = []
        self.background_tasks: set[asyncio.Task[Any]] = set()
        self.web_runner: web.AppRunner | None = None
        self.cleanup_task: asyncio.Task[None] | None = None
        self.command_sync_task: asyncio.Task[None] | None = None
        self.dsc_stats_task: asyncio.Task[None] | None = None
        self.topgg_stats_task: asyncio.Task[None] | None = None
        self.active_relay_uploads = 0
        self.delivery_bytes_held = 0
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
                revision=os.getenv("APP_REVISION", "development"),
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
                    headers={
                        "User-Agent": (
                            "ProfessorCompressor/1.0 "
                            "(+https://github.com/Anthony-Rama/professor-compressor)"
                        )
                    },
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
        task = asyncio.create_task(self.send_owner_alert(message))
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

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
                        "User-Agent": (
                            "ProfessorCompressor/1.0 "
                            "(+https://github.com/Anthony-Rama/professor-compressor)"
                        ),
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
        task = asyncio.create_task(self.report_dsc_stats())
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

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
        job = self.jobs.get(token)
        if (
            job is not None
            and time.time() > job.expires_at
            and job.state in {JobState.OPEN, JobState.CLAIMED}
        ):
            self.expire_job(token, job)
            return None
        if job is None or job.state is JobState.DONE:
            self.jobs.pop(token, None)
            return None
        return job

    def expire_job(self, token: str, job: UploadJob) -> None:
        """Retire an unfinished session once, with its last confirmed activity."""
        if self.jobs.pop(token, None) is not job:
            return
        self.track(job, "expired")
        if job.browser_failure_reported:
            return
        if job.state is JobState.OPEN:
            detail = "Last confirmed activity: link created; it was never opened."
        elif job.page_left:
            detail = "Last confirmed activity: browser reported the page was left or reloaded."
        elif job.browser_cancelled:
            detail = (
                "Last confirmed activity: browser reported compression was cancelled."
            )
        elif job.relay_upload_started:
            detail = "Last confirmed activity: relay upload was attempted; no valid completed batch reached the delivery queue."
        elif job.compression_started:
            detail = "Last confirmed activity: browser reported Compress was pressed; no finished files reached the relay."
        elif job.selected_count:
            detail = f"Last confirmed activity: browser reported {job.selected_count} file(s) selected; Compress was not reported."
        else:
            detail = (
                "Last confirmed activity: link opened; no file selection was reported."
            )
        self.schedule_owner_alert(
            compression_progress_alert(
                job, "⌛ **Compression session expired without delivery**", detail
            )
        )

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
        now = time.time()
        expired_tokens = [
            token
            for token, job in self.jobs.items()
            if job.state is JobState.DONE
            or (now > job.expires_at and job.state in {JobState.OPEN, JobState.CLAIMED})
        ]
        for token in expired_tokens:
            job = self.jobs.get(token)
            if job is not None:
                if job.state is JobState.DONE:
                    self.jobs.pop(token, None)
                else:
                    self.expire_job(token, job)
        for token, outcome in list(self.outcomes.items()):
            if now > outcome.expires_at:
                self.outcomes.pop(token, None)
        retained_ids = {job.diagnostic_id for job in self.jobs.values()} | {
            outcome.diagnostic_id for outcome in self.outcomes.values()
        }
        for diagnostic_id in list(self.diagnostic_event_counts):
            if diagnostic_id not in retained_ids:
                self.diagnostic_event_counts.pop(diagnostic_id, None)

        stale_cooldowns = [
            key
            for key, started_at in self.last_job_at.items()
            if now - started_at > max(self.settings.user_cooldown_seconds * 4, 300)
        ]
        for key in stale_cooldowns:
            self.last_job_at.pop(key, None)

        monotonic_now = time.monotonic()
        for values in self.request_times.values():
            while values and monotonic_now - values[0] >= 60:
                values.popleft()
        stale_rate_keys = [
            key for key, values in self.request_times.items() if not values
        ]
        for key in stale_rate_keys:
            self.request_times.pop(key, None)

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
                "revision": os.getenv("APP_REVISION", "development")[:64],
                "discord_ready": self.client.is_ready(),
                "active_upload_links": len(self.jobs),
                "active_relay_uploads": self.active_relay_uploads,
                "delivery_queue_depth": self.delivery_queue.qsize()
                if self.delivery_queue
                else 0,
                "delivery_queue_capacity": self.settings.delivery_queue_size,
                "delivery_buffer_mib": round(self.delivery_bytes_held / MIB, 1),
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

    def compressor_page(
        self, job: UploadJob, *, reopened: bool = False
    ) -> web.Response:
        safe_target = compression_target(job.discord_limit)
        return page(
            "Professor Compressor",
            browser_compressor(
                self.settings.max_clips,
                safe_target,
                min(
                    self.settings.max_result_total_bytes,
                    self.settings.max_delivery_buffer_bytes,
                ),
                job.claim_secret or "",
                max(1, int(job.expires_at - time.time())),
                discord_limit_bytes=job.discord_limit,
                reopened=reopened,
                diagnostic_reference=job.diagnostic_reference,
                diagnostic_events_enabled=bool(self.settings.observability_db_path),
            ),
        )

    async def upload_form(self, request: web.Request) -> web.Response:
        # aiohttp also dispatches HEAD to GET handlers; inspection must not claim a link.
        if request.method == "HEAD":
            return web.Response(headers={"Cache-Control": "no-store"})
        if self.draining:
            return page(
                "Maintenance",
                "<h1>Brief maintenance</h1><p>Please try your link again shortly.</p>",
            )
        retry_after = self.rate_limit_retry_after(request, "open")
        if retry_after is not None:
            return page(
                "Please wait",
                "<h1>Too many requests</h1>"
                f"<p>Please wait {retry_after} seconds and reopen this link. "
                "If it has expired, run <strong>/compress</strong> again.</p>",
            )
        token = request.match_info["token"]
        job = self.active_job(token)
        receipt = self.outcomes.get(token)
        if receipt is not None and time.time() > receipt.expires_at:
            self.outcomes.pop(token, None)
            receipt = None
        if job is not None and job.state is JobState.OPEN:
            job.claim_secret = secrets.token_urlsafe(32)
            job.browser_secret = secrets.token_urlsafe(32)
            job.claimed_at = time.time()
            job.expires_at = time.time() + self.settings.active_session_ttl_seconds
            job.state = JobState.CLAIMED
            self.track(job, "opened")
            self.metrics.increment("sessions_opened")
            self.schedule_owner_alert(
                compression_progress_alert(
                    job,
                    "🔗 **Compressor link opened**",
                    "Stage: browser requested the page; no files selected yet.",
                )
            )
            response = self.compressor_page(job)
            set_browser_cookie(response, token, job.browser_secret)
            return response
        if job is not None and browser_cookie_matches(request, job.browser_secret):
            if job.state is JobState.CLAIMED:
                self.track(job, "refreshed")
                # A fresh page supersedes any old tab holding the previous API secret.
                job.claim_secret = secrets.token_urlsafe(32)
                job.page_left = False
                job.page_left_at = None
                job.processing_until = 0
                hard_deadline = (job.claimed_at or job.created_at) + max(
                    7200, self.settings.active_session_ttl_seconds
                )
                job.expires_at = min(
                    time.time() + self.settings.active_session_ttl_seconds,
                    hard_deadline,
                )
                return self.compressor_page(job, reopened=True)
            return page(
                "Delivery status",
                browser_delivery_status(
                    job.claim_secret or "",
                    diagnostic_reference=job.diagnostic_reference,
                ),
            )
        if receipt is not None and browser_cookie_matches(
            request, receipt.browser_secret
        ):
            return page(
                "Delivery status",
                browser_delivery_status(
                    receipt.secret,
                    diagnostic_reference="PC-" + receipt.diagnostic_id[:12].upper()
                    if receipt.diagnostic_id
                    else "",
                ),
            )
        return page(
            "Private link unavailable",
            "<h1>Private link unavailable</h1>"
            "<p>This link has expired or cannot be reopened in this browser. "
            "If you switched browsers or cleared site data, run "
            "<strong>/compress</strong> in Discord for a new link.</p>",
        )

    def record_outcome(
        self, job: UploadJob, status: int, body: dict[str, object]
    ) -> None:
        self.outcomes[job.token] = DeliveryOutcome(
            secret=job.claim_secret or "",
            browser_secret=job.browser_secret or "",
            expires_at=time.time() + OUTCOME_TTL_SECONDS,
            status=status,
            body=body,
            diagnostic_id=job.diagnostic_id,
            run_id=job.active_run_id,
            is_test=job.guild_id in self.settings.diagnostic_test_guild_ids,
        )
        # Bound receipt memory independently from active sessions. No media is retained.
        while len(self.outcomes) > max(1000, self.settings.max_active_jobs * 4):
            self.outcomes.pop(next(iter(self.outcomes)))

    def session_access(
        self,
        request: web.Request,
    ) -> tuple[UploadJob | None, web.Response | None]:
        token = request.match_info["token"]
        job = self.active_job(token)
        receipt = self.outcomes.get(token)
        if receipt and time.time() > receipt.expires_at:
            self.outcomes.pop(token, None)
            receipt = None
        expected = receipt.secret if receipt else (job.claim_secret if job else None)
        if not job and not receipt:
            return None, json_error(
                "This session expired. Run /compress in Discord for a new link.",
                410,
                "session_expired",
            )
        supplied = request.headers.get("X-Upload-Session", "")
        if not expected or not secrets.compare_digest(
            supplied.encode(), expected.encode()
        ):
            return None, json_error(
                "This browser is not authorized to use the upload session.",
                403,
                "session_invalid",
            )
        if receipt:
            return None, web.json_response(
                receipt.body,
                status=receipt.status,
                headers={"Cache-Control": "no-store"},
            )
        return job, None

    async def session_status(self, request: web.Request) -> web.Response:
        job, response = self.session_access(request)
        if response is not None:
            return response
        assert job is not None
        if job.state in {JobState.UPLOADING, JobState.QUEUED}:
            return pending_response()
        return web.json_response(
            {
                "ok": False,
                "ready": True,
                "expires_in": max(0, int(job.expires_at - time.time())),
            },
            headers={"Cache-Control": "no-store"},
        )

    async def renew_session(self, request: web.Request) -> web.Response:
        job, response = self.session_access(request)
        if response is not None:
            return response
        assert job is not None
        if job.state is JobState.CLAIMED:
            if not self.draining:
                hard_deadline = (job.claimed_at or job.created_at) + max(
                    7200, self.settings.active_session_ttl_seconds
                )
                job.expires_at = min(
                    time.time() + self.settings.active_session_ttl_seconds,
                    hard_deadline,
                )
            job.processing_until = min(time.time() + 90, job.expires_at)
        return await self.session_status(request)

    async def pause_session(self, request: web.Request) -> web.Response:
        job, response = self.session_access(request)
        if response is not None:
            return response
        assert job is not None
        job.processing_until = 0
        return await self.session_status(request)

    async def receive_browser_progress(self, request: web.Request) -> web.Response:
        """Record a small, authenticated browser milestone, never filenames."""
        job, response = self.session_access(request)
        if response is not None:
            return response
        assert job is not None
        if job.state is not JobState.CLAIMED:
            return json_error(
                "The session is already being delivered.", 409, "session_used"
            )
        try:
            payload = await asyncio.wait_for(
                request.clone(client_max_size=4096).json(), 5
            )
        except web.HTTPRequestEntityTooLarge:
            return json_error("Progress report is too large.", 413, "report_too_large")
        except (ValueError, TypeError, TimeoutError):
            return json_error("Expected a JSON object.", 400, "invalid_progress")
        if not isinstance(payload, dict):
            return json_error("Expected a JSON object.", 400, "invalid_progress")
        event = payload.get("event")
        if event == "selected":
            count = payload.get("count")
            if type(count) is not int or not 1 <= count <= self.settings.max_clips:
                return json_error("Invalid file count.", 400, "invalid_progress")
            first_selection = job.selected_count == 0
            job.selected_count = count
            job.page_left = False
            job.page_left_at = None
            job.browser_cancelled = False
            if first_selection:
                self.schedule_owner_alert(
                    compression_progress_alert(
                        job,
                        "📁 **Videos selected in browser**",
                        f"Files selected: `{count}` (not uploaded yet).",
                    )
                )
        elif event == "started":
            next_run = valid_id(payload.get("run_id")) or secrets.token_hex(16)
            if next_run != job.active_run_id:
                job.browser_failure_reported = False
            job.active_run_id = next_run
            job.page_left = False
            job.page_left_at = None
            job.browser_cancelled = False
            if not job.compression_started:
                job.compression_started = True
                self.schedule_owner_alert(
                    compression_progress_alert(
                        job,
                        "▶️ **Compress pressed**",
                        "Stage: browser preparation/compression started; no finished files uploaded yet.",
                    )
                )
        elif event == "cancelled":
            job.browser_cancelled = True
            job.page_left = False
            job.page_left_at = None
            if not job.browser_cancel_reported:
                job.browser_cancel_reported = True
                self.schedule_owner_alert(
                    compression_progress_alert(
                        job,
                        "⏹️ **Compression cancelled in browser**",
                        "Stage: user pressed Cancel; the same link can be retried while active.",
                    )
                )
        elif event == "page_left":
            job.page_left = True
            job.page_left_at = time.time()
            if not job.page_left_reported:
                job.page_left_reported = True
                self.schedule_owner_alert(
                    compression_progress_alert(
                        job,
                        "🚪 **Compressor page left**",
                        "Browser reported navigation, reload, or tab close; delivery is not confirmed.",
                    )
                )
        else:
            return json_error("Unknown progress event.", 400, "invalid_progress")
        self.track(job, event, data={"selected_count": job.selected_count})
        return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})

    async def receive_diagnostic_events(self, request: web.Request) -> web.Response:
        job, response = self.session_access(request)
        if response is not None and response.status in {403, 410}:
            return response
        receipt = (
            self.outcomes.get(request.match_info["token"]) if job is None else None
        )
        diagnostic_id = (
            job.diagnostic_id if job else (receipt.diagnostic_id if receipt else "")
        )
        if not diagnostic_id:
            return json_error("Session unavailable.", 410, "session_expired")
        if (
            self.rate_limit_retry_after(request, "diagnostic_events", limit=120)
            is not None
        ):
            return json_error("Too many diagnostic reports.", 429, "rate_limited")
        try:
            payload = await asyncio.wait_for(
                request.clone(client_max_size=16384).json(), 3
            )
        except (ValueError, TypeError, TimeoutError, web.HTTPException):
            return json_error("Invalid diagnostic report.", 400, "invalid_report")
        if not isinstance(payload, dict) or not valid_id(payload.get("run_id")):
            return json_error("Invalid run.", 400, "invalid_report")
        events = payload.get("events")
        if not isinstance(events, list) or len(events) > 20:
            return json_error("Invalid events.", 400, "invalid_report")
        count = self.diagnostic_event_counts.get(diagnostic_id, 0)
        if count + len(events) > 1000:
            return json_error("Diagnostic limit reached.", 429, "rate_limited")
        for item in events:
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("event"), str)
                or item.get("event") not in CLIENT_EVENTS
                or not valid_id(item.get("event_id"))
                or type(item.get("file_index")) is not int
                or not 0 <= item["file_index"] <= self.settings.max_clips
            ):
                return json_error("Invalid event.", 400, "invalid_report")
        self.diagnostic_event_counts[diagnostic_id] = count + len(events)
        for item in events:
            kwargs = {
                "run_id": payload["run_id"],
                "event_id": item["event_id"],
                "file_index": item["file_index"],
                "source": "browser",
                "data": item.get("data"),
            }
            if job:
                self.track(job, item["event"], **kwargs)
            else:
                self.event_store.record(
                    diagnostic_id,
                    "PC-" + diagnostic_id[:12].upper(),
                    item["event"],
                    is_test=receipt.is_test,
                    **kwargs,
                )
        return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})

    async def receive_results(self, request: web.Request) -> web.Response:
        receive_started = time.monotonic()
        job, response = self.session_access(request)
        if response is not None:
            return response
        assert job is not None
        if job.state in {JobState.UPLOADING, JobState.QUEUED}:
            return pending_response()
        if job.state is not JobState.CLAIMED:
            return json_error(
                "This one-use session is no longer available.",
                410,
                "session_used",
            )
        retry_after = self.rate_limit_retry_after(request, "upload")
        if retry_after is not None:
            return json_error(
                f"Too many upload attempts. Try again in {retry_after} seconds.",
                429,
                "rate_limited",
                retry_after=retry_after,
            )
        if self.active_relay_uploads >= self.settings.max_concurrent_uploads:
            return json_error(
                "The relay is receiving its maximum number of uploads. "
                "It will retry automatically.",
                503,
                "relay_busy",
                retry_after=3,
            )
        if self.delivery_queue is None or self.delivery_queue.full():
            return json_error(
                "The Discord delivery queue is full. It will retry automatically.",
                503,
                "queue_full",
                retry_after=5,
            )

        results: list[BrowserResult] = []
        caption = ""
        caption_received = False
        diagnostics: list[CompressionDiagnostic] = []
        diagnostics_received = False
        total_size = 0
        last_chunk_at = receive_started
        received_bytes = 0
        job.relay_upload_attempts += 1

        def upload_failure_facts(code: str) -> dict:
            now = time.monotonic()
            return {
                "error_code": code,
                "stage": "upload",
                "upload_attempts": job.relay_upload_attempts,
                "received_bytes": received_bytes,
                "relay_receive_seconds": max(0, now - receive_started),
                "receive_idle_seconds": max(0, now - last_chunk_at),
            }

        upload_slot_held = True
        job.state = JobState.UPLOADING
        upload_run = (
            valid_id(request.headers.get("X-Diagnostic-Run")) or job.active_run_id
        )
        self.track(job, "upload_started", run_id=upload_run)
        self.active_relay_uploads += 1
        if not job.relay_upload_started:
            job.relay_upload_started = True
            self.schedule_owner_alert(
                compression_progress_alert(
                    job,
                    "📨 **Relay upload started**",
                    "Stage: browser began sending finished files; receipt is not yet confirmed.",
                )
            )
        try:
            if not request.content_type.startswith("multipart/"):
                raise ValueError("The upload format was invalid. Please try again.")
            reader = await request.multipart()
            upload_deadline = time.monotonic() + 300
            while True:
                field = await asyncio.wait_for(
                    reader.next(),
                    timeout=max(0, min(30, upload_deadline - time.monotonic())),
                )
                if field is None:
                    break
                if field.name == "caption":
                    if caption_received or results:
                        raise ValueError(
                            "The message field was repeated or out of order."
                        )
                    caption_received = True
                    caption_data = bytearray()
                    while chunk := await asyncio.wait_for(
                        field.read_chunk(1024),
                        timeout=max(0, min(30, upload_deadline - time.monotonic())),
                    ):
                        caption_data.extend(chunk)
                        received_bytes += len(chunk)
                        last_chunk_at = time.monotonic()
                        if len(caption_data) > 4000:
                            break
                    if len(caption_data) > 4000:
                        raise ValueError("The message is too long.")
                    try:
                        caption = " ".join(caption_data.decode("utf-8").split())
                    except UnicodeDecodeError as error:
                        raise ValueError(
                            "The message contains invalid text."
                        ) from error
                    if len(caption) > 1000:
                        raise ValueError(
                            "The message must be 1,000 characters or fewer."
                        )
                    continue
                if field.name == "diagnostics":
                    if diagnostics_received or results:
                        raise ValueError(
                            "The diagnostics field was repeated or out of order."
                        )
                    diagnostics_received = True
                    raw_diagnostics = bytearray()
                    while chunk := await asyncio.wait_for(
                        field.read_chunk(1024),
                        timeout=max(0, min(30, upload_deadline - time.monotonic())),
                    ):
                        raw_diagnostics.extend(chunk)
                        received_bytes += len(chunk)
                        last_chunk_at = time.monotonic()
                        if len(raw_diagnostics) > 16384:
                            raise ValueError("The diagnostics field is too long.")
                    diagnostics = parse_compression_diagnostics(
                        bytes(raw_diagnostics), max_clips=self.settings.max_clips
                    )
                    continue
                if field.name != "clips" or not field.filename:
                    raise ValueError("Only video file fields are accepted.")
                if len(results) >= self.settings.max_clips:
                    raise ValueError(
                        f"You can send at most {self.settings.max_clips} results."
                    )

                name = safe_result_name(field.filename, len(results) + 1)

                file_data = bytearray()
                while chunk := await asyncio.wait_for(
                    field.read_chunk(1024 * 1024),
                    timeout=max(0, min(30, upload_deadline - time.monotonic())),
                ):
                    if (
                        self.delivery_bytes_held + len(chunk)
                        > self.settings.max_delivery_buffer_bytes
                    ):
                        return json_error(
                            "The relay memory buffer is full. Try again shortly.",
                            503,
                            "delivery_buffer_full",
                            retry_after=5,
                        )
                    self.delivery_bytes_held += len(chunk)
                    received_bytes += len(chunk)
                    last_chunk_at = time.monotonic()
                    total_size += len(chunk)
                    file_data.extend(chunk)
                    if len(file_data) > job.discord_limit:
                        raise ValueError(
                            f"{name} exceeds Discord's current file-size limit."
                        )
                    if total_size > self.settings.max_result_total_bytes:
                        raise ValueError(
                            "The combined compressed results exceed the relay limit."
                        )
                if not file_data:
                    raise ValueError(f"{name} is empty.")
                result_bytes = bytes(file_data)
                del file_data
                if not valid_mp4_signature(result_bytes):
                    raise ValueError(
                        f"{name} did not contain a complete MP4 video. "
                        "Try the original again or convert it to MP4 first."
                    )
                results.append(BrowserResult(name=name, data=result_bytes))

            if not results:
                raise ValueError("No compressed MP4 results were received.")
            if len(diagnostics) != len(results):
                diagnostics = []
            result_count = len(results)
            loop = asyncio.get_running_loop()
            completion: asyncio.Future[str] = loop.create_future()
            # A disconnected request may never await this future again.
            completion.add_done_callback(
                lambda future: future.exception() if not future.cancelled() else None
            )
            delivery = DeliveryRequest(
                job=job,
                results=results,
                completed=completion,
                caption=caption,
                diagnostics=diagnostics,
                received_seconds=time.monotonic() - receive_started,
                run_id=upload_run,
            )
            try:
                self.delivery_queue.put_nowait(delivery)
            except asyncio.QueueFull:
                job.state = JobState.CLAIMED
                return json_error(
                    "The Discord delivery queue filled up. It will retry automatically.",
                    503,
                    "queue_full",
                    retry_after=5,
                )
            self.metrics.increment("batches_queued")
            self.metrics.increment("files_queued", len(results))
            self.metrics.increment("bytes_queued", total_size)
            self.active_relay_uploads -= 1
            upload_slot_held = False
            job.state = JobState.QUEUED
            self.track(
                job,
                "received",
                run_id=upload_run,
                data={
                    "files": len(results),
                    "output_bytes": total_size,
                    "relay_receive_seconds": delivery.received_seconds,
                },
            )
            for number, (result, diagnostic) in enumerate(
                zip(results, diagnostics, strict=False), 1
            ):
                self.track(
                    job,
                    "file_received",
                    run_id=upload_run,
                    file_index=number,
                    data={
                        **diagnostic.telemetry,
                        "input_bytes": diagnostic.input_bytes,
                        "output_bytes": len(result.data),
                        "duration_seconds": diagnostic.duration_seconds,
                        "encoding_seconds": diagnostic.encoding_seconds,
                        "input_format": diagnostic.input_format,
                    },
                )
            self.schedule_owner_alert(
                compression_progress_alert(
                    job,
                    "📤 **Finished files reached relay**",
                    f"Files: `{result_count}`; queued for Discord delivery.",
                )
            )
            try:
                message = await asyncio.wait_for(
                    asyncio.shield(completion), timeout=DELIVERY_RESPONSE_WAIT_SECONDS
                )
            except TimeoutError:
                return pending_response()
            except RuntimeError as error:
                _, response = self.session_access(request)
                return (
                    response
                    if response is not None
                    else json_error(str(error), 502, "discord_delivery_failed")
                )
            job.state = JobState.DONE
            return web.json_response(
                {
                    "ok": True,
                    "message": message,
                    "count": result_count,
                },
                headers={"Cache-Control": "no-store"},
            )
        except TimeoutError:
            self.track(
                job,
                "upload_failed",
                run_id=upload_run,
                data=upload_failure_facts("UPLOAD_TIMEOUT"),
            )
            return json_error(
                "The upload stalled. Please try again.", 408, "upload_timeout"
            )
        except ConnectionResetError:
            job.state = JobState.CLAIMED
            self.track(
                job,
                "upload_failed",
                run_id=upload_run,
                data=upload_failure_facts("UPLOAD_CONNECTION_RESET"),
            )
            logger.warning(
                "Upload connection reset (reference=%s)", job.diagnostic_reference
            )
            return json_error(
                "The upload connection was interrupted. Keep this page open and retry sending; your prepared files do not need recompression.",
                503,
                "upload_connection_reset",
                retry_after=3,
            )
        except (ValueError, web.HTTPException) as error:
            self.track(
                job,
                "upload_failed",
                run_id=upload_run,
                data={"error_code": "INVALID_UPLOAD", "stage": "upload"},
            )
            job.state = JobState.CLAIMED
            return json_error(str(error), 400, "invalid_upload")
        except Exception as error:
            job.state = JobState.CLAIMED
            self.track(
                job,
                "upload_failed",
                run_id=upload_run,
                data=upload_failure_facts("RELAY_ERROR"),
            )
            logger.warning("Upload relay failed (%s)", type(error).__name__)
            return json_error(
                "The relay could not read the compressed files. "
                "Check your connection and try again.",
                500,
                "relay_error",
            )
        finally:
            if upload_slot_held:
                self.active_relay_uploads -= 1
                self.delivery_bytes_held = max(0, self.delivery_bytes_held - total_size)
                job.state = JobState.CLAIMED

    async def receive_browser_telemetry(self, request: web.Request) -> web.Response:
        _job, response = self.session_access(request)
        # Valid receipts can report final timing after the delivery job is removed.
        if response is not None and response.status in {403, 410}:
            return response
        if self.rate_limit_retry_after(request, "telemetry") is not None:
            return json_error("Too many reports.", 429, "rate_limited")
        try:
            payload = await asyncio.wait_for(
                request.clone(client_max_size=4096).json(), 5
            )
        except (ValueError, TypeError, TimeoutError, web.HTTPException):
            return json_error("Invalid report.", 400, "invalid_report")
        if not isinstance(payload, dict):
            return json_error("Invalid report.", 400, "invalid_report")
        clean = sanitize_telemetry(payload)
        for key, allowed in {
            "outcome": {"delivered", "failed", "cancelled"},
            "category": {
                "none",
                "network",
                "http",
                "processing",
                "cancelled",
                "memory",
                "timeout",
            },
            "stage": {
                "processing",
                "delivery",
                "engine",
                "metadata",
                "encoding",
                "remux",
                "upload",
            },
        }.items():
            item = payload.get(key)
            if isinstance(item, str) and item in allowed:
                clean[key] = item
        clean["retry_recovered"] = clean.get("outcome") == "delivered" and (
            clean.get("processing_failures", 0) > 0
            or clean.get("delivery_failures", 0) > 0
        )
        correlated = dict(clean)
        correlated_job = _job
        correlated_receipt = self.outcomes.get(request.match_info["token"])
        correlated["diagnostic_id"] = (
            correlated_job.diagnostic_id
            if correlated_job
            else (correlated_receipt.diagnostic_id if correlated_receipt else "")
        )
        correlated["run_id"] = valid_id(request.headers.get("X-Diagnostic-Run"))
        logger.info(
            "Browser processing diagnostics %s", json.dumps(correlated, allow_nan=False)
        )
        receipt = self.outcomes.get(request.match_info["token"])
        if _job is not None:
            self.track(
                _job,
                "client_complete",
                data={**clean, **sanitize(payload)},
                source="browser",
                run_id=valid_id(request.headers.get("X-Diagnostic-Run")),
            )
        elif receipt and receipt.diagnostic_id:
            self.event_store.record(
                receipt.diagnostic_id,
                "PC-" + receipt.diagnostic_id[:12].upper(),
                "client_complete",
                is_test=receipt.is_test,
                source="browser",
                run_id=valid_id(request.headers.get("X-Diagnostic-Run"))
                or receipt.run_id,
                data={**clean, **sanitize(payload)},
            )
        return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})

    async def receive_browser_failure(self, request: web.Request) -> web.Response:
        """Accept a privacy-safe terminal failure signal from the browser client."""
        token = request.match_info["token"]
        job = self.active_job(token)
        if job is None:
            return json_error(
                "This session is no longer active.", 410, "session_expired"
            )
        supplied_secret = request.headers.get("X-Upload-Session", "")
        if not job.claim_secret or not secrets.compare_digest(
            supplied_secret.encode(), job.claim_secret.encode()
        ):
            return json_error(
                "This browser is not authorized to update the session.",
                403,
                "session_invalid",
            )
        if job.state is not JobState.CLAIMED:
            return json_error(
                "This session is still being processed or already reached delivery.",
                409,
                "outcome_already_recorded",
            )
        if self.rate_limit_retry_after(request, "failure", limit=30) is not None:
            return json_error("Too many failure reports.", 429, "rate_limited")
        try:
            payload = await asyncio.wait_for(
                request.clone(client_max_size=4096).json(), 5
            )
        except web.HTTPRequestEntityTooLarge:
            return json_error("Failure report is too large.", 413, "report_too_large")
        except (ValueError, TypeError, TimeoutError):
            payload = {}
        if not isinstance(payload, dict):
            return json_error("Expected a JSON object.", 400, "invalid_failure_report")
        failure_code = sanitize(payload).get("error_code", "UNKNOWN")
        operation = sanitize({"stage": payload.get("operation")}).get("stage", "")
        failure_run = valid_id(payload.get("run_id")) or job.active_run_id
        failure_file = payload.get("file_index", 0)
        if type(failure_file) is not int or not 0 <= failure_file <= 10:
            failure_file = 0
        self.track(
            job,
            "client_failed",
            source="browser",
            run_id=failure_run,
            event_id=failure_run,
            file_index=failure_file,
            data={**sanitize(payload), "error_code": failure_code, "stage": operation},
        )
        raw_stage = str(payload.get("stage", "Browser processing"))
        stage = "".join(
            character
            for character in raw_stage
            if character.isalnum() or character in " ._-"
        ).strip()[:60]
        if not job.browser_failure_reported and (
            not job.active_run_id or failure_run == job.active_run_id
        ):
            self.schedule_owner_alert(
                compression_outcome_alert(
                    job,
                    succeeded=False,
                    stage=(operation or stage or "Browser processing"),
                    error_code=failure_code,
                )
            )
            job.browser_failure_reported = True
        return web.json_response(
            {"ok": True},
            headers={"Cache-Control": "no-store"},
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

    async def delivery_worker(self, worker_number: int) -> None:
        assert self.delivery_queue is not None
        while not self.client.is_closed():
            delivery = await self.delivery_queue.get()
            delivery_started = time.monotonic()
            self.track(
                delivery.job,
                "delivery_started",
                run_id=delivery.run_id,
                data={"queue_seconds": max(0, delivery_started - delivery.queued_at)},
            )
            try:
                message = await asyncio.wait_for(
                    (self._deliver_results or self.deliver_browser_results)(
                        delivery.job, delivery.results, delivery.caption
                    ),
                    timeout=120,
                )
                total_bytes = sum(len(result.data) for result in delivery.results)
                try:
                    log_delivery_diagnostics(delivery, delivery_started, "delivered")
                except Exception:
                    logger.warning("Optional delivery diagnostic formatting failed")
                self.track(
                    delivery.job,
                    "delivered",
                    run_id=delivery.run_id,
                    data={
                        "files": len(delivery.results),
                        "output_bytes": total_bytes,
                        **delivery_timing(delivery, delivery_started),
                    },
                )
                self.metrics.increment("deliveries_succeeded")
                self.metrics.increment("files_delivered", len(delivery.results))
                self.metrics.increment("bytes_delivered", total_bytes)
                self.record_outcome(
                    delivery.job,
                    200,
                    {
                        "ok": True,
                        "message": message,
                        "count": len(delivery.results),
                    },
                )
                self.schedule_owner_alert(
                    compression_outcome_alert(
                        delivery.job,
                        succeeded=True,
                        file_count=len(delivery.results),
                        total_bytes=total_bytes,
                        results=delivery.results,
                        diagnostics=delivery.diagnostics,
                        timing=delivery_timing(delivery, delivery_started),
                        effective_target=min(
                            compression_target(delivery.job.discord_limit),
                            min(
                                self.settings.max_result_total_bytes,
                                self.settings.max_delivery_buffer_bytes,
                            )
                            // len(delivery.results),
                        ),
                        unchanged_limit=min(
                            delivery.job.discord_limit,
                            min(
                                self.settings.max_result_total_bytes,
                                self.settings.max_delivery_buffer_bytes,
                            )
                            // len(delivery.results),
                        ),
                    )
                )
                if not delivery.completed.done():
                    delivery.completed.set_result(message)
            except Exception as error:
                failure = delivery_failure(error)
                try:
                    log_delivery_diagnostics(
                        delivery, delivery_started, "delivery_failed"
                    )
                except Exception:
                    logger.warning("Optional delivery diagnostic formatting failed")
                self.track(
                    delivery.job,
                    "delivery_failed",
                    run_id=delivery.run_id,
                    data={
                        "error_code": failure.error_code,
                        "stage": "delivery",
                        **delivery_timing(delivery, delivery_started),
                    },
                )
                self.metrics.increment("deliveries_failed")
                message = str(failure)
                self.record_outcome(
                    delivery.job,
                    502,
                    {
                        "ok": False,
                        "error": message,
                        "code": "discord_delivery_failed",
                        "delivery_outcome": failure.outcome,
                        "error_code": failure.error_code,
                    },
                )
                self.schedule_owner_alert(
                    compression_outcome_alert(
                        delivery.job,
                        succeeded=False,
                        file_count=len(delivery.results),
                        total_bytes=sum(
                            len(result.data) for result in delivery.results
                        ),
                        stage="Discord delivery",
                        results=delivery.results,
                        diagnostics=delivery.diagnostics,
                        timing=delivery_timing(delivery, delivery_started),
                        effective_target=min(
                            compression_target(delivery.job.discord_limit),
                            min(
                                self.settings.max_result_total_bytes,
                                self.settings.max_delivery_buffer_bytes,
                            )
                            // len(delivery.results),
                        ),
                        unchanged_limit=min(
                            delivery.job.discord_limit,
                            min(
                                self.settings.max_result_total_bytes,
                                self.settings.max_delivery_buffer_bytes,
                            )
                            // len(delivery.results),
                        ),
                    )
                )
                if not delivery.completed.done():
                    delivery.completed.set_exception(RuntimeError(message))
            finally:
                self.delivery_bytes_held = max(
                    0,
                    self.delivery_bytes_held
                    - sum(len(result.data) for result in delivery.results),
                )
                delivery.job.state = JobState.DONE
                self.jobs.pop(delivery.job.token, None)
                self.delivery_queue.task_done()
                logger.info("Delivery worker %d completed a session", worker_number)
                del delivery

    @app_commands.guild_only()
    @app_commands.allowed_installs(guilds=True, users=False)
    async def compress(self, interaction: discord.Interaction) -> None:
        if self.draining:
            await interaction.response.send_message(
                "Professor Compressor is finishing existing work before maintenance. Please try again shortly.",
                ephemeral=True,
            )
            return
        if (
            self.effective_allowed_guild_ids
            and interaction.guild_id not in self.effective_allowed_guild_ids
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
        self.discard_expired_jobs()
        user_jobs = [
            job for job in self.jobs.values() if job.user_id == interaction.user.id
        ]
        if any(job.state in {JobState.UPLOADING, JobState.QUEUED} for job in user_jobs):
            await interaction.response.send_message(
                "Your existing session is sending files to Discord. Check the original channel "
                "for the result before starting another session. It has not been replaced.",
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
                self.last_job_at.get(cooldown_key, 0.0)
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
                "Your existing compressor session may still be active. Return to its tab "
                f"or wait {seconds} seconds and run /compress again. The current "
                "session has not been replaced.",
                ephemeral=True,
            )
            return
        cooldown_key = (interaction.guild_id or 0, interaction.user.id)
        previous_job_at = self.last_job_at.get(cooldown_key, 0.0)
        remaining = self.settings.user_cooldown_seconds - (now - previous_job_at)
        if remaining > 0:
            await interaction.response.send_message(
                f"Please wait {max(1, int(remaining + 0.999))} seconds before "
                "creating another upload link.",
                ephemeral=True,
            )
            return
        if len(self.jobs) >= self.settings.max_active_jobs:
            await interaction.response.send_message(
                "Professor Compressor is handling too many upload sessions right "
                "now. Please try again in a few minutes.",
                ephemeral=True,
            )
            return

        # One active link per member. A new command invalidates their older link.
        for old_token, old_job in list(self.jobs.items()):
            if old_job.user_id == interaction.user.id:
                self.jobs.pop(old_token, None)
                self.track(old_job, "replaced")
                self.schedule_owner_alert(
                    compression_progress_alert(
                        old_job,
                        "🔄 **Compression session replaced**",
                        "A new /compress command replaced this unfinished link.",
                    )
                )
        self.last_job_at[cooldown_key] = now

        token = secrets.token_urlsafe(32)
        self.jobs[token] = UploadJob(
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
        self.track(
            self.jobs[token],
            "created",
            data={"discord_limit": self.jobs[token].discord_limit},
        )
        upload_url = f"{self.settings.public_base_url}/upload/{quote(token)}"
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="Open compressor", url=upload_url))
        await interaction.response.send_message(
            f"Choose up to {self.settings.max_clips} videos. Keep the compressor page open "
            f"until they are sent back here. Compression runs locally. "
            f"The link expires in {self.settings.job_ttl_seconds // 60} minutes.",
            view=view,
            ephemeral=True,
        )
        guild_name = self.interaction_guild_name(interaction)
        self.schedule_owner_alert(
            "⚙️ **Compression session created**\n"
            f"Server: **{safe_alert_text(guild_name)}**\n"
            f"Server ID: `{interaction.guild_id}`\n"
            f"Username: **{safe_alert_text('@' + interaction.user.name)}**\n"
            f"User ID: `{interaction.user.id}`\n"
            f"Connected servers: `{len(self.client.guilds)}`\n"
            f"Sessions since restart: `{self.metrics.snapshot()['sessions_created']}`"
            f"\nReference: `{self.jobs[token].diagnostic_reference}`"
        )

    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
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
        if len(report) <= 1900:
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
            "revision": os.getenv("APP_REVISION", "development")[:64],
            "active_sessions": len(self.jobs),
            "active_uploads": self.active_relay_uploads,
            "queue_depth": self.delivery_queue.qsize() if self.delivery_queue else 0,
            "queue_capacity": self.settings.delivery_queue_size,
            "delivery_workers": sum(
                not worker.done() for worker in self.delivery_workers
            ),
            "worker_capacity": self.settings.delivery_workers,
            "buffer_bytes": self.delivery_bytes_held,
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
        application.router.add_get("/upload/{token}", self.upload_form)
        application.router.add_post("/upload/{token}", self.receive_results)
        application.router.add_get("/upload/{token}/status", self.session_status)
        application.router.add_post("/upload/{token}/heartbeat", self.renew_session)
        application.router.add_post("/upload/{token}/pause", self.pause_session)
        application.router.add_post(
            "/upload/{token}/progress", self.receive_browser_progress
        )
        application.router.add_post(
            "/upload/{token}/telemetry", self.receive_browser_telemetry
        )
        application.router.add_post(
            "/upload/{token}/events", self.receive_diagnostic_events
        )
        application.router.add_post(
            "/upload/{token}/failure",
            self.receive_browser_failure,
        )
        package_root = Path(__file__).resolve().parent
        application.router.add_static("/brand/", package_root / "static")
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
        self.delivery_queue = asyncio.Queue(maxsize=self.settings.delivery_queue_size)
        for worker_number in range(1, self.settings.delivery_workers + 1):
            worker = asyncio.create_task(self.delivery_worker(worker_number))
            self.delivery_workers.append(worker)
            self.background_tasks.add(worker)
            worker.add_done_callback(self.background_tasks.discard)
        self.cleanup_task = asyncio.create_task(self.expire_jobs_loop())
        self.background_tasks.add(self.cleanup_task)
        self.cleanup_task.add_done_callback(self.background_tasks.discard)
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
            self.command_sync_task = asyncio.create_task(
                self.sync_commands_with_retry()
            )
            self.background_tasks.add(self.command_sync_task)
            self.command_sync_task.add_done_callback(self.background_tasks.discard)
        if self.settings.dsc_api_token and (
            self.dsc_stats_task is None or self.dsc_stats_task.done()
        ):
            self.dsc_stats_task = asyncio.create_task(self.dsc_stats_loop())
            self.background_tasks.add(self.dsc_stats_task)
            self.dsc_stats_task.add_done_callback(self.background_tasks.discard)
        if self.topgg_stats.token and (
            self.topgg_stats_task is None or self.topgg_stats_task.done()
        ):
            self.topgg_stats_task = asyncio.create_task(self.topgg_stats.run())
            self.background_tasks.add(self.topgg_stats_task)
            self.topgg_stats_task.add_done_callback(self.background_tasks.discard)
        logger.info(
            "Logged in as %s in %d server(s)", self.client.user, len(self.client.guilds)
        )

    async def drain_sessions(self, timeout: float = 120) -> None:
        """Reject new work while allowing existing browser/relay work to finish."""
        self.draining = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = self.active_relay_uploads or any(
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
