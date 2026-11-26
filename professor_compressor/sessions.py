"""Browser session claims, authenticated receipts, expiry, and lease endpoints."""

import secrets
import time
from collections import deque
from typing import Protocol
from urllib.parse import quote

from aiohttp import web

from .config import Settings
from .delivery_policy import compression_target
from .domain import DeliveryOutcome, JobState, UploadJob
from .metrics import RuntimeMetrics
from .notifications import compression_progress_alert
from .web_pages import page
from .web_responses import json_error, pending_response
from .web_ui import browser_compressor, browser_delivery_status

OUTCOME_TTL_SECONDS = 3600
RESUME_COOKIE_NAME = "__Secure-pc-browser"
RESUME_COOKIE_MAX_AGE_SECONDS = 3 * 3600


class SessionContext(Protocol):
    settings: Settings
    jobs: dict[str, UploadJob]
    outcomes: dict[str, DeliveryOutcome]
    diagnostic_event_counts: dict[str, int]
    last_job_at: dict[tuple[int, int], float]
    request_times: dict[tuple[str, str], deque[float]]
    draining: bool
    metrics: RuntimeMetrics

    def track(self, job: UploadJob, event: str) -> None: ...
    def schedule_owner_alert(self, message: str) -> None: ...
    def active_job(self, token: str) -> UploadJob | None: ...
    def expire_job(self, token: str, job: UploadJob) -> None: ...
    def rate_limit_retry_after(
        self, request: web.Request, bucket: str
    ) -> int | None: ...
    def session_access(
        self, request: web.Request
    ) -> tuple[UploadJob | None, web.Response | None]: ...


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


def active_job(context: SessionContext, token: str) -> UploadJob | None:
    job = context.jobs.get(token)
    if (
        job is not None
        and time.time() > job.expires_at
        and job.state in {JobState.OPEN, JobState.CLAIMED}
    ):
        context.expire_job(token, job)
        return None
    if job is None or job.state is JobState.DONE:
        context.jobs.pop(token, None)
        return None
    return job


def expire_job(context: SessionContext, token: str, job: UploadJob) -> None:
    """Retire an unfinished session once, with its last confirmed activity."""
    if context.jobs.pop(token, None) is not job:
        return
    context.track(job, "expired")
    if job.browser_failure_reported:
        return
    if job.state is JobState.OPEN:
        detail = "Last confirmed activity: link created; it was never opened."
    elif job.page_left:
        detail = (
            "Last confirmed activity: browser reported the page was left or reloaded."
        )
    elif job.browser_cancelled:
        detail = "Last confirmed activity: browser reported compression was cancelled."
    elif job.relay_upload_started:
        detail = (
            "Last confirmed activity: relay upload was attempted; "
            "no valid completed batch reached the delivery queue."
        )
    elif job.compression_started:
        detail = (
            "Last confirmed activity: browser reported Compress was pressed; "
            "no finished files reached the relay."
        )
    elif job.selected_count:
        detail = (
            f"Last confirmed activity: browser reported {job.selected_count} "
            "file(s) selected; Compress was not reported."
        )
    else:
        detail = "Last confirmed activity: link opened; no file selection was reported."
    context.schedule_owner_alert(
        compression_progress_alert(
            job, "⌛ **Compression session expired without delivery**", detail
        )
    )


def discard_expired_jobs(context: SessionContext) -> None:
    now = time.time()
    expired_tokens = [
        token
        for token, job in context.jobs.items()
        if job.state is JobState.DONE
        or (now > job.expires_at and job.state in {JobState.OPEN, JobState.CLAIMED})
    ]
    for token in expired_tokens:
        job = context.jobs.get(token)
        if job is not None:
            if job.state is JobState.DONE:
                context.jobs.pop(token, None)
            else:
                context.expire_job(token, job)
    for token, outcome in list(context.outcomes.items()):
        if now > outcome.expires_at:
            context.outcomes.pop(token, None)
    retained_ids = {job.diagnostic_id for job in context.jobs.values()} | {
        outcome.diagnostic_id for outcome in context.outcomes.values()
    }
    for diagnostic_id in list(context.diagnostic_event_counts):
        if diagnostic_id not in retained_ids:
            context.diagnostic_event_counts.pop(diagnostic_id, None)

    stale_cooldowns = [
        key
        for key, started_at in context.last_job_at.items()
        if now - started_at > max(context.settings.user_cooldown_seconds * 4, 300)
    ]
    for key in stale_cooldowns:
        context.last_job_at.pop(key, None)

    monotonic_now = time.monotonic()
    for values in context.request_times.values():
        while values and monotonic_now - values[0] >= 60:
            values.popleft()
    stale_rate_keys = [
        key for key, values in context.request_times.items() if not values
    ]
    for key in stale_rate_keys:
        context.request_times.pop(key, None)


def compressor_page(
    context: SessionContext, job: UploadJob, *, reopened: bool = False
) -> web.Response:
    safe_target = compression_target(job.discord_limit)
    return page(
        "Professor Compressor",
        browser_compressor(
            context.settings.max_clips,
            safe_target,
            min(
                context.settings.max_result_total_bytes,
                context.settings.max_delivery_buffer_bytes,
            ),
            job.claim_secret or "",
            max(1, int(job.expires_at - time.time())),
            discord_limit_bytes=job.discord_limit,
            reopened=reopened,
            diagnostic_reference=job.diagnostic_reference,
            diagnostic_events_enabled=bool(context.settings.observability_db_path),
        ),
    )


