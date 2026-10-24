"""Domain models shared by the Discord and HTTP boundaries."""

from __future__ import annotations

import asyncio
import time
import uuid
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
    browser_secret: str | None = field(default=None, repr=False)
    claimed_at: float | None = None
    browser_failure_reported: bool = False
    guild_id: int | None = None
    guild_name: str = "Unknown server"
    username: str = ""
    created_at: float = field(default_factory=time.time)
    processing_until: float = 0
    selected_count: int = 0
    compression_started: bool = False
    relay_upload_started: bool = False
    browser_cancelled: bool = False
    browser_cancel_reported: bool = False
    page_left: bool = False
    page_left_reported: bool = False
    page_left_at: float | None = None
    diagnostic_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    active_run_id: str = ""

    @property
    def diagnostic_reference(self) -> str:
        return "PC-" + self.diagnostic_id[:12].upper()


@dataclass(frozen=True, slots=True)
class BrowserResult:
    name: str
    data: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class CompressionDiagnostic:
    input_bytes: int
    duration_seconds: float | None
    video_kbps: int | None
    copied: bool
    input_format: str | None = None
    encoding_seconds: float | None = None
    telemetry: dict = field(default_factory=dict)


@dataclass(slots=True)
class DeliveryRequest:
    job: UploadJob
    results: list[BrowserResult]
    completed: asyncio.Future[str]
    caption: str = ""
    diagnostics: list[CompressionDiagnostic] = field(default_factory=list)
    received_seconds: float = 0
    queued_at: float = field(default_factory=time.monotonic)
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    """Short-lived receipt; never retains media or interaction credentials."""

    secret: str = field(repr=False)
    browser_secret: str = field(repr=False)
    expires_at: float
    status: int
    body: dict[str, object]
    diagnostic_id: str = ""
    run_id: str = ""
    is_test: bool = False
