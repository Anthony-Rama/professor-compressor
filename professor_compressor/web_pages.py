"""HTML document responses for private session pages and public pages."""

from html import escape

from aiohttp import web

from .paths import TEMPLATES

_PAGE_TEMPLATE = (TEMPLATES / "page.html").read_text(encoding="utf-8").rstrip("\n")


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


def public_document_response(document: str) -> web.Response:
    return web.Response(
        text=document,
        content_type="text/html",
        headers={
            "Cache-Control": "public, max-age=3600",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
