"""Domain models shared by the Discord and HTTP boundaries."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum

import discord


class JobState(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    UPLOADING = "uploading"
    QUEUED = "queued"
    DONE = "done"


@dataclass(slots=True)
class UploadJob:
    token: str = field(repr=False)
    user_id: int
    channel_id: int
    expires_at: float
    discord_limit: int
    interaction: discord.Interaction = field(repr=False)
    state: JobState = JobState.OPEN
    claim_secret: str | None = field(default=None, repr=False)
    claimed_at: float | None = None
    guild_id: int | None = None
    guild_name: str = "Unknown server"
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class BrowserResult:
    name: str
    data: bytes = field(repr=False)


@dataclass(slots=True)
class DeliveryRequest:
    job: UploadJob
    results: list[BrowserResult]
    completed: asyncio.Future[str]
