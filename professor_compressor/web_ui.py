import json
from html import escape

from .assets import ASSET_PREFIX
from .paths import TEMPLATES

_COMPRESSOR_TEMPLATE = (TEMPLATES / "compressor.html").read_text(encoding="utf-8")
_DELIVERY_TEMPLATE = (TEMPLATES / "delivery-status.html").read_text(encoding="utf-8")

REVIEW_PROMPT = """<p id="review-prompt" class="privacy" hidden>
  If you're enjoying Professor Compressor, please consider leaving a review and/or
  upvoting on
  <a href="https://top.gg/bot/1550752400271351839"
    target="_blank" rel="noopener noreferrer">Top.gg</a>!
</p>"""


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
    discord_limit_bytes: int | None = None,
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
            discord_limit_bytes if discord_limit_bytes is not None else target_bytes
        ),
        "MAX_BATCH_BYTES": max_batch_bytes,
        "SESSION_SECRET": session_secret,
        "DIAGNOSTIC_REFERENCE": diagnostic_reference,
        "DIAGNOSTIC_EVENTS_ENABLED": diagnostic_events_enabled,
        "EXPIRES_SECONDS": expires_in_seconds,
    }
    recovery = (
        '<div class="message" role="status"><strong>Page refreshed.</strong> '
        "Videos selected in the previous page are no longer available here. "
        "Choose them again to restart local processing.</div>"
        if reopened
        else ""
    )
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
