import asyncio
import html
import io
import ipaddress
import logging
import os
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import discord
from aiohttp import ClientSession, ClientTimeout, web
from discord import app_commands

from .config import MIB, Settings
from .domain import BrowserResult, DeliveryRequest, JobState, UploadJob
from .legal_pages import privacy_policy_html, terms_of_service_html
from .media_validation import valid_mp4_signature
from .metrics import RuntimeMetrics
from .notifications import (
    botstats_report,
    compression_outcome_alert,
    guild_alert_message,
    safe_alert_text,
)
from .web_ui import browser_compressor

logger = logging.getLogger(__name__)
settings = Settings.from_environment(require_token=False)

# Local aliases keep the request-handling code concise. Configuration parsing,
# validation, defaults, and secret handling live exclusively in config.py.
ALERT_WEBHOOK_URL = settings.alert_webhook_url
DSC_API_TOKEN = settings.dsc_api_token
DSC_STATS_INTERVAL_SECONDS = settings.dsc_stats_interval_seconds
BOTSTATS_GUILD_ID = settings.botstats_guild_id
WEB_HOST = settings.web_host
WEB_PORT = settings.web_port
PUBLIC_BASE_URL = settings.public_base_url
JOB_TTL_SECONDS = settings.job_ttl_seconds
ACTIVE_SESSION_TTL_SECONDS = settings.active_session_ttl_seconds
USER_COOLDOWN_SECONDS = settings.user_cooldown_seconds
MAX_ACTIVE_JOBS = settings.max_active_jobs
MAX_CLIPS = settings.max_clips
MAX_RESULT_TOTAL_BYTES = settings.max_result_total_bytes
UPLOAD_RATE_LIMIT_PER_MINUTE = settings.upload_rate_limit_per_minute
MAX_CONCURRENT_UPLOADS = settings.max_concurrent_uploads
DELIVERY_QUEUE_SIZE = settings.delivery_queue_size
DELIVERY_WORKERS = settings.delivery_workers
MAX_DELIVERY_BUFFER_BYTES = settings.max_delivery_buffer_bytes
ALLOWED_GUILD_IDS = settings.allowed_guild_ids


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
jobs: dict[str, UploadJob] = {}
background_tasks: set[asyncio.Task[Any]] = set()
last_job_at: dict[tuple[int, int], float] = {}
web_runner: web.AppRunner | None = None
cleanup_task: asyncio.Task[None] | None = None
dsc_stats_task: asyncio.Task[None] | None = None
delivery_queue: asyncio.Queue[DeliveryRequest] | None = None
delivery_workers: list[asyncio.Task[None]] = []
request_times: dict[tuple[str, str], deque[float]] = {}
active_relay_uploads = 0
delivery_bytes_held = 0
commands_synced = False
application_operator_ids: frozenset[int] | None = None
effective_allowed_guild_ids: frozenset[int] = ALLOWED_GUILD_IDS
metrics = RuntimeMetrics()


async def send_owner_alert(message: str) -> None:
    """Send a best-effort private alert without affecting the user workflow."""
    if not ALERT_WEBHOOK_URL:
        return
    try:
        timeout = ClientTimeout(total=5)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                ALERT_WEBHOOK_URL,
                headers={
                    "User-Agent": (
                        "ProfessorCompressor/1.0 "
                        "(+https://github.com/Anthony-Rama/discord-bot)"
                    )
                },
                json={
                    "content": message,
                    "allowed_mentions": {"parse": []},
                },
            ) as response:
                response.raise_for_status()
    except Exception as error:
        logger.warning("Owner notification failed (%s)", type(error).__name__)


def schedule_owner_alert(message: str) -> None:
    """Run an alert in the background so Discord commands remain responsive."""
    if not ALERT_WEBHOOK_URL:
        return
    task = asyncio.create_task(send_owner_alert(message))
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


async def report_dsc_stats() -> bool:
    """Publish the current server count to dsc.sh without blocking bot work."""
    if not DSC_API_TOKEN or client.user is None:
        return False
    try:
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                f"https://dsc.sh/api/bots/{client.user.id}/stats",
                headers={
                    "Authorization": DSC_API_TOKEN,
                    "Content-Type": "application/json",
                    "User-Agent": (
                        "ProfessorCompressor/1.0 "
                        "(+https://github.com/Anthony-Rama/discord-bot)"
                    ),
                },
                json={"server_count": len(client.guilds)},
            ) as response:
                response.raise_for_status()
        logger.info("Published dsc.sh server count: %d", len(client.guilds))
        return True
    except Exception as error:
        logger.warning("dsc.sh statistics update failed (%s)", type(error).__name__)
        return False


def schedule_dsc_stats_update() -> None:
    """Schedule a best-effort dsc.sh update after a server-count change."""
    if not DSC_API_TOKEN:
        return
    task = asyncio.create_task(report_dsc_stats())
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


