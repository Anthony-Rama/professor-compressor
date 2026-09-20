import asyncio
import html
import io
import os
import secrets
import time
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

from web_ui import browser_compressor


MIB = 1024 * 1024
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))
PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL", f"http://127.0.0.1:{WEB_PORT}"
).rstrip("/")
JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_MINUTES", "30")) * 60
USER_COOLDOWN_SECONDS = int(os.getenv("USER_COOLDOWN_SECONDS", "15"))
MAX_ACTIVE_JOBS = int(os.getenv("MAX_ACTIVE_JOBS", "250"))
MAX_CLIPS = 10
MAX_RESULT_TOTAL_BYTES = int(os.getenv("MAX_RESULT_TOTAL_MIB", "220")) * MIB
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
    used: bool = False


@dataclass(frozen=True)
class BrowserResult:
    name: str
    data: bytes


intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
jobs: dict[str, UploadJob] = {}
background_tasks: set[asyncio.Task[Any]] = set()
last_job_at: dict[tuple[int, int], float] = {}
web_runner: web.AppRunner | None = None
cleanup_task: asyncio.Task[None] | None = None
commands_synced = False
effective_allowed_guild_ids: frozenset[int] = ALLOWED_GUILD_IDS


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
    progress {{ width: 100%; height: 10px; display: none; overflow: hidden;
                border: 0; border-radius: 999px; accent-color: #6873ff; }}
    progress::-webkit-progress-bar {{ background: #101827; border-radius: 999px; }}
    progress::-webkit-progress-value {{ background: #6873ff; border-radius: 999px; }}
    #status {{ min-height: 22px; margin: 9px 0 0; font-size: 14px; }}
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
    if job is None or job.used or time.time() > job.expires_at:
        jobs.pop(token, None)
        return None
    return job


def discard_expired_jobs() -> None:
    now = time.time()
    expired_tokens = [
        token
        for token, job in jobs.items()
        if job.used or now > job.expires_at
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
        }
    )


async def upload_form(request: web.Request) -> web.Response:
    job = active_job(request.match_info["token"])
    if job is None:
        return page(
            "Link expired",
            "<h1>Upload link unavailable</h1>"
            "<p>This link expired or was already used. Run "
            "<strong>/compress</strong> again.</p>",
        )

    safe_target = max(1 * MIB, int(job.discord_limit * 0.94))
    return page(
        "Professor Compressor",
        browser_compressor(MAX_CLIPS, safe_target),
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
    token = request.match_info["token"]
    job = active_job(token)
    if job is None:
        return page(
            "Link expired",
            "<h1>Upload link unavailable</h1>"
            "<p>Run <strong>/compress</strong> again.</p>",
        )

    results: list[BrowserResult] = []
    total_size = 0
    try:
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
            if len(result_bytes) < 12 or result_bytes[4:8] != b"ftyp":
                raise ValueError(f"{name} is not a valid MP4 result.")
            results.append(BrowserResult(name=name, data=result_bytes))

        if not results:
            raise ValueError("No compressed MP4 results were received.")

        job.used = True
        task = asyncio.create_task(deliver_browser_results(job, results))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        return page(
            "Compression complete",
            f"<h1>Compression complete</h1><p>{len(results)} locally "
            "compressed clip(s) were relayed to Professor Compressor. "
            "You can close this page and return to Discord.</p>",
        )
    except (ValueError, web.HTTPException) as error:
        return page(
            "Delivery error",
            f"<h1>Delivery failed</h1><p class='error'>"
            f"{html.escape(str(error))}</p><p>Your original videos remained "
            "on this device. Run <strong>/compress</strong> again to retry.</p>",
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
) -> None:
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
                return
            except (discord.HTTPException, discord.NotFound):
                pass
        if channel is None:
            raise RuntimeError(
                "The interaction expired and the bot cannot access the channel."
            )
        await send_result_batch(channel.send, results, message)
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
    finally:
        jobs.pop(job.token, None)


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
    global web_runner, cleanup_task
    if web_runner is not None:
        return
    application = web.Application(
        client_max_size=MAX_RESULT_TOTAL_BYTES + 2 * MIB,
        middlewares=[browser_security_headers],
    )
    application.router.add_get("/healthz", health)
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
