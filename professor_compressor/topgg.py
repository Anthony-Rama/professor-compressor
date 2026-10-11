"""Optional aggregate Top.gg statistics, isolated from compression work."""

import asyncio
import logging
import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from aiohttp import ClientSession, ClientTimeout

logger = logging.getLogger(__name__)
METRICS_URL = "https://top.gg/api/v1/projects/@me/metrics"


def retry_delay(value: str | None) -> float:
    """Honor numeric or HTTP-date Retry-After, with a conservative hourly floor."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = (date - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 3600
    return max(3600, seconds) if math.isfinite(seconds) else 3600


class TopGGStats:
    """One serialized worker; guild bursts coalesce and cannot bypass backoff."""

    def __init__(self, client, token: str, interval: int = 3600):
        self.client = client
        self.token = token
        self.interval = max(1800, interval)
        self.changed = asyncio.Event()

    def notify_change(self) -> None:
        if self.token:
            self.changed.set()

    async def publish(self) -> float:
        """Return the minimum delay before another attempt; never log secrets."""
        if not self.token or not self.client.is_ready() or self.client.user is None:
            return 60
        count = len(self.client.guilds)
        try:
            async with ClientSession(timeout=ClientTimeout(total=10)) as session:
                async with session.patch(
                    METRICS_URL,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"server_count": count},
                    allow_redirects=False,
                ) as response:
                    if response.status == 429:
                        delay = retry_delay(response.headers.get("Retry-After"))
                        logger.warning(
                            "Top.gg rate limited; retry in %.0f seconds", delay
                        )
                        return delay
                    if not 200 <= response.status < 300:
                        logger.warning(
                            "Top.gg stats update failed (HTTP=%d)", response.status
                        )
                        return max(3600, self.interval)
            logger.info("Published Top.gg server count: %d", count)
            return 60
        except Exception as error:
            logger.warning("Top.gg stats update failed (%s)", type(error).__name__)
            return max(3600, self.interval)

    async def run(self) -> None:
        if not self.token:
            return
        while not self.client.is_closed():
            await self.client.wait_until_ready()
            self.changed.clear()
            cooldown = await self.publish()
            attempted_at = time.monotonic()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=self.interval)
            except TimeoutError:
                pass
            # A join/leave wakes the loop, but never evades the minimum spacing
            # or an API backoff. Changes during this wait use the latest count.
            await asyncio.sleep(max(0, cooldown - (time.monotonic() - attempted_at)))
