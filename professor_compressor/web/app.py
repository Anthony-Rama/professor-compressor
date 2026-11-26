"""The aiohttp application and its route table, in registration order."""

import asyncio
from collections.abc import AsyncIterator, Callable

from aiohttp import web

from ..config import MIB, Settings
from ..observability.dashboard import register_dashboard
from ..observability.store import EventStore
from ..observability.tracking import SessionTracker
from ..paths import NODE_MODULES, STATIC
from ..sessions.registry import SessionRegistry
from .assets import ASSET_PACKAGES, ASSET_PREFIX
from .health import HealthService
from .pages.document import public_document_response
from .pages.landing import guide_html, home_html, robots_txt, sitemap_xml
from .pages.legal import privacy_policy_html, terms_of_service_html
from .rate_limit import request_ip
from .report_routes import ReportRoutes
from .security import browser_security_headers
from .session_routes import SessionRoutes

GOOGLE_VERIFICATION_FILENAME = "googlec910ef324dae35ac.html"


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


def register_public_pages(application: web.Application, base_url: str) -> None:
    """Register public content independently of bot or relay state."""

    async def home(request: web.Request) -> web.Response:
        del request
        return public_document_response(home_html(base_url))

    async def video_guide(request: web.Request) -> web.Response:
        del request
        return public_document_response(guide_html(base_url))

    async def robots(request: web.Request) -> web.Response:
        del request
        return web.Response(
            text=robots_txt(base_url),
            content_type="text/plain",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async def sitemap(request: web.Request) -> web.Response:
        del request
        return web.Response(
            text=sitemap_xml(base_url),
            content_type="application/xml",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async def google_site_verification(request: web.Request) -> web.Response:
        del request
        return web.Response(
            text=f"google-site-verification: {GOOGLE_VERIFICATION_FILENAME}",
            content_type="text/html",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    async def privacy_policy(request: web.Request) -> web.Response:
        del request
        return public_document_response(privacy_policy_html())

    async def terms_of_service(request: web.Request) -> web.Response:
        del request
        return public_document_response(terms_of_service_html())

    application.router.add_get("/", home)
    application.router.add_get("/guide/compress-video-for-discord", video_guide)
    application.router.add_get("/robots.txt", robots)
    application.router.add_get("/sitemap.xml", sitemap)
    application.router.add_get(
        f"/{GOOGLE_VERIFICATION_FILENAME}", google_site_verification
    )
    application.router.add_get("/privacy", privacy_policy)
    application.router.add_get("/terms", terms_of_service)
