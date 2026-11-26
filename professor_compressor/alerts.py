"""Best-effort private operator alerts through a Discord webhook."""

import logging

from aiohttp import ClientSession, ClientTimeout

from .background_tasks import BackgroundTasks
from .branding import USER_AGENT
from .config import Settings

# Extracted from the application module; keep its logger name for log continuity.
logger = logging.getLogger("professor_compressor.application")

ALERT_TIMEOUT_SECONDS = 5


class OwnerAlerts:
    """Deliver alerts in the background; failures never reach the user workflow."""

    def __init__(self, settings: Settings, tasks: BackgroundTasks) -> None:
        self.settings = settings
        self.tasks = tasks

    async def send(self, message: str) -> None:
        """Send a best-effort private alert without affecting the user workflow."""
        if not self.settings.alert_webhook_url:
            return
        try:
            timeout = ClientTimeout(total=ALERT_TIMEOUT_SECONDS)
            async with ClientSession(timeout=timeout) as session:
                async with session.post(
                    self.settings.alert_webhook_url,
                    headers={"User-Agent": USER_AGENT},
                    json={
                        "content": message,
                        "allowed_mentions": {"parse": []},
                    },
                ) as response:
                    response.raise_for_status()
        except Exception as error:
            logger.warning("Owner notification failed (%s)", type(error).__name__)

    def schedule(self, message: str) -> None:
        """Run an alert in the background so Discord commands remain responsive."""
        if not self.settings.alert_webhook_url:
            return
        self.tasks.spawn(self.send(message))
