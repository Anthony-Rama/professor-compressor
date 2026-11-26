"""Optional per-session diagnostic events for the operator dashboard."""

import logging

from .config import Settings
from .domain import UploadJob
from .observability import EventStore

# Extracted from the application module; keep its logger name for log continuity.
logger = logging.getLogger("professor_compressor.application")


class SessionTracker:
    """Attach session identity to diagnostic events; never raises."""

    def __init__(self, settings: Settings, event_store: EventStore) -> None:
        self.settings = settings
        self.event_store = event_store

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
                revision=self.settings.app_revision,
                user_key=self.event_store.pseudonym("user", job.user_id),
                guild_key=self.event_store.pseudonym("guild", job.guild_id),
                is_test=job.guild_id in self.settings.diagnostic_test_guild_ids,
                event_id=event_id,
            )
        except Exception:
            logger.warning("Optional session diagnostic could not be recorded")
