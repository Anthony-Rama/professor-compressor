"""Loopback-only browser test harness; never starts a Discord connection."""

import asyncio
import os
import secrets
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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

from professor_compressor import application as app  # noqa: E402
from professor_compressor.domain import UploadJob  # noqa: E402

delivery_delays = {}


async def test_session(request: web.Request) -> web.Response:
    token = secrets.token_urlsafe(16)
    app.jobs[token] = UploadJob(
        token=token,
        user_id=1,
        channel_id=2,
        expires_at=time.time() + 600,
        discord_limit=1_100_000,
        interaction=SimpleNamespace(channel=None),
        guild_id=999,
    )
    if request.query.get("slow") == "1":
        delivery_delays[token] = 3
    app.track(app.jobs[token], "created")
    return web.json_response(
        {"path": f"/upload/{token}", "reference": app.jobs[token].diagnostic_reference}
    )


async def capture_delivery(job: UploadJob, results: list, caption: str = "") -> str:
    del caption
    await asyncio.sleep(delivery_delays.pop(job.token, 0))
    if not results or not all(app.valid_mp4_signature(item.data) for item in results):
        raise RuntimeError("Invalid browser output")
    if any(len(item.data) > job.discord_limit for item in results):
        raise RuntimeError("Output exceeds target")
    return f"Test delivery accepted: {len(results)} file(s)."


async def workers(application: web.Application):
    app.delivery_queue = asyncio.Queue(maxsize=8)
    app.deliver_browser_results = capture_delivery
    task = asyncio.create_task(app.delivery_worker(1))
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    app.UPLOAD_RATE_LIMIT_PER_MINUTE = 1000
    application = app.create_web_application()
    application.router.add_post("/test/session", test_session)
    application.cleanup_ctx.append(workers)
    web.run_app(application, host="127.0.0.1", port=18764, access_log=None)
