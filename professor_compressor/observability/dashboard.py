"""Read-only operator diagnostics. No credential in a URL or browser storage."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from collections import deque
from collections.abc import Callable

from aiohttp import BasicAuth, web

from ..paths import TEMPLATES
from ..web.pages.theme import THEME_BOOT_SCRIPT, THEME_CSS, THEME_MENU
from .queries import FILTER_DIMENSIONS, MAX_DAYS
from .report import SCHEMA_VERSION, anomalies, summary_markdown
from .store import EventStore

HTML = (
    (TEMPLATES / "dashboard.html")
    .read_text(encoding="utf-8")
    .replace("__THEME_CSS__", THEME_CSS)
    .replace("__THEME_BOOT__", THEME_BOOT_SCRIPT)
    .replace("__THEME_MENU__", THEME_MENU)
)
# The support reference shown to users: "PC-" and 12 uppercase hex digits.
REFERENCE = re.compile(r"PC-[0-9A-F]{12}")


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
        reference = request.query.get("reference", "").strip().upper()
        if reference and not REFERENCE.fullmatch(reference):
            return web.json_response(
                {"error": "Invalid reference"}, status=400, headers=headers
            )
        include_tests = request.query.get("tests") == "1"
        dimension = request.query.get("dimension", "")
        value = request.query.get("value", "")
        # The Discord cache belongs to the event loop, never the SQL reader thread.
        runtime = runtime_snapshot() if runtime_snapshot else {}
        uptime = (runtime.get("counters") or {}).get("uptime_seconds")
        if slots.locked():
            return web.json_response(
                {"error": "Dashboard busy"}, status=429, headers=headers
            )
        async with slots:
            try:
                result = await asyncio.to_thread(
                    store.query,
                    days=days,
                    include_tests=include_tests,
                    reference=reference,
                    dimension=dimension,
                    value=value,
                    process_started=(
                        time.time() - uptime if isinstance(uptime, int) else None
                    ),
                )
            except Exception:
                return web.json_response(
                    {"error": "Diagnostic storage unavailable"},
                    status=503,
                    headers=headers,
                )
        now = time.time()
        span = max(1, min(MAX_DAYS, days)) * 86400
        window = result.setdefault(
            "window",
            {
                "days": span // 86400,
                "start": now - span,
                "end": now,
                "previous_start": now - 2 * span,
                "include_tests": include_tests,
            },
        )
        if dimension in FILTER_DIMENSIONS and value:
            window.update(dimension=dimension, value=value[:64])
        if reference:
            window["reference"] = reference
        result["schema_version"] = SCHEMA_VERSION
        result["runtime"] = runtime
        result["generated_at"] = now
        result["anomalies"] = anomalies(result)
        result["summary"] = summary_markdown(result)
        return web.json_response(result, headers=headers)

    application.router.add_get("/ops", dashboard)
    application.router.add_get("/ops/data", data)
