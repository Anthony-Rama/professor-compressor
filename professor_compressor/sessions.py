"""In-memory upload sessions, short-lived delivery receipts, and expiry."""

import secrets
import time

from aiohttp import web

from .alerts import OwnerAlerts
from .config import Settings
from .domain import DeliveryOutcome, JobState, UploadJob
from .notifications import compression_progress_alert
from .rate_limit import RateLimiter
from .tracking import SessionTracker
from .web_responses import json_error

OUTCOME_TTL_SECONDS = 3600
# Expired cooldown entries are dropped after the larger of 4 cooldowns and this.
MIN_COOLDOWN_RETENTION_SECONDS = 300
MIN_RETAINED_RECEIPTS = 1000


class SessionRegistry:
    """Own one runtime's active upload links and delivery receipts."""

    def __init__(
        self,
        settings: Settings,
        *,
        tracker: SessionTracker,
        alerts: OwnerAlerts,
        rate_limiter: RateLimiter,
    ) -> None:
        self.settings = settings
        self.tracker = tracker
        self.alerts = alerts
        self.rate_limiter = rate_limiter
        self.jobs: dict[str, UploadJob] = {}
        self.outcomes: dict[str, DeliveryOutcome] = {}
        # Last /compress time per (guild, user), for the command cooldown.
        self.last_job_at: dict[tuple[int, int], float] = {}
        self.diagnostic_event_counts: dict[str, int] = {}
        # Set during shutdown: links are no longer opened or extended.
        self.draining = False

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

    def receipt(self, token: str) -> DeliveryOutcome | None:
        """Return the unexpired delivery receipt for a link, if any."""
        receipt = self.outcomes.get(token)
        if receipt is not None and time.time() > receipt.expires_at:
            self.outcomes.pop(token, None)
            return None
        return receipt

    def expire_job(self, token: str, job: UploadJob) -> None:
        """Retire an unfinished session once, with its last confirmed activity."""
        if self.jobs.pop(token, None) is not job:
            return
        self.tracker.track(job, "expired")
        if job.browser_failure_reported:
            return
        if job.state is JobState.OPEN:
            detail = "Last confirmed activity: link created; it was never opened."
        elif job.page_left:
            detail = (
                "Last confirmed activity: "
                "browser reported the page was left or reloaded."
            )
        elif job.browser_cancelled:
            detail = (
                "Last confirmed activity: browser reported compression was cancelled."
            )
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
            detail = (
                "Last confirmed activity: link opened; no file selection was reported."
            )
        self.alerts.schedule(
            compression_progress_alert(
                job, "⌛ **Compression session expired without delivery**", detail
            )
        )

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
            if now - started_at
            > max(
                self.settings.user_cooldown_seconds * 4,
                MIN_COOLDOWN_RETENTION_SECONDS,
            )
        ]
        for key in stale_cooldowns:
            self.last_job_at.pop(key, None)
        self.rate_limiter.prune()

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
        # Bound receipt memory independently from active sessions. No media is
        # retained.
        while len(self.outcomes) > max(
            MIN_RETAINED_RECEIPTS, self.settings.max_active_jobs * 4
        ):
            self.outcomes.pop(next(iter(self.outcomes)))

    def session_access(
        self, request: web.Request
    ) -> tuple[UploadJob | None, web.Response | None]:
        """Authenticate the page secret; a valid receipt answers on the job's behalf."""
        token = request.match_info["token"]
        job = self.active_job(token)
        receipt = self.receipt(token)
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
