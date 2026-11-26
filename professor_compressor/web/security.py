"""Security and caching headers applied to every HTTP response."""

from typing import Any

from aiohttp import web

from .assets import ASSET_PREFIX


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
    elif request.path.startswith("/brand/") and response.status in {200, 304}:
        # Unversioned browser modules import each other; revalidating keeps a
        # browser from combining cached files with newly deployed ones.
        response.headers["Cache-Control"] = "no-cache"
    if request.path.startswith("/upload/") or request.path in {
        "/healthz",
        "/readyz",
        "/metricsz",
    }:
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response
