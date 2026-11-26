"""Optional dsc.sh server-count reporting, isolated from compression work."""

import asyncio
import logging

import discord
from aiohttp import ClientSession, ClientTimeout

from .background_tasks import BackgroundTasks
from .branding import USER_AGENT
from .config import Settings

# Log name is part of the operations contract; this code came from application.py.
logger = logging.getLogger("professor_compressor.application")

DSC_TIMEOUT_SECONDS = 10


class DscStats:
    """Publish the connected server count on changes and on a fixed interval."""

    def __init__(
        self, settings: Settings, client: discord.Client, tasks: BackgroundTasks
    ) -> None:
        self.settings = settings
        self.client = client
        self.tasks = tasks

    async def report(self) -> bool:
        """Publish the current server count to dsc.sh without blocking bot work."""
        if not self.settings.dsc_api_token or self.client.user is None:
            return False
        try:
            timeout = ClientTimeout(total=DSC_TIMEOUT_SECONDS)
            async with ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"https://dsc.sh/api/bots/{self.client.user.id}/stats",
                    headers={
                        "Authorization": self.settings.dsc_api_token,
                        "Content-Type": "application/json",
                        "User-Agent": USER_AGENT,
                    },
                    json={"server_count": len(self.client.guilds)},
                ) as response:
                    response.raise_for_status()
            logger.info("Published dsc.sh server count: %d", len(self.client.guilds))
            return True
        except Exception as error:
            logger.warning("dsc.sh statistics update failed (%s)", type(error).__name__)
            return False

    def schedule_update(self) -> None:
        """Schedule a best-effort dsc.sh update after a server-count change."""
        if not self.settings.dsc_api_token:
            return
        self.tasks.spawn(self.report())

    async def run(self) -> None:
        """Refresh dsc.sh periodically so the public listing stays current."""
        while not self.client.is_closed():
            await self.report()
            await asyncio.sleep(self.settings.dsc_stats_interval_seconds)
