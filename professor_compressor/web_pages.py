"""Shared HTTP document responses, security headers, and public pages."""

from html import escape
from typing import Any

from aiohttp import web

from .assets import ASSET_PREFIX
from .landing_pages import guide_html, home_html, robots_txt, sitemap_xml
from .legal_pages import privacy_policy_html, terms_of_service_html
from .paths import TEMPLATES

_PAGE_TEMPLATE = (TEMPLATES / "page.html").read_text(encoding="utf-8").rstrip("\n")
GOOGLE_VERIFICATION_FILENAME = "googlec910ef324dae35ac.html"


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
    if (
        isinstance(request.path, str)
        and request.path.startswith(ASSET_PREFIX + "/")
        and response.status == 200
    ):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    if request.path.startswith("/upload/") or request.path in {
        "/healthz",
        "/readyz",
        "/metricsz",
    }:
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


def page(title: str, body: str) -> web.Response:
    page_title = (
        title if title == "Professor Compressor" else f"Professor Compressor | {title}"
    )
    head, _, tail = _PAGE_TEMPLATE.partition("__BODY__")
    document = head.replace("__TITLE__", escape(page_title)) + body + tail
    return web.Response(
        text=document,
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
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


def register_public_pages(application: web.Application, base_url: str) -> None:
    """Register public content independently of bot or relay state."""

    async def home(request: web.Request) -> web.Response:
        del request
        return legal_response(home_html(base_url))

    async def video_guide(request: web.Request) -> web.Response:
        del request
        return legal_response(guide_html(base_url))

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
        return legal_response(privacy_policy_html())

    async def terms_of_service(request: web.Request) -> web.Response:
        del request
        return legal_response(terms_of_service_html())

    application.router.add_get("/", home)
    application.router.add_get("/guide/compress-video-for-discord", video_guide)
    application.router.add_get("/robots.txt", robots)
    application.router.add_get("/sitemap.xml", sitemap)
    application.router.add_get(
        f"/{GOOGLE_VERIFICATION_FILENAME}", google_site_verification
    )
    application.router.add_get("/privacy", privacy_policy)
    application.router.add_get("/terms", terms_of_service)
