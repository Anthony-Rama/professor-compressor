"""Authenticated browser progress, diagnostics, and failure reports."""

import asyncio
import json
import logging
import secrets
import time
from typing import Protocol

from aiohttp import web

from .config import Settings
from .domain import DeliveryOutcome, JobState, UploadJob
from .notifications import compression_outcome_alert, compression_progress_alert
from .observability import EventStore
from .observability_contract import CLIENT_EVENTS, sanitize, valid_id
from .relay_diagnostics import sanitize_telemetry
from .web_responses import json_error

logger = logging.getLogger(__name__)


class ReportContext(Protocol):
    settings: Settings
    outcomes: dict[str, DeliveryOutcome]
    event_store: EventStore
    diagnostic_event_counts: dict[str, int]

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
    ) -> None: ...
    def schedule_owner_alert(self, message: str) -> None: ...
    def active_job(self, token: str) -> UploadJob | None: ...
    def rate_limit_retry_after(
        self, request: web.Request, bucket: str, *, limit: int | None = None
    ) -> int | None: ...
    def session_access(
        self, request: web.Request
    ) -> tuple[UploadJob | None, web.Response | None]: ...


async def receive_browser_progress(
    context: ReportContext, request: web.Request
) -> web.Response:
    """Record a small, authenticated browser milestone, never filenames."""
    job, response = context.session_access(request)
    if response is not None:
        return response
    assert job is not None
    if job.state is not JobState.CLAIMED:
        return json_error(
            "The session is already being delivered.", 409, "session_used"
        )
    try:
        payload = await asyncio.wait_for(request.clone(client_max_size=4096).json(), 5)
    except web.HTTPRequestEntityTooLarge:
        return json_error("Progress report is too large.", 413, "report_too_large")
    except (ValueError, TypeError, TimeoutError):
        return json_error("Expected a JSON object.", 400, "invalid_progress")
    if not isinstance(payload, dict):
        return json_error("Expected a JSON object.", 400, "invalid_progress")
    event = payload.get("event")
    if event == "selected":
        count = payload.get("count")
        if type(count) is not int or not 1 <= count <= context.settings.max_clips:
            return json_error("Invalid file count.", 400, "invalid_progress")
        first_selection = job.selected_count == 0
        job.selected_count = count
        job.page_left = False
        job.page_left_at = None
        job.browser_cancelled = False
        if first_selection:
            context.schedule_owner_alert(
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
            context.schedule_owner_alert(
                compression_progress_alert(
                    job,
                    "▶️ **Compress pressed**",
                    "Stage: browser preparation/compression started; "
                    "no finished files uploaded yet.",
                )
            )
    elif event == "cancelled":
        job.browser_cancelled = True
        job.page_left = False
        job.page_left_at = None
        if not job.browser_cancel_reported:
            job.browser_cancel_reported = True
            context.schedule_owner_alert(
                compression_progress_alert(
                    job,
                    "⏹️ **Compression cancelled in browser**",
                    "Stage: user pressed Cancel; "
                    "the same link can be retried while active.",
                )
            )
    elif event == "page_left":
        job.page_left = True
        job.page_left_at = time.time()
        if not job.page_left_reported:
            job.page_left_reported = True
            context.schedule_owner_alert(
                compression_progress_alert(
                    job,
                    "🚪 **Compressor page left**",
                    "Browser reported navigation, reload, or tab close; "
                    "delivery is not confirmed.",
                )
            )
    else:
        return json_error("Unknown progress event.", 400, "invalid_progress")
    context.track(job, event, data={"selected_count": job.selected_count})
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def receive_diagnostic_events(
    context: ReportContext, request: web.Request
) -> web.Response:
    job, response = context.session_access(request)
    if response is not None and response.status in {403, 410}:
        return response
    receipt = context.outcomes.get(request.match_info["token"]) if job is None else None
    diagnostic_id = (
        job.diagnostic_id if job else (receipt.diagnostic_id if receipt else "")
    )
    if not diagnostic_id:
        return json_error("Session unavailable.", 410, "session_expired")
    if (
        context.rate_limit_retry_after(request, "diagnostic_events", limit=120)
        is not None
    ):
        return json_error("Too many diagnostic reports.", 429, "rate_limited")
    try:
        payload = await asyncio.wait_for(request.clone(client_max_size=16384).json(), 3)
    except (ValueError, TypeError, TimeoutError, web.HTTPException):
        return json_error("Invalid diagnostic report.", 400, "invalid_report")
    if not isinstance(payload, dict) or not valid_id(payload.get("run_id")):
        return json_error("Invalid run.", 400, "invalid_report")
    events = payload.get("events")
    if not isinstance(events, list) or len(events) > 20:
        return json_error("Invalid events.", 400, "invalid_report")
    count = context.diagnostic_event_counts.get(diagnostic_id, 0)
    if count + len(events) > 1000:
        return json_error("Diagnostic limit reached.", 429, "rate_limited")
    for item in events:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("event"), str)
            or item.get("event") not in CLIENT_EVENTS
            or not valid_id(item.get("event_id"))
            or type(item.get("file_index")) is not int
            or not 0 <= item["file_index"] <= context.settings.max_clips
        ):
            return json_error("Invalid event.", 400, "invalid_report")
    context.diagnostic_event_counts[diagnostic_id] = count + len(events)
    for item in events:
        kwargs = {
            "run_id": payload["run_id"],
            "event_id": item["event_id"],
            "file_index": item["file_index"],
            "source": "browser",
            "data": item.get("data"),
        }
        if job:
            context.track(job, item["event"], **kwargs)
        else:
            context.event_store.record(
                diagnostic_id,
                "PC-" + diagnostic_id[:12].upper(),
                item["event"],
                is_test=receipt.is_test,
                **kwargs,
            )
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def receive_browser_telemetry(
    context: ReportContext, request: web.Request
) -> web.Response:
    _job, response = context.session_access(request)
    # Valid receipts can report final timing after the delivery job is removed.
    if response is not None and response.status in {403, 410}:
        return response
    if context.rate_limit_retry_after(request, "telemetry") is not None:
        return json_error("Too many reports.", 429, "rate_limited")
    try:
        payload = await asyncio.wait_for(request.clone(client_max_size=4096).json(), 5)
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
        clean.get("processing_failures", 0) > 0 or clean.get("delivery_failures", 0) > 0
    )
    correlated = dict(clean)
    correlated_job = _job
    correlated_receipt = context.outcomes.get(request.match_info["token"])
    correlated["diagnostic_id"] = (
        correlated_job.diagnostic_id
        if correlated_job
        else (correlated_receipt.diagnostic_id if correlated_receipt else "")
    )
    correlated["run_id"] = valid_id(request.headers.get("X-Diagnostic-Run"))
    logger.info(
        "Browser processing diagnostics %s", json.dumps(correlated, allow_nan=False)
    )
    receipt = context.outcomes.get(request.match_info["token"])
    if _job is not None:
        context.track(
            _job,
            "client_complete",
            data={**clean, **sanitize(payload)},
            source="browser",
            run_id=valid_id(request.headers.get("X-Diagnostic-Run")),
        )
    elif receipt and receipt.diagnostic_id:
        context.event_store.record(
            receipt.diagnostic_id,
            "PC-" + receipt.diagnostic_id[:12].upper(),
            "client_complete",
            is_test=receipt.is_test,
            source="browser",
            run_id=valid_id(request.headers.get("X-Diagnostic-Run")) or receipt.run_id,
            data={**clean, **sanitize(payload)},
        )
    return web.json_response({"ok": True}, headers={"Cache-Control": "no-store"})


async def receive_browser_failure(
    context: ReportContext, request: web.Request
) -> web.Response:
    """Accept a privacy-safe terminal failure signal from the browser client."""
    token = request.match_info["token"]
    job = context.active_job(token)
    if job is None:
        return json_error("This session is no longer active.", 410, "session_expired")
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
    if context.rate_limit_retry_after(request, "failure", limit=30) is not None:
        return json_error("Too many failure reports.", 429, "rate_limited")
    try:
        payload = await asyncio.wait_for(request.clone(client_max_size=4096).json(), 5)
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
    context.track(
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
        context.schedule_owner_alert(
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