async def dsc_stats_loop() -> None:
    """Refresh dsc.sh periodically so the public listing stays current."""
    while not client.is_closed():
        await report_dsc_stats()
        await asyncio.sleep(DSC_STATS_INTERVAL_SECONDS)


def interaction_guild_name(interaction: discord.Interaction) -> str:
    """Return a usable guild name even when Discord sends partial guild data."""
    guild = interaction.guild
    if guild is None and interaction.guild_id is not None:
        guild = client.get_guild(interaction.guild_id)
    guild_name = getattr(guild, "name", "").strip()
    return guild_name or "Unknown server"


async def get_application_operator_ids() -> frozenset[int]:
    """Return the application owner and team members allowed to view status."""
    global application_operator_ids
    if application_operator_ids is None:
        application = await client.application_info()
        operator_ids = {application.owner.id}
        if application.team is not None:
            operator_ids.update(member.id for member in application.team.members)
        application_operator_ids = frozenset(operator_ids)
    return application_operator_ids


def compression_target(discord_limit: int) -> int:
    """Reserve two percent for delivery while preserving output quality."""
    return max(1 * MIB, int(discord_limit * 0.98))


@web.middleware
async def browser_security_headers(
    request: web.Request,
    handler: Any,
) -> web.StreamResponse:
    response = await handler(request)
    # SharedArrayBuffer is available only in a cross-origin-isolated page.
    # FFmpeg's multithreaded WebAssembly core uses it to share memory between
    # encoder worker threads.
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), screen-wake-lock=(self)"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self' blob:; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "media-src 'self' blob:; "
        "object-src 'none'; "
        "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "worker-src 'self' blob:"
    )
    response.headers["X-Frame-Options"] = "DENY"
    return response


