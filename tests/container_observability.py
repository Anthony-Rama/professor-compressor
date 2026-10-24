"""Production-image smoke check; uses a dedicated disposable diagnostics volume.

Run twice with the same mounted directory: first `seed`, then `verify`.
No Discord connection, webhook, or production credential is used.
"""

import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.update(
    PYTHON_DOTENV_DISABLED="1",
    DISCORD_TOKEN="",
    ALERT_WEBHOOK_URL="",
    TOPGG_API_TOKEN="",
    DSC_API_TOKEN="",
    OBSERVABILITY_DB_PATH="/data/observability/events.sqlite3",
    OBSERVABILITY_ADMIN_KEY="container-smoke-only-password-not-for-production",
    PUBLIC_BASE_URL="https://container-smoke.invalid",
    DIAGNOSTIC_TEST_GUILD_IDS="999",
)

from aiohttp import BasicAuth, ClientSession, web

from professor_compressor import application as app
from professor_compressor.domain import JobState, UploadJob

SESSION_ID = "0123456789abcdef0123456789abcdef"
REFERENCE = "PC-0123456789AB"


async def main():
    application = app.create_web_application()
    runner = web.AppRunner(application, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 18765).start()
    try:
        for _ in range(100):
            if app.event_store.ready:
                break
            await asyncio.sleep(0.05)
        assert app.event_store.ready, "Diagnostic volume is not writable"
        path = Path(app.settings.observability_db_path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert os.getuid() != 0, "Smoke test must run as the production non-root user"
        auth = BasicAuth("admin", app.settings.observability_admin_key)
        async with ClientSession() as client:
            base = "http://127.0.0.1:18765"
            for route in (
                "/",
                "/healthz",
                "/brand/diagnostics.js",
                "/brand/dashboard.js",
                "/brand/dashboard.css",
            ):
                async with client.get(base + route) as response:
                    assert response.status == 200, route
            async with client.get(base + "/readyz") as response:
                assert response.status == 503, (
                    "HTTP-only test cannot claim Discord readiness"
                )
            for route in ("/ops", "/ops/data"):
                async with client.get(base + route) as response:
                    assert response.status == 401, route
                async with client.get(base + route, auth=auth) as response:
                    assert response.status == 200, route
                    assert response.headers["Cache-Control"] == "no-store"
            if sys.argv[1] == "seed":
                job = UploadJob(
                    token="container-smoke",
                    user_id=1,
                    channel_id=2,
                    expires_at=time.time() + 600,
                    discord_limit=20 * 1024 * 1024,
                    interaction=SimpleNamespace(),
                    guild_id=999,
                    state=JobState.CLAIMED,
                    claim_secret="test-only-page-secret",
                    diagnostic_id=SESSION_ID,
                )
                app.jobs[job.token] = job
                app.track(job, "created")
                app.track(job, "started", run_id="a" * 32)
                async with client.post(
                    base + "/upload/container-smoke/events",
                    headers={"X-Upload-Session": "test-only-page-secret"},
                    json={
                        "run_id": "a" * 32,
                        "events": [
                            {
                                "event_id": "b" * 32,
                                "event": "stage_finished",
                                "file_index": 1,
                                "data": {
                                    "stage": "encoding",
                                    "seconds": 0.25,
                                    "filename": "private.mp4",
                                },
                            }
                        ],
                    },
                ) as response:
                    assert response.status == 200
                app.track(job, "delivered", data={"files": 1})
            await asyncio.to_thread(app.event_store._queue.join)
            async with client.get(
                base + "/ops/data?tests=1&reference=" + REFERENCE, auth=auth
            ) as response:
                report = await response.json()
                assert report["runtime"]["connected_servers"] is None
                assert report["runtime"]["server_count_current"] is False
                assert report["totals"]["delivered"] == 1
                assert any(
                    event["event"] == "stage_finished" for event in report["events"]
                )
                assert "private.mp4" not in json.dumps(report)
            async with client.get(base + "/ops/data", auth=auth) as response:
                assert (await response.json())["totals"]["sessions"] == 0
        print(
            f"Container observability {sys.argv[1]}: passed "
            "(private auth, non-root storage, test filtering, retained timeline)"
        )
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
