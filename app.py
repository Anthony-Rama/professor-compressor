import asyncio
import html
import io
import ipaddress
import os
import secrets
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import certifi
from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("SSL_CERT_FILE", certifi.where())

from aiohttp import web
import discord
from discord import app_commands

from media_validation import valid_mp4_signature
from metrics import RuntimeMetrics
from web_ui import browser_compressor


MIB = 1024 * 1024
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", f"http://127.0.0.1:{WEB_PORT}"
).rstrip("/")
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_MINUTES", "10")) * 60
ACTIVE_SESSION_TTL_SECONDS = int(
    os.getenv("ACTIVE_SESSION_TTL_MINUTES", "30")
) * 60
USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "15"))
MAX_ACTIVE_JOBS = int(os.getenv("MAX_ACTIVE_JOBS", "250"))
MAX_CLIPS = 10
MAX_RESULT_TOTAL_BYTES = int(os.getenv("MAX_RESULT_TOTAL_MIB", "220")) * MIB
UPLOAD_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("UPLOAD_RATE_LIMIT_PER_MINUTE", "8")
)
MAX_CONCURRENT_UPLOADS = int(os.getenv("MAX_CONCURRENT_UPLOADS", "2"))
DELIVERY_QUEUE_SIZE = int(os.getenv("DELIVERY_QUEUE_SIZE", "8"))
DELIVERY_WORKERS = int(os.getenv("DELIVERY_WORKERS", "2"))
MAX_DELIVERY_BUFFER_BYTES = int(
    os.getenv("MAX_DELIVERY_BUFFER_MIB", "400")
) * MIB
ALLOWED_GUILD_IDS_TEXT = os.getenv(
    "ALLOWED_GUILD_IDS", os.getenv("ALLOWED_GUILD_ID", "")
).strip()
try:
    ALLOWED_GUILD_IDS = frozenset(
        int(guild_id.strip())
        for guild_id in ALLOWED_GUILD_IDS_TEXT.split(",")
        if guild_id.strip()
    )
except ValueError as error:
    raise RuntimeError(
        "ALLOWED_GUILD_IDS must contain comma-separated Discord server IDs."
    ) from error

if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing from .env")


@dataclass
class UploadJob:
    token: str
    user_id: int
    channel_id: int
    expires_at: float
    discord_limit: int
    interaction: discord.Interaction
    state: str = "open"
    claim_secret: str | None = None
    claimed_at: float | None = None


@dataclass(frozen=True)
class BrowserResult:
    name: str
    data: bytes