async def upload_form(context: SessionContext, request: web.Request) -> web.Response:
    # aiohttp also dispatches HEAD to GET handlers; inspection must not claim a link.
    if request.method == "HEAD":
        return web.Response(headers={"Cache-Control": "no-store"})
    if context.draining:
        return page(
            "Maintenance",
            "<h1>Brief maintenance</h1><p>Please try your link again shortly.</p>",
        )
    retry_after = context.rate_limit_retry_after(request, "open")
    if retry_after is not None:
        return page(
            "Please wait",
            "<h1>Too many requests</h1>"
            f"<p>Please wait {retry_after} seconds and reopen this link. "
            "If it has expired, run <strong>/compress</strong> again.</p>",
        )
    token = request.match_info["token"]
    job = context.active_job(token)
    receipt = context.outcomes.get(token)
    if receipt is not None and time.time() > receipt.expires_at:
        context.outcomes.pop(token, None)
        receipt = None
    if job is not None and job.state is JobState.OPEN:
        job.claim_secret = secrets.token_urlsafe(32)
        job.browser_secret = secrets.token_urlsafe(32)
        job.claimed_at = time.time()
        job.expires_at = time.time() + context.settings.active_session_ttl_seconds
        job.state = JobState.CLAIMED
        context.track(job, "opened")
        context.metrics.increment("sessions_opened")
        context.schedule_owner_alert(
            compression_progress_alert(
                job,
                "🔗 **Compressor link opened**",
                "Stage: browser requested the page; no files selected yet.",
            )
        )
        response = compressor_page(context, job)
        set_browser_cookie(response, token, job.browser_secret)
        return response
    if job is not None and browser_cookie_matches(request, job.browser_secret):
        if job.state is JobState.CLAIMED:
            context.track(job, "refreshed")
            # A fresh page supersedes any old tab holding the previous API secret.
            job.claim_secret = secrets.token_urlsafe(32)
            job.page_left = False
            job.page_left_at = None
            job.processing_until = 0
            hard_deadline = (job.claimed_at or job.created_at) + max(
                7200, context.settings.active_session_ttl_seconds
            )
            job.expires_at = min(
                time.time() + context.settings.active_session_ttl_seconds,
                hard_deadline,
            )
            return compressor_page(context, job, reopened=True)
        return page(
            "Delivery status",
            browser_delivery_status(
                job.claim_secret or "",
                diagnostic_reference=job.diagnostic_reference,
            ),
        )
    if receipt is not None and browser_cookie_matches(request, receipt.browser_secret):
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
    context: SessionContext, job: UploadJob, status: int, body: dict[str, object]
) -> None:
    context.outcomes[job.token] = DeliveryOutcome(
        secret=job.claim_secret or "",
        browser_secret=job.browser_secret or "",
        expires_at=time.time() + OUTCOME_TTL_SECONDS,
        status=status,
        body=body,
        diagnostic_id=job.diagnostic_id,
        run_id=job.active_run_id,
        is_test=job.guild_id in context.settings.diagnostic_test_guild_ids,
    )
    # Bound receipt memory independently from active sessions. No media is retained.
    while len(context.outcomes) > max(1000, context.settings.max_active_jobs * 4):
        context.outcomes.pop(next(iter(context.outcomes)))


def session_access(
    context: SessionContext,
    request: web.Request,
) -> tuple[UploadJob | None, web.Response | None]:
    token = request.match_info["token"]
    job = context.active_job(token)
    receipt = context.outcomes.get(token)
    if receipt and time.time() > receipt.expires_at:
        context.outcomes.pop(token, None)
        receipt = None
    expected = receipt.secret if receipt else (job.claim_secret if job else None)
    if not job and not receipt:
        return None, json_error(
            "This session expired. Run /compress in Discord for a new link.",
            410,
            "session_expired",
        )
    supplied = request.headers.get("X-Upload-Session", "")
    if not expected or not secrets.compare_digest(supplied.encode(), expected.encode()):
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


async def session_status(context: SessionContext, request: web.Request) -> web.Response:
    job, response = context.session_access(request)
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


async def renew_session(context: SessionContext, request: web.Request) -> web.Response:
    job, response = context.session_access(request)
    if response is not None:
        return response
    assert job is not None
    if job.state is JobState.CLAIMED:
        if not context.draining:
            hard_deadline = (job.claimed_at or job.created_at) + max(
                7200, context.settings.active_session_ttl_seconds
            )
            job.expires_at = min(
                time.time() + context.settings.active_session_ttl_seconds,
                hard_deadline,
            )
        job.processing_until = min(time.time() + 90, job.expires_at)
    return await session_status(context, request)


async def pause_session(context: SessionContext, request: web.Request) -> web.Response:
    job, response = context.session_access(request)
    if response is not None:
        return response
    assert job is not None
    job.processing_until = 0
    return await session_status(context, request)
