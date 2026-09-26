"""Small, dependency-free aggregate runtime metrics.

These counters intentionally contain no user, guild, channel, filename, IP, or
file-content data. They reset whenever the process restarts.
"""

from __future__ import annotations

import time
from collections import Counter
from threading import Lock


class RuntimeMetrics:
    """Store privacy-safe process-lifetime counters."""

    def __init__(self) -> None:
        self._started_at = time.monotonic()
        self._counters: Counter[str] = Counter()
        self._lock = Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("Metric increments cannot be negative.")
        with self._lock:
            self._counters[name] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            counters = dict(self._counters)
        return {
            "uptime_seconds": max(0, int(time.monotonic() - self._started_at)),
            "sessions_created": counters.get("sessions_created", 0),
            "sessions_opened": counters.get("sessions_opened", 0),
            "batches_queued": counters.get("batches_queued", 0),
            "files_queued": counters.get("files_queued", 0),
            "bytes_queued": counters.get("bytes_queued", 0),
            "deliveries_succeeded": counters.get("deliveries_succeeded", 0),
            "files_delivered": counters.get("files_delivered", 0),
            "bytes_delivered": counters.get("bytes_delivered", 0),
            "deliveries_failed": counters.get("deliveries_failed", 0),
            "rate_limited_requests": counters.get("rate_limited_requests", 0),
        }