@dataclass
class DeliveryRequest:
    job: UploadJob
    results: list[BrowserResult]
    completed: asyncio.Future[str]


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
jobs: dict[str, UploadJob] = {}
background_tasks: set[asyncio.Task[Any]] = set()
last_job_at: dict[tuple[int, int], float] = {}
web_runner: web.AppRunner | None = None
cleanup_task: asyncio.Task[None] | None = None
delivery_queue: asyncio.Queue[DeliveryRequest] | None = None
delivery_workers: list[asyncio.Task[None]] = []
request_times: dict[tuple[str, str], deque[float]] = {}
active_relay_uploads = 0
delivery_bytes_held = 0
commands_synced = False
effective_allowed_guild_ids: frozenset[int] = ALLOWED_GUILD_IDS
metrics = RuntimeMetrics()


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
        "camera=(), microphone=(), geolocation=(), payment=()"
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
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
            padding: 24px; background:
            radial-gradient(circle at 50% 0%, #1c2941 0, #0d1422 42%, #090e18 100%);
            color: #f8fafc; }}
    main {{ width: min(560px, 100%); padding: 36px;
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
    .file-input {{ position: absolute; width: 1px; height: 1px; padding: 0;
                   margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0);
                   white-space: nowrap; border: 0; }}
    .file-picker {{ display: flex; gap: 14px; align-items: center; padding: 18px;
                    border: 1px dashed #59677d; border-radius: 14px;
                    background: #111a2a; cursor: pointer; transition: .18s ease; }}
    .file-picker:hover {{ border-color: #8991ff; background: #151f33; }}
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
    .file-list {{ display: grid; gap: 10px; margin: 16px 0; }}
    .file-card {{ padding: 13px 14px; border: 1px solid #354258;
                  border-radius: 12px; background: #131d2d; }}
    .file-head, .file-meta, .run-meta {{ display: flex; align-items: center;
                                        justify-content: space-between; gap: 12px; }}
    .file-name {{ min-width: 0; overflow: hidden; text-overflow: ellipsis;
                  white-space: nowrap; font-size: 14px; font-weight: 700; }}
    .file-status {{ flex: 0 0 auto; color: #aab5c5; font-size: 12px; }}
    .file-card progress {{ height: 7px; margin-top: 10px; }}
    .file-meta {{ margin-top: 7px; color: #7f8da1; font-size: 11px; }}
    .phase-panel {{ padding: 16px; border: 1px solid #3a4861; border-radius: 14px;
                    background: #111a2a; }}
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
    if job is None or job.state == "done" or time.time() > job.expires_at:
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
        if job.state == "done" or now > job.expires_at
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
    if job is None or job.state != "open":
        return page(
            "Link expired",
            "<h1>Upload link unavailable</h1>"
            "<p>This one-use link expired or was already opened. Run "
            "<strong>/compress</strong> again.</p>",
        )

    job.claim_secret = secrets.token_urlsafe(32)
    job.claimed_at = time.time()
    job.expires_at = time.time() + ACTIVE_SESSION_TTL_SECONDS
    job.state = "claimed"
    metrics.increment("sessions_opened")
    safe_target = max(1 * MIB, int(job.discord_limit * 0.94))
    return page(
        "Professor Compressor",
        browser_compressor(
            MAX_CLIPS,
            safe_target,
            job.claim_secret,
            max(1, int(job.expires_at - time.time())),
        ),
    )


def safe_result_name(name: str | None, number: int) -> str:
    raw = (name or f"clip-{number}.mp4").replace("\\", "/").split("/")[-1]
    cleaned = "".join(
        character
        for character in raw
        if character.isalnum() or character in " ._-"
    ).strip(" .")
    if not cleaned.lower().endswith(".mp4"):
        cleaned += ".mp4"
    return cleaned or f"clip-{number}.mp4"


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
        supplied_secret, job.claim_secret
    ):
        return json_error(
            "This browser is not authorized to use the upload session.",
            403,
            "session_invalid",
        )
    if job.state == "uploading" or job.state == "queued":
        return json_error(
            "This session already has an upload in progress.",
            409,
            "upload_in_progress",
        )
    if job.state != "claimed":
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
    job.state = "uploading"
    active_relay_uploads += 1
    try:
        if not request.content_type.startswith("multipart/"):
            raise ValueError("The upload format was invalid. Please try again.")
        reader = await request.multipart()
        while True:
            field = await reader.next()
            if field is None:
                break
            if field.name != "clips" or not field.filename:
                continue
            if len(results) >= MAX_CLIPS:
                raise ValueError(f"You can send at most {MAX_CLIPS} results.")

            name = safe_result_name(field.filename, len(results) + 1)
            if not name.lower().endswith(".mp4"):
                raise ValueError(f"{name} is not an MP4 result.")

            file_data = bytearray()
            while chunk := await field.read_chunk(1024 * 1024):
                file_data.extend(chunk)
                total_size += len(chunk)
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
            if not valid_mp4_signature(result_bytes):
                raise ValueError(
                    f"{name} did not contain a complete MP4 video. "
                    "Try the original again or convert it to MP4 first."
                )
            results.append(BrowserResult(name=name, data=result_bytes))

        if not results:
            raise ValueError("No compressed MP4 results were received.")
        if delivery_bytes_held + total_size > MAX_DELIVERY_BUFFER_BYTES:
            job.state = "claimed"
            return json_error(
                "The in-memory delivery buffer is full. It will retry automatically.",
                503,
                "delivery_buffer_full",
                retry_after=5,
            )

        loop = asyncio.get_running_loop()
        completion: asyncio.Future[str] = loop.create_future()
        delivery = DeliveryRequest(job=job, results=results, completed=completion)
        try:
            delivery_queue.put_nowait(delivery)
        except asyncio.QueueFull:
            job.state = "claimed"
            return json_error(
                "The Discord delivery queue filled up. It will retry automatically.",
                503,
                "queue_full",
                retry_after=5,
            )
        delivery_bytes_held += total_size
        metrics.increment("batches_queued")
        metrics.increment("files_queued", len(results))
        metrics.increment("bytes_queued", total_size)
        active_relay_uploads -= 1
        upload_slot_held = False
        job.state = "queued"
        try:
            message = await asyncio.wait_for(completion, timeout=180)
        except TimeoutError:
            job.state = "done"
            return json_error(
                "Discord took too long to accept the files. Check the channel "
                "before starting a new compression session.",
                504,
                "discord_timeout",
            )
        except RuntimeError as error:
            job.state = "done"
            return json_error(str(error), 502, "discord_delivery_failed")
        job.state = "done"
        return web.json_response(
            {
                "ok": True,
                "message": message,
                "count": len(results),
            },
            headers={"Cache-Control": "no-store"},
        )
    except (ValueError, web.HTTPException) as error:
        job.state = "claimed"
        return json_error(str(error), 400, "invalid_upload")
    except Exception as error:
        job.state = "claimed"
        print(f"Upload relay failed: {error!r}")
        return json_error(
            "The relay could not read the compressed files. "
            "Check your connection and try again.",
            500,
            "relay_error",
        )
    finally:
        if upload_slot_held:
            active_relay_uploads -= 1


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
        message = (
            f"✅ **Compression complete!** <@{job.user_id}>, {result_summary}"
        )
        if not job.interaction.is_expired():
            try:
                await send_result_batch(
                    job.interaction.followup.send,
                    results,
                    message,
                )
                return "Your compressed videos were delivered to Discord."
            except (discord.HTTPException, discord.NotFound):
                pass
        if channel is None:
            raise RuntimeError(
                "The interaction expired and the bot cannot access the channel."
            )
        await send_result_batch(channel.send, results, message)
        return "Your compressed videos were delivered to Discord."
    except Exception as error:
        print(f"Result delivery failed: {error!r}")
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
            message = await deliver_browser_results(delivery.job, delivery.results)
            metrics.increment("deliveries_succeeded")
            metrics.increment("files_delivered", len(delivery.results))
            metrics.increment(
                "bytes_delivered",
                sum(len(result.data) for result in delivery.results),
            )
            if not delivery.completed.done():
                delivery.completed.set_result(message)
        except Exception as error:
            metrics.increment("deliveries_failed")
            if not delivery.completed.done():
                delivery.completed.set_exception(error)
        finally:
            delivery_bytes_held = max(
                0,
                delivery_bytes_held
                - sum(len(result.data) for result in delivery.results),
            )
            delivery.job.state = "done"
            jobs.pop(delivery.job.token, None)
            delivery_queue.task_done()
            print(
                f"Delivery worker {worker_number} completed session "
                f"{delivery.job.token[:8]}."
            )


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
    )
    metrics.increment("sessions_created")
    upload_url = f"{PUBLIC_BASE_URL}/upload/{quote(token)}"
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Open compressor", url=upload_url))
    await interaction.response.send_message(
        f"Choose up to {MAX_CLIPS} videos. Keep the compressor page open "
        f"until they are sent back here. Your originals stay on your device. "
        f"The link expires in {JOB_TTL_SECONDS // 60} minutes.",
        view=view,
        ephemeral=True,
    )


async def start_upload_server() -> None:
    global web_runner, cleanup_task, delivery_queue
    if web_runner is not None:
        return
    application = web.Application(
        client_max_size=MAX_RESULT_TOTAL_BYTES + 2 * MIB,
        middlewares=[browser_security_headers],
    )
    application.router.add_get("/healthz", health)
    application.router.add_get("/metricsz", aggregate_metrics)
    application.router.add_get("/upload/{token}", upload_form)
    application.router.add_post("/upload/{token}", receive_results)
    project_root = Path(__file__).resolve().parent
    application.router.add_static("/brand/", project_root / "static")
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
    web_runner = web.AppRunner(application)
    await web_runner.setup()
    site = web.TCPSite(web_runner, WEB_HOST, WEB_PORT)
    await site.start()
    delivery_queue = asyncio.Queue(maxsize=DELIVERY_QUEUE_SIZE)
    for worker_number in range(1, DELIVERY_WORKERS + 1):
        worker = asyncio.create_task(delivery_worker(worker_number))
        delivery_workers.append(worker)
        background_tasks.add(worker)
        worker.add_done_callback(background_tasks.discard)
    cleanup_task = asyncio.create_task(expire_jobs_loop())
    background_tasks.add(cleanup_task)
    cleanup_task.add_done_callback(background_tasks.discard)
    print(f"Private browser compressor listening at {PUBLIC_BASE_URL}")


@client.event
async def on_ready() -> None:
    global commands_synced
    if not commands_synced:
        synced_commands = await tree.sync()
        print(
            "Synced global commands: "
            + ", ".join(command.name for command in synced_commands)
        )
        if not client.guilds:
            print(
                "Warning: the bot is not installed as a member of any server. "
                "Install it to your server so long compression jobs can fall "
                "back to normal channel messages after interactions expire."
            )
        if effective_allowed_guild_ids:
            print(
                "Commands restricted to Discord servers "
                + ", ".join(str(guild_id) for guild_id in sorted(effective_allowed_guild_ids))
            )
        else:
            print("Commands are available in all Discord servers.")
        commands_synced = True
    await start_upload_server()
    print(f"Logged in as {client.user}")


def run() -> None:
    client.run(DISCORD_TOKEN)
