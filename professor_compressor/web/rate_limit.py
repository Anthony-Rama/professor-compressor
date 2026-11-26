"""Per-client sliding-window limits for the private upload routes."""

import ipaddress
import time
from collections import deque

from aiohttp import web

from ..config import Settings
from ..observability.metrics import RuntimeMetrics

RATE_LIMIT_WINDOW_SECONDS = 60


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


class RateLimiter:
    """Count recent requests per (bucket, client address) within one window."""

    def __init__(self, settings: Settings, metrics: RuntimeMetrics) -> None:
        self.settings = settings
        self.metrics = metrics
        self.request_times: dict[tuple[str, str], deque[float]] = {}

    def retry_after(
        self, request: web.Request, bucket: str, *, limit: int | None = None
    ) -> int | None:
        """Record the request, or return seconds to wait if the bucket is full."""
        now = time.monotonic()
        key = (bucket, request_ip(request))
        timestamps = self.request_times.setdefault(key, deque())
        while timestamps and now - timestamps[0] >= RATE_LIMIT_WINDOW_SECONDS:
            timestamps.popleft()
        if len(timestamps) >= (limit or self.settings.upload_rate_limit_per_minute):
            self.metrics.increment("rate_limited_requests")
            return max(1, int(RATE_LIMIT_WINDOW_SECONDS - (now - timestamps[0])))
        timestamps.append(now)
        return None

    def prune(self) -> None:
        """Forget requests outside the window and clients with none left."""
        now = time.monotonic()
        for values in self.request_times.values():
            while values and now - values[0] >= RATE_LIMIT_WINDOW_SECONDS:
                values.popleft()
        stale_keys = [key for key, values in self.request_times.items() if not values]
        for key in stale_keys:
            self.request_times.pop(key, None)
