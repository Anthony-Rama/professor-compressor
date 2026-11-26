"""Upload-session pages rendered for the browser."""

import json
from html import escape

from ...branding import TOPGG_LISTING_URL
from ...paths import TEMPLATES
from ..assets import ASSET_PREFIX


def _fragment(name: str) -> str:
    """Load an HTML fragment that is inserted into another document."""
    return (TEMPLATES / name).read_text(encoding="utf-8").rstrip("\n")


_COMPRESSOR_TEMPLATE = (TEMPLATES / "compressor.html").read_text(encoding="utf-8")
_DELIVERY_TEMPLATE = (TEMPLATES / "delivery-status.html").read_text(encoding="utf-8")
REVIEW_PROMPT = _fragment("review-prompt.html").replace(
    "__TOPGG_URL__", TOPGG_LISTING_URL
)
RECOVERY_NOTICE = _fragment("recovery-notice.html")
MAINTENANCE_BODY = _fragment("session-maintenance.html")
LINK_UNAVAILABLE_BODY = _fragment("session-unavailable.html")
_RATE_LIMITED_BODY = _fragment("session-rate-limited.html")


def rate_limited_body(retry_after: int) -> str:
    return _RATE_LIMITED_BODY.replace("__RETRY_AFTER__", str(retry_after))


def _json_script_value(value: object) -> str:
    """Serialize data without allowing it to terminate a JSON script element."""
    return (
        json.dumps(value)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def browser_compressor(
    max_clips: int,
    target_bytes: int,
    max_batch_bytes: int,
    session_secret: str,
    expires_in_seconds: int,
    *,
    discord_limit: int | None = None,
    reopened: bool = False,
    diagnostic_reference: str = "",
    diagnostic_events_enabled: bool = False,
) -> str:
    """Return the browser-bound page that encodes videos locally."""
    config = {
        "ASSET_PREFIX": ASSET_PREFIX,
        "MAX_CLIPS": max_clips,
        "TARGET_BYTES": target_bytes,
        "DISCORD_LIMIT_BYTES": (
            discord_limit if discord_limit is not None else target_bytes
        ),
        "MAX_BATCH_BYTES": max_batch_bytes,
        "SESSION_SECRET": session_secret,
        "DIAGNOSTIC_REFERENCE": diagnostic_reference,
        "DIAGNOSTIC_EVENTS_ENABLED": diagnostic_events_enabled,
        "EXPIRES_SECONDS": expires_in_seconds,
    }
    recovery = RECOVERY_NOTICE if reopened else ""
    return (
        _COMPRESSOR_TEMPLATE.replace("__MAX_CLIPS__", escape(str(max_clips)))
        .replace("__REVIEW_PROMPT__", REVIEW_PROMPT)
        .replace("__RECOVERY_NOTICE__", recovery)
        .replace("__CONFIG_JSON__", _json_script_value(config))
    )


def browser_delivery_status(
    session_secret: str, *, diagnostic_reference: str = ""
) -> str:
    """Show a status-only view while the relay owns the delivery."""
    config = {
        "SESSION_SECRET": session_secret,
        "DIAGNOSTIC_REFERENCE": diagnostic_reference,
    }
    return _DELIVERY_TEMPLATE.replace("__REVIEW_PROMPT__", REVIEW_PROMPT).replace(
        "__CONFIG_JSON__", _json_script_value(config)
    )