def page(title: str, body: str) -> web.Response:
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui,
             -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; min-height: 100vh; display: flex; justify-content: center;
            align-items: flex-start;
            padding: 24px; background:
            radial-gradient(circle at 50% 0%, #1c2941 0, #0d1422 42%, #090e18 100%);
            color: #f8fafc; }}
    main {{ width: min(560px, 100%); min-width: 0; margin-block: auto; padding: 36px;
            border: 1px solid #2b3a50; border-radius: 22px;
            background: rgba(23, 33, 49, .96);
            box-shadow: 0 24px 70px rgba(0, 0, 0, .38); }}
    .brand {{ width: 78px; height: 78px; display: block; object-fit: cover;
              margin-bottom: 20px; border: 1px solid #5265ff;
              border-radius: 22px; box-shadow: 0 10px 30px rgba(34, 91, 255, .3); }}
    h1 {{ margin: 0; font-size: clamp(28px, 6vw, 38px); line-height: 1.12;
          letter-spacing: -.035em; }}
    p {{ line-height: 1.55; color: #b8c2d1; }}
    .intro {{ margin: 14px 0 22px; font-size: 16px; }}
    .intro strong {{ color: #e6e9ff; }}
    .stay-open {{ display: flex; gap: 12px; align-items: flex-start;
                  margin-bottom: 24px; padding: 14px 16px;
                  border: 1px solid #3b4860; border-radius: 13px;
                  background: #202c40; color: #c8d1df; font-size: 14px;
                  line-height: 1.45; }}
    .stay-open > span:first-child {{ font-size: 20px; line-height: 1.2; }}
    .stay-open strong {{ color: #f8fafc; }}
    .performance-help {{ margin: -10px 0 24px; padding: 12px 14px;
                         border: 1px solid #344158; border-radius: 12px;
                         background: #151f30; color: #aeb9c9; font-size: 13px; }}
    .performance-help summary {{ color: #dbe2ed; font-weight: 700; cursor: pointer; }}
    .performance-help p {{ margin: 10px 0 0; font-size: 13px; }}
    .performance-help code {{ color: #cbd2ff; overflow-wrap: anywhere; }}
    .file-input {{ position: absolute; width: 1px; height: 1px; padding: 0;
                   margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0);
                   white-space: nowrap; border: 0; }}
    .file-picker {{ display: flex; gap: 14px; align-items: center; padding: 18px;
                    border: 1px dashed #59677d; border-radius: 14px;
                    background: #111a2a; cursor: pointer; transition: .18s ease; }}
    .file-picker:hover {{ border-color: #8991ff; background: #151f33; }}
    .file-picker.drag-over {{ border-color: #a5b4fc; background: #26345b;
      outline: 3px solid rgba(124, 131, 255, .35); }}
    .file-input:focus + .file-picker {{ outline: 3px solid rgba(124, 131, 255, .28);
                                       outline-offset: 2px; }}
    .picker-plus {{ width: 38px; height: 38px; display: grid; place-items: center;
                    flex: 0 0 auto; border-radius: 10px; background: #283452;
                    color: #aeb4ff; font-size: 26px; line-height: 1; }}
    .file-picker strong {{ display: block; color: #f8fafc; font-size: 16px; }}
    .file-picker small {{ display: block; margin-top: 3px; color: #8e9bad;
                         font-size: 13px; }}
    .selection {{ min-height: 22px; margin: 10px 2px 0; font-size: 13px;
                  color: #93a0b2; }}
    button {{ margin-top: 16px; width: 100%; padding: 14px 18px; border: 0;
              border-radius: 12px; background: linear-gradient(135deg, #6873ff, #5865f2);
              color: white; font-size: 15px; font-weight: 750; cursor: pointer;
              box-shadow: 0 10px 28px rgba(88, 101, 242, .24); }}
    button:hover:not(:disabled) {{ filter: brightness(1.08); }}
    button:disabled {{ opacity: .45; cursor: not-allowed; box-shadow: none; }}
    .work {{ margin-top: 20px; }}
    progress {{ width: 100%; height: 10px; overflow: hidden;
                border: 0; border-radius: 999px; accent-color: #6873ff; }}
    progress::-webkit-progress-bar {{ background: #101827; border-radius: 999px; }}
    progress::-webkit-progress-value {{ background: #6873ff; border-radius: 999px; }}
    #status {{ min-height: 22px; margin: 9px 0 0; font-size: 14px; }}
    .session-note {{ display: flex; justify-content: space-between; gap: 12px;
                     margin: 10px 2px 20px; color: #8290a4; font-size: 12px; }}
    .file-list {{ display: grid; grid-template-columns: minmax(0, 1fr);
                  min-width: 0; gap: 10px; margin: 16px 0; }}
    .file-card {{ width: 100%; min-width: 0; padding: 13px 14px;
                  border: 1px solid #354258;
                  border-radius: 12px; background: #131d2d; }}
    .file-head, .file-meta, .run-meta {{ display: flex; align-items: center;
                                        justify-content: space-between; gap: 12px; }}
    .file-head {{ min-width: 0; }}
    .file-name {{ display: block; flex: 1 1 auto; min-width: 0;
                  overflow: hidden; text-overflow: ellipsis;
                  white-space: nowrap; font-size: 14px; font-weight: 700; }}
    .file-status {{ flex: 0 0 auto; color: #aab5c5; font-size: 12px; }}
    .file-card progress {{ height: 7px; margin-top: 10px; }}
    .file-meta {{ margin-top: 7px; color: #7f8da1; font-size: 11px; }}
    .phase-panel {{ padding: 16px; border: 1px solid #3a4861; border-radius: 14px;
                    background: #111a2a; }}
    .background-warning {{ margin-bottom: 12px; padding: 12px 14px;
                           border: 1px solid #8a652c; border-radius: 11px;
                           background: #332714; color: #f5d58f; font-size: 13px;
                           line-height: 1.45; }}
    .phase-title {{ margin: 0; color: #f8fafc; font-size: 16px; }}
    .phase-copy {{ margin: 5px 0 12px; color: #93a0b2; font-size: 13px; }}
    .phase-track {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px;
                    margin-bottom: 14px; }}
    .phase-step {{ padding: 8px 10px; border-radius: 9px; background: #1b2739;
                   color: #718096; text-align: center; font-size: 12px;
                   font-weight: 700; }}
    .phase-step.active {{ background: #29376a; color: #dfe3ff; }}
    .phase-step.done {{ background: #173b35; color: #8ce5c8; }}
    .run-meta {{ margin-top: 10px; color: #9aa7b9; font-size: 12px; }}
    .controls {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; }}
    .controls button {{ margin-top: 16px; }}
    .secondary {{ width: auto; background: #303c50; box-shadow: none; }}
    .message {{ margin-top: 14px; padding: 13px 14px; border-radius: 11px;
                font-size: 13px; line-height: 1.45; }}
    .message.error {{ border: 1px solid #733648; background: #321b27; }}
    .message.success {{ border: 1px solid #276052; background: #16362f;
                        color: #9ce8d2; }}
    .privacy {{ margin: 18px 0 0; text-align: center; color: #8794a7;
                font-size: 13px; }}
    .privacy a {{ color: #aeb4ff; }}
    .error {{ color: #fda4af !important; }}
    [hidden] {{ display: none !important; }}
    @media (max-width: 560px) {{
      body {{ padding: 14px; }}
      main {{ padding: 26px 22px; border-radius: 18px; }}
      .brand {{ margin-bottom: 18px; }}
    }}
  </style>
</head>
<body><main>{body}</main></body>
</html>"""
    return web.Response(
        text=document,
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def active_job(token: str) -> UploadJob | None:
    job = jobs.get(token)
    if job is None or job.state is JobState.DONE or time.time() > job.expires_at:
        jobs.pop(token, None)
        return None
    return job


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


def rate_limit_retry_after(request: web.Request, bucket: str) -> int | None:
    now = time.monotonic()
    key = (bucket, request_ip(request))
    timestamps = request_times.setdefault(key, deque())
    while timestamps and now - timestamps[0] >= 60:
        timestamps.popleft()
    if len(timestamps) >= UPLOAD_RATE_LIMIT_PER_MINUTE:
        metrics.increment("rate_limited_requests")
        return max(1, int(60 - (now - timestamps[0])))
    timestamps.append(now)
    return None


def json_error(message: str, status: int, code: str, **extra: Any) -> web.Response:
    return web.json_response(
        {"ok": False, "error": message, "code": code, **extra},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def discard_expired_jobs() -> None:
    now = time.time()
    expired_tokens = [
        token
        for token, job in jobs.items()
        if job.state is JobState.DONE or now > job.expires_at
    ]
    for token in expired_tokens:
        jobs.pop(token, None)

    stale_cooldowns = [
        key
        for key, started_at in last_job_at.items()
        if now - started_at > max(USER_COOLDOWN_SECONDS * 4, 300)
    ]
    for key in stale_cooldowns:
        last_job_at.pop(key, None)

    monotonic_now = time.monotonic()
    for values in request_times.values():
        while values and monotonic_now - values[0] >= 60:
            values.popleft()
    stale_rate_keys = [key for key, values in request_times.items() if not values]
    for key in stale_rate_keys:
        request_times.pop(key, None)


async def expire_jobs_loop() -> None:
    try:
        while not client.is_closed():
            discard_expired_jobs()
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        pass


async def health(request: web.Request) -> web.Response:
    del request
    return web.json_response(
        {
            "status": "ok",
            "discord_ready": client.is_ready(),
            "active_upload_links": len(jobs),
            "active_relay_uploads": active_relay_uploads,
            "delivery_queue_depth": delivery_queue.qsize() if delivery_queue else 0,
            "delivery_queue_capacity": DELIVERY_QUEUE_SIZE,
            "delivery_buffer_mib": round(delivery_bytes_held / MIB, 1),
            "delivery_buffer_limit_mib": round(MAX_DELIVERY_BUFFER_BYTES / MIB, 1),
        }
    )


async def aggregate_metrics(request: web.Request) -> web.Response:
    del request
    return web.json_response(
        {
            "privacy": "aggregate_process_counters_only",
            **metrics.snapshot(),
        },
        headers={"Cache-Control": "no-store"},
    )


def legal_response(document: str) -> web.Response:
    return web.Response(
        text=document,
        content_type="text/html",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def privacy_policy(request: web.Request) -> web.Response:
    del request
    return legal_response(privacy_policy_html())


async def terms_of_service(request: web.Request) -> web.Response:
    del request
    return legal_response(terms_of_service_html())


async def upload_form(request: web.Request) -> web.Response:
    retry_after = rate_limit_retry_after(request, "open")
    if retry_after is not None:
        return page(
            "Please wait",
            "<h1>Too many requests</h1>"
            f"<p>Please wait {retry_after} seconds, then run "
            "<strong>/compress</strong> again.</p>",
        )
    job = active_job(request.match_info["token"])
    if job is None or job.state is not JobState.OPEN:
        return page(
            "Link expired",
            "<h1>Upload link unavailable</h1>"
            "<p>This one-use link expired or was already opened. Run "
            "<strong>/compress</strong> again.</p>",
        )

    job.claim_secret = secrets.token_urlsafe(32)
    job.claimed_at = time.time()
    job.expires_at = time.time() + ACTIVE_SESSION_TTL_SECONDS
    job.state = JobState.CLAIMED
    metrics.increment("sessions_opened")
    # Keep a small delivery reserve while letting the browser use nearly all of
    # the attachment allowance. The encoder applies its own muxing reserve.
    safe_target = compression_target(job.discord_limit)
    return page(
        "Professor Compressor",
        browser_compressor(
            MAX_CLIPS,
            safe_target,
            min(MAX_RESULT_TOTAL_BYTES, MAX_DELIVERY_BUFFER_BYTES),
            job.claim_secret,
            max(1, int(job.expires_at - time.time())),
        ),
    )


def safe_result_name(name: str | None, number: int) -> str:
    raw = (name or f"clip-{number}.mp4").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character for character in raw if character.isalnum() or character in " ._-"
    ).strip(" .")
    stem = cleaned[:-4] if cleaned.lower().endswith(".mp4") else cleaned
    stem = stem[:120].rstrip(" .")
    return f"{stem or f'clip-{number}'}.mp4"


async def receive_results(request: web.Request) -> web.Response:
    global active_relay_uploads, delivery_bytes_held
    token = request.match_info["token"]
    job = active_job(token)
    if job is None:
        return json_error(
            "This session expired. Run /compress in Discord for a new link.",
            410,
            "session_expired",
        )
    supplied_secret = request.headers.get("X-Upload-Session", "")
    if not job.claim_secret or not secrets.compare_digest(
        supplied_secret.encode(), job.claim_secret.encode()
    ):
        return json_error(
            "This browser is not authorized to use the upload session.",
            403,
            "session_invalid",
        )
    if job.state in {JobState.UPLOADING, JobState.QUEUED}:
        return json_error(
            "This session already has an upload in progress.",
            409,
            "upload_in_progress",
        )
    if job.state is not JobState.CLAIMED:
        return json_error(
            "This one-use session is no longer available.",
            410,
            "session_used",
        )
    retry_after = rate_limit_retry_after(request, "upload")
    if retry_after is not None:
        return json_error(
            f"Too many upload attempts. Try again in {retry_after} seconds.",
            429,
            "rate_limited",
            retry_after=retry_after,
        )
    if active_relay_uploads >= MAX_CONCURRENT_UPLOADS:
        return json_error(
            "The relay is receiving its maximum number of uploads. "
            "It will retry automatically.",
            503,
            "relay_busy",
            retry_after=3,
        )
    if delivery_queue is None or delivery_queue.full():
        return json_error(
            "The Discord delivery queue is full. It will retry automatically.",
            503,
            "queue_full",
            retry_after=5,
        )

    results: list[BrowserResult] = []
    total_size = 0
    upload_slot_held = True
    job.state = JobState.UPLOADING
    active_relay_uploads += 1
    try:
        if not request.content_type.startswith("multipart/"):
            raise ValueError("The upload format was invalid. Please try again.")
        reader = await request.multipart()
        while True:
            field = await asyncio.wait_for(reader.next(), timeout=30)
            if field is None:
                break
            if field.name != "clips" or not field.filename:
                raise ValueError("Only video file fields are accepted.")
            if len(results) >= MAX_CLIPS:
                raise ValueError(f"You can send at most {MAX_CLIPS} results.")

            name = safe_result_name(field.filename, len(results) + 1)
            if not name.lower().endswith(".mp4"):
                raise ValueError(f"{name} is not an MP4 result.")

            file_data = bytearray()
            while chunk := await asyncio.wait_for(
                field.read_chunk(1024 * 1024), timeout=30
            ):
                if delivery_bytes_held + len(chunk) > MAX_DELIVERY_BUFFER_BYTES:
                    return json_error(
                        "The relay memory buffer is full. Try again shortly.",
                        503,
                        "delivery_buffer_full",
                        retry_after=5,
                    )
                delivery_bytes_held += len(chunk)
                total_size += len(chunk)
                file_data.extend(chunk)
                if len(file_data) > job.discord_limit:
                    raise ValueError(
                        f"{name} exceeds Discord's current file-size limit."
                    )
                if total_size > MAX_RESULT_TOTAL_BYTES:
                    raise ValueError(
                        "The combined compressed results exceed the relay limit."
                    )
            if not file_data:
                raise ValueError(f"{name} is empty.")
            result_bytes = bytes(file_data)
            del file_data
            if not valid_mp4_signature(result_bytes):
                raise ValueError(
                    f"{name} did not contain a complete MP4 video. "
                    "Try the original again or convert it to MP4 first."
                )
            results.append(BrowserResult(name=name, data=result_bytes))

        if not results:
            raise ValueError("No compressed MP4 results were received.")
        result_count = len(results)
        loop = asyncio.get_running_loop()
        completion: asyncio.Future[str] = loop.create_future()
        delivery = DeliveryRequest(job=job, results=results, completed=completion)
        try:
            delivery_queue.put_nowait(delivery)
        except asyncio.QueueFull:
            job.state = JobState.CLAIMED
            return json_error(
                "The Discord delivery queue filled up. It will retry automatically.",
                503,
                "queue_full",
                retry_after=5,
            )
        metrics.increment("batches_queued")
        metrics.increment("files_queued", len(results))
        metrics.increment("bytes_queued", total_size)
        active_relay_uploads -= 1
        upload_slot_held = False
        job.state = JobState.QUEUED
        try:
            message = await asyncio.wait_for(completion, timeout=180)
        except TimeoutError:
            job.state = JobState.DONE
            return json_error(
                "Discord took too long to accept the files. Check the channel "
                "before starting a new compression session.",
                504,
                "discord_timeout",
            )
        except RuntimeError as error:
            job.state = JobState.DONE
            return json_error(str(error), 502, "discord_delivery_failed")
        job.state = JobState.DONE
        return web.json_response(
            {
                "ok": True,
                "message": message,
                "count": result_count,
            },
            headers={"Cache-Control": "no-store"},
        )
    except TimeoutError:
        return json_error(
            "The upload stalled. Please try again.", 408, "upload_timeout"
        )
    except (ValueError, web.HTTPException) as error:
        job.state = JobState.CLAIMED
        return json_error(str(error), 400, "invalid_upload")
    except Exception as error:
        job.state = JobState.CLAIMED
        logger.warning("Upload relay failed (%s)", type(error).__name__)
        return json_error(
            "The relay could not read the compressed files. "
            "Check your connection and try again.",
            500,
            "relay_error",
        )
    finally:
        if upload_slot_held:
            active_relay_uploads -= 1
            delivery_bytes_held = max(0, delivery_bytes_held - total_size)
            job.state = JobState.CLAIMED


async def receive_browser_failure(request: web.Request) -> web.Response:
    """Accept a privacy-safe terminal failure signal from the browser client."""
    token = request.match_info["token"]
    job = active_job(token)
    if job is None:
        return json_error("This session is no longer active.", 410, "session_expired")
    supplied_secret = request.headers.get("X-Upload-Session", "")
    if not job.claim_secret or not secrets.compare_digest(
        supplied_secret.encode(), job.claim_secret.encode()
    ):
        return json_error(
            "This browser is not authorized to update the session.",
            403,
            "session_invalid",
        )
    if job.state is not JobState.CLAIMED:
        return json_error(
            "This session is still being processed or already reached delivery.",
            409,
            "outcome_already_recorded",
        )
    try:
        payload = await request.clone(client_max_size=4096).json()
    except web.HTTPRequestEntityTooLarge:
        return json_error("Failure report is too large.", 413, "report_too_large")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        return json_error("Expected a JSON object.", 400, "invalid_failure_report")
    raw_stage = str(payload.get("stage", "Browser processing"))
    stage = "".join(
        character
        for character in raw_stage
        if character.isalnum() or character in " ._-"
    ).strip()[:60]
    if not job.browser_failure_reported:
        schedule_owner_alert(
            compression_outcome_alert(
                job,
                succeeded=False,
                stage=stage or "Browser processing",
            )
        )
        job.browser_failure_reported = True
    return web.json_response(
        {"ok": True},
        headers={"Cache-Control": "no-store"},
    )


async def get_channel(job: UploadJob) -> discord.abc.Messageable | None:
    if job.interaction.channel is not None:
        return job.interaction.channel
    cached = client.get_channel(job.channel_id)
    if cached is not None:
        return cached
    try:
        return await client.fetch_channel(job.channel_id)
    except (discord.Forbidden, discord.NotFound):
        return None


async def send_result_batch(
    sender: Any,
    results: list[BrowserResult],
    message: str,
) -> None:
    attachments = [
        discord.File(io.BytesIO(result.data), filename=result.name)
        for result in results
    ]
    try:
        await sender(content=message, files=attachments)
    finally:
        for attachment in attachments:
            attachment.close()


async def deliver_browser_results(
    job: UploadJob,
    results: list[BrowserResult],
) -> str:
    channel: discord.abc.Messageable | None = None
    try:
        channel = await get_channel(job)
        result_count = len(results)
        if result_count == 1:
            result_summary = "your compressed video is ready."
        else:
            result_summary = f"your {result_count} compressed videos are ready."
        message = f"✅ **Compression complete!** <@{job.user_id}>, {result_summary}"
        if channel is None:
            raise RuntimeError(
                "The bot cannot access the channel where compression started."
            )
        await send_result_batch(channel.send, results, message)
        return "Your compressed videos were delivered to Discord."
    except Exception as error:
        logger.warning("Result delivery failed (%s)", type(error).__name__)
        if channel is not None:
            try:
                await channel.send(
                    f"<@{job.user_id}> compressed files were received, but "
                    "Discord rejected their delivery. Please try again."
                )
            except discord.HTTPException:
                pass
        raise RuntimeError(
            "Discord rejected the files. Confirm that the bot can view the "
            "channel, send messages, and attach files, then run /compress again."
        ) from error


async def delivery_worker(worker_number: int) -> None:
    global delivery_bytes_held
    assert delivery_queue is not None
    while not client.is_closed():
        delivery = await delivery_queue.get()
        try:
            message = await asyncio.wait_for(
                deliver_browser_results(delivery.job, delivery.results), timeout=120
            )
            total_bytes = sum(len(result.data) for result in delivery.results)
            metrics.increment("deliveries_succeeded")
            metrics.increment("files_delivered", len(delivery.results))
            metrics.increment("bytes_delivered", total_bytes)
            schedule_owner_alert(
                compression_outcome_alert(
                    delivery.job,
                    succeeded=True,
                    file_count=len(delivery.results),
                    total_bytes=total_bytes,
                )
            )
            if not delivery.completed.done():
                delivery.completed.set_result(message)
        except Exception as error:
            metrics.increment("deliveries_failed")
            schedule_owner_alert(
                compression_outcome_alert(
                    delivery.job,
                    succeeded=False,
                    file_count=len(delivery.results),
                    total_bytes=sum(len(result.data) for result in delivery.results),
                    stage="Discord delivery",
                )
            )
            if not delivery.completed.done():
                delivery.completed.set_exception(error)
        finally:
            delivery_bytes_held = max(
                0,
                delivery_bytes_held
                - sum(len(result.data) for result in delivery.results),
            )
            delivery.job.state = JobState.DONE
            jobs.pop(delivery.job.token, None)
            delivery_queue.task_done()
            logger.info("Delivery worker %d completed a session", worker_number)
            del delivery


@tree.command(
    name="compress",
    description="Privately compress up to 10 videos in your browser",
)
@app_commands.guild_only()
async def compress(interaction: discord.Interaction) -> None:
    if (
        effective_allowed_guild_ids
        and interaction.guild_id not in effective_allowed_guild_ids
    ):
        await interaction.response.send_message(
            "Professor Compressor is not enabled in this server.",
            ephemeral=True,
        )
        return
    if interaction.channel_id is None:
        await interaction.response.send_message(
            "Use this command inside a server channel.",
            ephemeral=True,
        )
        return
    discard_expired_jobs()
    cooldown_key = (interaction.guild_id or 0, interaction.user.id)
    now = time.time()
    previous_job_at = last_job_at.get(cooldown_key, 0.0)
    remaining = USER_COOLDOWN_SECONDS - (now - previous_job_at)
    if remaining > 0:
        await interaction.response.send_message(
            f"Please wait {max(1, int(remaining + 0.999))} seconds before "
            "creating another upload link.",
            ephemeral=True,
        )
        return
    if len(jobs) >= MAX_ACTIVE_JOBS:
        await interaction.response.send_message(
            "Professor Compressor is handling too many upload sessions right "
            "now. Please try again in a few minutes.",
            ephemeral=True,
        )
        return

    # One active link per member. A new command invalidates their older link.
    for old_token, old_job in list(jobs.items()):
        if old_job.user_id == interaction.user.id:
            jobs.pop(old_token, None)
    last_job_at[cooldown_key] = now

    token = secrets.token_urlsafe(32)
    jobs[token] = UploadJob(
        token=token,
        user_id=interaction.user.id,
        channel_id=interaction.channel_id,
        expires_at=time.time() + JOB_TTL_SECONDS,
        discord_limit=interaction.filesize_limit,
        interaction=interaction,
        guild_id=interaction.guild_id,
        guild_name=interaction_guild_name(interaction),
    )
    metrics.increment("sessions_created")
    upload_url = f"{PUBLIC_BASE_URL}/upload/{quote(token)}"
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Open compressor", url=upload_url))
    await interaction.response.send_message(
        f"Choose up to {MAX_CLIPS} videos. Keep the compressor page open "
        f"until they are sent back here. Compression runs locally; fitting MP4s "
        f"may be sent unchanged. "
        f"The link expires in {JOB_TTL_SECONDS // 60} minutes.",
        view=view,
        ephemeral=True,
    )
    guild_name = interaction_guild_name(interaction)
    schedule_owner_alert(
        "⚙️ **Compression session created**\n"
        f"Server: **{safe_alert_text(guild_name)}**\n"
        f"Server ID: `{interaction.guild_id}`\n"
        f"Connected servers: `{len(client.guilds)}`\n"
        f"Sessions since restart: `{metrics.snapshot()['sessions_created']}`"
    )


@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
async def botstats(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True)
    try:
        operator_ids = await get_application_operator_ids()
    except discord.HTTPException:
        await interaction.followup.send(
            "Discord could not verify the application owner. Try again shortly.",
            ephemeral=True,
        )
        return
    if interaction.user.id not in operator_ids:
        await interaction.followup.send(
            "This command is restricted to the application owner.",
            ephemeral=True,
        )
        return

    report = botstats_report(client.guilds, metrics.snapshot())
    if len(report) <= 1900:
        await interaction.followup.send(report, ephemeral=True)
        return
    report_file = discord.File(
        io.BytesIO(report.encode("utf-8")),
        filename="professor-compressor-status.txt",
    )
    await interaction.followup.send(
        "The live server list is attached.",
        file=report_file,
        ephemeral=True,
    )


if BOTSTATS_GUILD_ID is not None:
    tree.command(
        name="botstats",
        description="View private Professor Compressor server and usage statistics",
        guild=discord.Object(id=BOTSTATS_GUILD_ID),
    )(botstats)


def create_web_application() -> web.Application:
    """Build the HTTP application so routes can be smoke-tested independently."""
    application = web.Application(
        client_max_size=MAX_RESULT_TOTAL_BYTES + 2 * MIB,
        middlewares=[browser_security_headers],
    )
    application.router.add_get("/healthz", health)
    application.router.add_get("/metricsz", aggregate_metrics)
    application.router.add_get("/privacy", privacy_policy)
    application.router.add_get("/terms", terms_of_service)
    application.router.add_get("/upload/{token}", upload_form)
    application.router.add_post("/upload/{token}", receive_results)
    application.router.add_post(
        "/upload/{token}/failure",
        receive_browser_failure,
    )
    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent
    application.router.add_static("/brand/", package_root / "static")
    application.router.add_static(
        "/assets/ffmpeg-esm/",
        project_root / "node_modules/@ffmpeg/ffmpeg/dist/esm",
    )
    application.router.add_static(
        "/assets/util-esm/",
        project_root / "node_modules/@ffmpeg/util/dist/esm",
    )
    application.router.add_static(
        "/assets/core-esm/",
        project_root / "node_modules/@ffmpeg/core/dist/esm",
    )
    application.router.add_static(
        "/assets/core-mt-esm/",
        project_root / "node_modules/@ffmpeg/core-mt/dist/esm",
    )
    return application


async def start_upload_server() -> None:
    global web_runner, cleanup_task, delivery_queue
    if web_runner is not None:
        return
    application = create_web_application()
    # Upload URLs contain one-use credentials; keep them out of access logs.
    web_runner = web.AppRunner(application, access_log=None)
    try:
        await web_runner.setup()
        site = web.TCPSite(web_runner, WEB_HOST, WEB_PORT)
        await site.start()
    except Exception:
        await web_runner.cleanup()
        web_runner = None
        raise
    delivery_queue = asyncio.Queue(maxsize=DELIVERY_QUEUE_SIZE)
    for worker_number in range(1, DELIVERY_WORKERS + 1):
        worker = asyncio.create_task(delivery_worker(worker_number))
        delivery_workers.append(worker)
        background_tasks.add(worker)
        worker.add_done_callback(background_tasks.discard)
    cleanup_task = asyncio.create_task(expire_jobs_loop())
    background_tasks.add(cleanup_task)
    cleanup_task.add_done_callback(background_tasks.discard)
    logger.info("Private browser compressor listening at %s", PUBLIC_BASE_URL)


@client.event
async def on_guild_join(guild: discord.Guild) -> None:
    schedule_owner_alert(
        guild_alert_message(
            "🟢 **Professor Compressor installed**",
            guild,
            server_count=len(client.guilds),
        )
    )
    schedule_dsc_stats_update()


@client.event
async def on_guild_remove(guild: discord.Guild) -> None:
    schedule_owner_alert(
        guild_alert_message(
            "🔴 **Professor Compressor removed**",
            guild,
            server_count=len(client.guilds),
        )
    )
    schedule_dsc_stats_update()


@client.event
async def on_ready() -> None:
    global commands_synced, dsc_stats_task
    if not commands_synced:
        synced_commands = await tree.sync()
        logger.info(
            "Synced global commands: "
            + ", ".join(command.name for command in synced_commands)
        )
        if BOTSTATS_GUILD_ID is not None:
            owner_guild = discord.Object(id=BOTSTATS_GUILD_ID)
            owner_commands = await tree.sync(guild=owner_guild)
            logger.info(
                f"Synced private commands to server {BOTSTATS_GUILD_ID}: "
                + ", ".join(command.name for command in owner_commands)
            )
        if not client.guilds:
            logger.warning(
                "Warning: the bot is not installed as a member of any server. "
                "Install it to your server so long compression jobs can fall "
                "back to normal channel messages after interactions expire."
            )
        if effective_allowed_guild_ids:
            logger.info(
                "Commands restricted to Discord servers "
                + ", ".join(
                    str(guild_id) for guild_id in sorted(effective_allowed_guild_ids)
                )
            )
        else:
            logger.info("Commands are available in all Discord servers.")
        commands_synced = True
    await start_upload_server()
    if DSC_API_TOKEN and (dsc_stats_task is None or dsc_stats_task.done()):
        dsc_stats_task = asyncio.create_task(dsc_stats_loop())
        background_tasks.add(dsc_stats_task)
        dsc_stats_task.add_done_callback(background_tasks.discard)
    logger.info("Logged in as %s in %d server(s)", client.user, len(client.guilds))


def run() -> None:
    runtime_settings = Settings.from_environment(require_token=True)
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    client.run(runtime_settings.discord_token, log_handler=None)
