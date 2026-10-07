"""Privacy-safe formatting for operator notifications and status output."""

from __future__ import annotations

import time
from collections.abc import Iterable

import discord

from .config import MIB
from .domain import BrowserResult, CompressionDiagnostic, UploadJob


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
    results: list[BrowserResult] | None = None,
    diagnostics: list[CompressionDiagnostic] | None = None,
    effective_target: int | None = None,
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
        f"User ID: `{job.user_id}`",
        f"Files: `{file_count}`",
    ]
    if total_bytes:
        lines.append(f"Finished size: `{total_bytes / MIB:.1f} MiB`")
    if (
        results
        and diagnostics
        and len(results) == len(diagnostics)
        and effective_target
        and effective_target > 0
    ):
        lines.append(f"Discord limit: `{job.discord_limit / MIB:.1f} MiB`")
        lines.append(f"Effective target per file: `{effective_target / MIB:.1f} MiB`")
        for index, (result, diagnostic) in enumerate(
            zip(results, diagnostics, strict=True), 1
        ):
            output_mib = len(result.data) / MIB
            input_mib = diagnostic.input_bytes / MIB
            utilization = len(result.data) / effective_target * 100
            if len(results) > 1:
                detail = (
                    "already fit"
                    if diagnostic.copied
                    else (
                        f"{diagnostic.duration_seconds:.1f}s, "
                        f"{diagnostic.video_kbps} kbps"
                    )
                )
                lines.append(
                    f"File {index}: `{input_mib:.1f} → {output_mib:.1f} MiB`"
                    f" · `{utilization:.1f}% target` · {detail}"
                )
                continue
            lines.append(f"Input size: `{input_mib:.1f} MiB`")
            if diagnostic.copied:
                lines.append("Encoding: `skipped (MP4 already fit)`")
            else:
                lines.append(
                    f"Detected duration: `{diagnostic.duration_seconds:.1f} sec`"
                )
                lines.append(
                    f"Calculated video bitrate: `{diagnostic.video_kbps} kbps`"
                )
            lines.append(f"Actual output: `{output_mib:.1f} MiB`")
            lines.append(f"Target utilization: `{utilization:.1f}%`")
    if stage:
        lines.append(f"Stage: `{safe_alert_text(stage, limit=60)}`")
    lines.append(f"Elapsed: `{elapsed_seconds // 60}:{elapsed_seconds % 60:02d}`")
    return "\n".join(lines)


def compression_progress_alert(job: UploadJob, title: str, detail: str) -> str:
    """Report a confirmed session milestone without media or session secrets."""
    return "\n".join(
        (
            f"{title}",
            f"Server: **{safe_alert_text(job.guild_name or 'Unknown server')}**",
            f"Server ID: `{job.guild_id if job.guild_id is not None else 'Unknown'}`",
            f"Username: **{safe_alert_text('@' + (job.username or 'unknown'))}**",
            f"User ID: `{job.user_id}`",
            detail,
        )
    )


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
