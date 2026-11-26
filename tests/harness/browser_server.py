"""Loopback-only browser test harness; never starts a Discord connection."""

import asyncio
import os
import secrets
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
os.environ["ALERT_WEBHOOK_URL"] = ""
os.environ["DSC_API_TOKEN"] = ""
os.environ["TOPGG_API_TOKEN"] = ""
diagnostic_directory = tempfile.TemporaryDirectory(prefix="compressor-diagnostics-")
os.environ["OBSERVABILITY_DB_PATH"] = str(
    Path(diagnostic_directory.name) / "events.sqlite3"
)
os.environ["OBSERVABILITY_ADMIN_KEY"] = "browser-test-only-operator-password"
os.environ["DIAGNOSTIC_TEST_GUILD_IDS"] = "999"

from aiohttp import web  # noqa: E402 - configure the isolated harness before app imports

from professor_compressor.app.runtime import Runtime  # noqa: E402
from professor_compressor.config import Settings  # noqa: E402
from professor_compressor.domain import UploadJob  # noqa: E402
from professor_compressor.relay.media_validation import valid_mp4_signature  # noqa: E402

delivery_delays = {}
test_discord_limit = int(os.environ.get("BROWSER_TEST_DISCORD_LIMIT_BYTES", "1100000"))
if not 1_100_000 <= test_discord_limit <= 20 * 1024 * 1024:
    raise ValueError("Browser test upload limit is outside the supported test range")
capture_directory = os.environ.get("BROWSER_TEST_CAPTURE_DIR")


def test_runtime_snapshot():
    # Explicitly simulated Discord cache, only in this credential-free harness.
    snapshot = runtime.dashboard_runtime()
    snapshot.update(
        connected_servers=57,
        server_count_current=True,
        discord_ready=True,
        ready=True,
        commands_synced=True,
        gateway_latency_ms=42,
    )
    return snapshot


async def test_session(request: web.Request) -> web.Response:
    # Independent browser tests share one loopback IP. Do not let an earlier
    # test consume a later test's diagnostic request budget. HTTP rate-limit
    # enforcement is tested separately against the unmodified production route.
    for key in tuple(runtime.request_times):
        if key[0] == "diagnostic_events":
            runtime.request_times.pop(key, None)
    token = secrets.token_urlsafe(16)
    runtime.jobs[token] = UploadJob(
        token=token,
        user_id=1,
        channel_id=2,
        expires_at=time.time()
        + (1800 if os.environ.get("RUN_LONG_ENCODER_STRESS") == "1" else 600),
        discord_limit=test_discord_limit,
        interaction=SimpleNamespace(channel=None),
        guild_id=999,
    )
    if request.query.get("slow") == "1":
        delivery_delays[token] = 3
    runtime.track(runtime.jobs[token], "created")
    return web.json_response(
        {
            "path": f"/upload/{token}",
            "reference": runtime.jobs[token].diagnostic_reference,
        }
    )


async def capture_delivery(job: UploadJob, results: list, caption: str = "") -> str:
    del caption
    await asyncio.sleep(delivery_delays.pop(job.token, 0))
    if not results or not all(valid_mp4_signature(item.data) for item in results):
        raise RuntimeError("Invalid browser output")
    if any(len(item.data) > job.discord_limit for item in results):
        raise RuntimeError("Output exceeds target")
    if capture_directory:
        directory = Path(capture_directory)
        directory.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(results):
            await asyncio.to_thread(
                (directory / f"{job.token}-{index}.mp4").write_bytes, item.data
            )
    return f"Test delivery accepted: {len(results)} file(s)."


async def workers(application: web.Application):
    runtime.relay.queue = asyncio.Queue(maxsize=runtime.settings.delivery_queue_size)
    task = asyncio.create_task(runtime.delivery_worker(1))
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    settings = replace(
        Settings.from_environment(require_token=False),
        upload_rate_limit_per_minute=1000,
    )
    runtime = Runtime(settings, deliver_results=capture_delivery)
    application = runtime.create_web_application(runtime_snapshot=test_runtime_snapshot)
    application.router.add_post("/test/session", test_session)
    application.cleanup_ctx.append(workers)
    web.run_app(application, host="127.0.0.1", port=18764, access_log=None)
