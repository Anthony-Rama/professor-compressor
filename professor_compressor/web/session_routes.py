"""HTTP routes that open, poll, renew, pause, and upload to a browser session."""

import secrets
import time
from urllib.parse import quote

from aiohttp import web

from ..bot.delivery_policy import compression_target
from ..bot.messages import compression_progress_alert
from ..config import Settings
from ..domain import JobState, UploadJob
from ..integrations.alerts import OwnerAlerts
from ..observability.contract import diagnostic_reference
from ..observability.metrics import RuntimeMetrics
from ..observability.tracking import SessionTracker
from ..relay.upload import RelayState, receive_results
from ..sessions.registry import SessionRegistry
from .pages.compressor import (
    LINK_UNAVAILABLE_BODY,
    MAINTENANCE_BODY,
    browser_compressor,
    browser_delivery_status,
    rate_limited_body,
)
from .pages.document import page
from .rate_limit import RateLimiter
from .responses import pending_response

RESUME_COOKIE_NAME = "__Secure-pc-browser"
RESUME_COOKIE_MAX_AGE_SECONDS = 3 * 3600
# Activity renews a claimed link, but never beyond this long after it was opened.
HARD_SESSION_LIFETIME_SECONDS = 7200
# A heartbeat marks the page as actively processing for this long.
PROCESSING_LEASE_SECONDS = 90


def extend_claimed_session(job: UploadJob, ttl_seconds: int) -> None:
    """Slide a claimed link's expiry forward, but never past its hard lifetime."""
    hard_deadline = (job.claimed_at or job.created_at) + max(
        HARD_SESSION_LIFETIME_SECONDS, ttl_seconds
    )
    job.expires_at = min(time.time() + ttl_seconds, hard_deadline)


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


class SessionRoutes:
    """Handlers for /upload/{token} and its status, heartbeat, and pause routes."""

    def __init__(
        self,
        settings: Settings,
        *,
        sessions: SessionRegistry,
        rate_limiter: RateLimiter,
        tracker: SessionTracker,
        alerts: OwnerAlerts,
        metrics: RuntimeMetrics,
        relay: RelayState,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.rate_limiter = rate_limiter
        self.tracker = tracker
        self.alerts = alerts
        self.metrics = metrics
        self.relay = relay

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
        # aiohttp also dispatches HEAD to GET handlers; inspection must not claim
        # a link.
        if request.method == "HEAD":
            return web.Response(headers={"Cache-Control": "no-store"})
        if self.sessions.draining:
            return page("Maintenance", MAINTENANCE_BODY)
        retry_after = self.rate_limiter.retry_after(request, "open")
        if retry_after is not None:
            return page("Please wait", rate_limited_body(retry_after))
        token = request.match_info["token"]
        job = self.sessions.active_job(token)
        receipt = self.sessions.receipt(token)
        if job is not None and job.state is JobState.OPEN:
            job.claim_secret = secrets.token_urlsafe(32)
            job.browser_secret = secrets.token_urlsafe(32)
            job.claimed_at = time.time()
            job.expires_at = time.time() + self.settings.active_session_ttl_seconds
            job.state = JobState.CLAIMED
            self.tracker.track(job, "opened")
            self.metrics.increment("sessions_opened")
            self.alerts.schedule(
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
                self.tracker.track(job, "refreshed")
                # A fresh page supersedes any old tab holding the previous secret.
                job.claim_secret = secrets.token_urlsafe(32)
                job.page_left = False
                job.page_left_at = None
                job.processing_until = 0
                extend_claimed_session(job, self.settings.active_session_ttl_seconds)
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
                    diagnostic_reference=diagnostic_reference(receipt.diagnostic_id)
                    if receipt.diagnostic_id
                    else "",
                ),
            )
        return page("Private link unavailable", LINK_UNAVAILABLE_BODY)

    async def receive_results(self, request: web.Request) -> web.Response:
        return await receive_results(
            request,
            state=self.relay,
            limits=self.settings,
            authorize=self.sessions.session_access,
            rate_limit=self.rate_limiter.retry_after,
            track=self.tracker.track,
            notify=self.alerts.schedule,
            metrics=self.metrics,
        )

    async def session_status(self, request: web.Request) -> web.Response:
        job, response = self.sessions.session_access(request)
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
        job, response = self.sessions.session_access(request)
        if response is not None:
            return response
        assert job is not None
        if job.state is JobState.CLAIMED:
            if not self.sessions.draining:
                extend_claimed_session(job, self.settings.active_session_ttl_seconds)
            job.processing_until = min(
                time.time() + PROCESSING_LEASE_SECONDS, job.expires_at
            )
        return await self.session_status(request)

    async def pause_session(self, request: web.Request) -> web.Response:
        job, response = self.sessions.session_access(request)
        if response is not None:
            return response
        assert job is not None
        job.processing_until = 0
        return await self.session_status(request)
