"""Common no-store JSON responses for private upload routes."""

from typing import Any

from aiohttp import web


def json_error(message: str, status: int, code: str, **extra: Any) -> web.Response:
    return web.json_response(
        {"ok": False, "error": message, "code": code, **extra},
        status=status,
        headers={"Cache-Control": "no-store"},
    )


def pending_response() -> web.Response:
    return web.json_response(
        {
            "ok": False,
            "pending": True,
            "retry_after": 2,
            "message": (
                "Delivery is still being processed. Do not start another session."
            ),
        },
        status=202,
        headers={"Cache-Control": "no-store"},
    )
