"""The aiohttp application and its route table, in registration order."""

import asyncio
from collections.abc import AsyncIterator, Callable

from aiohttp import web

from .assets import ASSET_PACKAGES, ASSET_PREFIX
from .browser_reports import ReportRoutes
from .config import MIB, Settings
from .dashboard import register_dashboard
from .health import HealthService
from .observability import EventStore
from .paths import NODE_MODULES, STATIC
from .rate_limit import request_ip
from .session_routes import SessionRoutes
from .sessions import SessionRegistry
from .tracking import SessionTracker
from .web_pages import browser_security_headers, register_public_pages


def build_web_application(
    settings: Settings,
    *,
    event_store: EventStore,
    sessions: SessionRegistry,
    tracker: SessionTracker,
    health: HealthService,
    session_routes: SessionRoutes,
    report_routes: ReportRoutes,
    runtime_snapshot: Callable[[], dict],
) -> web.Application:
    application = web.Application(
        client_max_size=settings.max_result_total_bytes + 2 * MIB,
        middlewares=[browser_security_headers],
    )

    async def diagnostic_lifecycle(
        _application: web.Application,
    ) -> AsyncIterator[None]:
        event_store.start()
        yield
        for job in list(sessions.jobs.values()):
            if job.compression_started or job.relay_upload_started:
                tracker.track(job, "interrupted")
        await asyncio.to_thread(event_store.stop)

    application.cleanup_ctx.append(diagnostic_lifecycle)
    register_dashboard(
        application,
        event_store,
        settings.observability_admin_key,
        request_ip,
        runtime_snapshot=runtime_snapshot,
    )
    register_public_pages(application, settings.public_base_url)
    router = application.router
    router.add_get("/healthz", health.health)
    router.add_get("/readyz", health.readiness)
    router.add_get("/metricsz", health.aggregate_metrics)
    router.add_get("/upload/{token}", session_routes.upload_form)
    router.add_post("/upload/{token}", session_routes.receive_results)
    router.add_get("/upload/{token}/status", session_routes.session_status)
    router.add_post("/upload/{token}/heartbeat", session_routes.renew_session)
    router.add_post("/upload/{token}/pause", session_routes.pause_session)
    router.add_post("/upload/{token}/progress", report_routes.receive_browser_progress)
    router.add_post(
        "/upload/{token}/telemetry", report_routes.receive_browser_telemetry
    )
    router.add_post("/upload/{token}/events", report_routes.receive_diagnostic_events)
    router.add_post("/upload/{token}/failure", report_routes.receive_browser_failure)
    router.add_static("/brand/", STATIC)
    for name, package in ASSET_PACKAGES.items():
        directory = NODE_MODULES / package / "dist/esm"
        router.add_static(f"{ASSET_PREFIX}/{name}/", directory)
        # Compatibility for pages opened before this release; never immutable.
        router.add_static(f"/assets/{name}/", directory)
    return application
