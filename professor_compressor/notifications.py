"""Privacy-safe formatting for operator notifications and status output."""

from __future__ import annotations

import time
from collections.abc import Iterable

import discord

from .config import MIB
from .domain import UploadJob


def safe_alert_text(value: str, limit: int = 100) -> str:
    """Escape untrusted Discord names before placing them in Markdown."""
    escaped = discord.utils.escape_markdown(value.replace("`", "'"))
    return discord.utils.escape_mentions(escaped)[:limit]


def guild_alert_message(
    action: str,
    guild: discord.Guild,
    *,
    server_count: int,
) -> str:
    member_count = guild.member_count if guild.member_count is not None else "Unknown"
    return (
        f"{action}\n"
        f"Server: **{safe_alert_text(guild.name)}**\n"
        f"Server ID: `{guild.id}`\n"
        f"Members: `{member_count}`\n"
        f"Connected servers: `{server_count}`"
    )


def compression_outcome_alert(
    job: UploadJob,
    *,
    succeeded: bool,
    file_count: int = 0,
    total_bytes: int = 0,
    stage: str | None = None,
    elapsed_seconds: int | None = None,
) -> str:
    """Build an outcome alert without filenames or user mentions."""
    if elapsed_seconds is None:
        started_at = job.claimed_at or job.created_at
        elapsed_seconds = max(0, int(time.time() - started_at))
    outcome = (
        "✅ **Compression delivered successfully**"
        if succeeded
        else "❌ **Compression failed**"
    )
    lines = [
        outcome,
        f"Server: **{safe_alert_text(job.guild_name or 'Unknown server')}**",
        f"Server ID: `{job.guild_id if job.guild_id is not None else 'Unknown'}`",
        f"Username: **{safe_alert_text('@' + (job.username or 'unknown'))}**",
        f"Files: `{file_count}`",
    ]
    if total_bytes:
        lines.append(f"Finished size: `{total_bytes / MIB:.1f} MiB`")
    if stage:
        lines.append(f"Stage: `{safe_alert_text(stage, limit=60)}`")
    lines.append(f"Elapsed: `{elapsed_seconds // 60}:{elapsed_seconds % 60:02d}`")
    return "\n".join(lines)


def botstats_report(
    guilds: Iterable[discord.Guild],
    snapshot: dict[str, int],
) -> str:
    sorted_guilds = sorted(guilds, key=lambda guild: guild.name.casefold())
    lines = [
        "**Professor Compressor status**",
        f"Connected servers: **{len(sorted_guilds)}**",
        f"Sessions since restart: **{snapshot['sessions_created']}**",
        f"Successful deliveries since restart: **{snapshot['deliveries_succeeded']}**",
        "",
        "**Live server list**",
    ]
    for guild in sorted_guilds:
        member_count = (
            guild.member_count if guild.member_count is not None else "Unknown"
        )
        lines.append(
            f"- {safe_alert_text(guild.name)} | ID: `{guild.id}` | "
            f"Members: `{member_count}`"
        )
    return "\n".join(lines)
