"""Read-only operator diagnostics. No credential in a URL or browser storage."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

from aiohttp import BasicAuth, web

from .observability import EventStore

HTML = (Path(__file__).parent / "templates" / "dashboard.html").read_text(
    encoding="utf-8"
)


def register_dashboard(
    application: web.Application,
    store: EventStore,
    key: str,
    ip_address: Callable[[web.Request], str],
    *,
    runtime_snapshot: Callable[[], dict] | None = None,
) -> None:
    failures: dict[str, deque] = {}
    global_failures: deque[float] = deque()
    slots = asyncio.Semaphore(2)
    headers = {
        "Cache-Control": "no-store",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }

    def authenticate(request: web.Request) -> web.Response | None:
        if not key:
            return web.Response(status=404, headers=headers)
        # A stranger must not be able to lock out an operator who knows the key.
        try:
            auth = BasicAuth.decode(
                request.headers.get("Authorization", ""), encoding="utf-8"
            )
            valid = auth.login == "admin" and secrets.compare_digest(
                auth.password.encode(), key.encode()
            )
        except (ValueError, UnicodeError):
            valid = False
        if valid:
            return None
        now = time.monotonic()
        while global_failures and now - global_failures[0] > 60:
            global_failures.popleft()
        if len(global_failures) >= 500:
            return web.Response(status=429, headers={**headers, "Retry-After": "60"})
        # Bound unauthenticated bookkeeping, including requests with spoofed IPs.
        for ip in list(failures):
            if not failures[ip] or now - failures[ip][-1] > 60:
                failures.pop(ip, None)
        ip = ip_address(request)
        attempts = failures.get(ip, deque())
        while attempts and now - attempts[0] > 60:
            attempts.popleft()
        if len(attempts) >= 10:
            return web.Response(status=429, headers={**headers, "Retry-After": "60"})
        if not valid:
            global_failures.append(now)
            if len(failures) < 2000 or ip in failures:
                failures.setdefault(ip, attempts).append(now)
            return web.Response(
                status=401,
                headers={
                    **headers,
                    "WWW-Authenticate": 'Basic realm="Professor Compressor operations", charset="UTF-8"',
                },
            )
        return None

    async def dashboard(request: web.Request) -> web.Response:
        failure = authenticate(request)
        if failure is not None:
            return failure
        return web.Response(text=HTML, content_type="text/html", headers=headers)

    async def data(request: web.Request) -> web.Response:
        failure = authenticate(request)
        if failure is not None:
            return failure
        try:
            days = int(request.query.get("days", "7"))
        except ValueError:
            return web.json_response(
                {"error": "Invalid days"}, status=400, headers=headers
            )
        if slots.locked():
            return web.json_response(
                {"error": "Dashboard busy"}, status=429, headers=headers
            )
        async with slots:
            try:
                result = await asyncio.to_thread(
                    store.query,
                    days=days,
                    include_tests=request.query.get("tests") == "1",
                    reference=request.query.get("reference", ""),
                    dimension=request.query.get("dimension", ""),
                    value=request.query.get("value", ""),
                )
            except Exception:
                return web.json_response(
                    {"error": "Diagnostic storage unavailable"},
                    status=503,
                    headers=headers,
                )
        # The Discord cache belongs to the event loop, never the SQL reader thread.
        result["runtime"] = runtime_snapshot() if runtime_snapshot else {}
        result["generated_at"] = time.time()
        return web.json_response(result, headers=headers)

    application.router.add_get("/ops", dashboard)
    application.router.add_get("/ops/data", data)
