"""Typed application configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from dotenv import load_dotenv

MIB = 1024 * 1024


def _integer(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer.") from error
    if value < minimum:
        raise RuntimeError(f"{name} must be at least {minimum}.")
    return value


def _guild_ids(name: str) -> frozenset[int]:
    raw = os.getenv(name, os.getenv("ALLOWED_GUILD_ID", "")).strip()
    if not raw:
        return frozenset()
    try:
        return frozenset(
            int(value.strip()) for value in raw.split(",") if value.strip()
        )
    except ValueError as error:
        raise RuntimeError(
            f"{name} must contain comma-separated Discord server IDs."
        ) from error


def _optional_snowflake(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a Discord server ID.") from error
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive Discord server ID.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings.

    Secret fields are excluded from ``repr`` so accidental diagnostic logging
    cannot expose credentials.
    """

    discord_token: str = field(repr=False)
    alert_webhook_url: str = field(default="", repr=False)
    dsc_api_token: str = field(default="", repr=False)
    dsc_stats_interval_seconds: int = 3600
    botstats_guild_id: int | None = None
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    public_base_url: str = "http://127.0.0.1:8080"
    job_ttl_seconds: int = 600
    active_session_ttl_seconds: int = 1800
    user_cooldown_seconds: int = 15
    max_active_jobs: int = 250
    max_clips: int = 10
    bot_base_upload_bytes: int = 20 * MIB
    max_result_total_bytes: int = 220 * MIB
    upload_rate_limit_per_minute: int = 8
    max_concurrent_uploads: int = 2
    delivery_queue_size: int = 8
    delivery_workers: int = 2
    max_delivery_buffer_bytes: int = 400 * MIB
    allowed_guild_ids: frozenset[int] = frozenset()

    @classmethod
    def from_environment(cls, *, require_token: bool = True) -> Settings:
        load_dotenv()
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if require_token and not token:
            raise RuntimeError("DISCORD_TOKEN is missing from the environment.")

        web_host = os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1"
        web_port = _integer("WEB_PORT", 8080, minimum=1)
        if web_port > 65_535:
            raise RuntimeError("WEB_PORT must be at most 65535.")
        public_base_url = (
            os.getenv("PUBLIC_BASE_URL", f"http://127.0.0.1:{web_port}")
            .strip()
            .rstrip("/")
        )
        parsed_url = urlparse(public_base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise RuntimeError("PUBLIC_BASE_URL must be an absolute HTTP(S) URL.")

        return cls(
            discord_token=token,
            alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", "").strip(),
            dsc_api_token=os.getenv("DSC_API_TOKEN", "").strip(),
            dsc_stats_interval_seconds=max(
                30 * 60,
                _integer("DSC_STATS_INTERVAL_MINUTES", 60, minimum=1) * 60,
            ),
            botstats_guild_id=_optional_snowflake("BOTSTATS_GUILD_ID"),
            web_host=web_host,
            web_port=web_port,
            public_base_url=public_base_url,
            job_ttl_seconds=_integer("JOB_TTL_MINUTES", 10, minimum=1) * 60,
            active_session_ttl_seconds=(
                _integer("ACTIVE_SESSION_TTL_MINUTES", 30, minimum=1) * 60
            ),
            user_cooldown_seconds=_integer("USER_COOLDOWN_SECONDS", 15),
            max_active_jobs=_integer("MAX_ACTIVE_JOBS", 250, minimum=1),
            bot_base_upload_bytes=_integer("BOT_BASE_UPLOAD_MIB", 20, minimum=1) * MIB,
            max_result_total_bytes=(
                _integer("MAX_RESULT_TOTAL_MIB", 220, minimum=1) * MIB
            ),
            upload_rate_limit_per_minute=_integer(
                "UPLOAD_RATE_LIMIT_PER_MINUTE", 8, minimum=1
            ),
            max_concurrent_uploads=_integer("MAX_CONCURRENT_UPLOADS", 2, minimum=1),
            delivery_queue_size=_integer("DELIVERY_QUEUE_SIZE", 8, minimum=1),
            delivery_workers=_integer("DELIVERY_WORKERS", 2, minimum=1),
            max_delivery_buffer_bytes=(
                _integer("MAX_DELIVERY_BUFFER_MIB", 400, minimum=1) * MIB
            ),
            allowed_guild_ids=_guild_ids("ALLOWED_GUILD_IDS"),
        )
