"""Validate optional browser measurements before using them in delivery reports."""

import json
from typing import Any

from ..domain import CompressionDiagnostic
from ..observability.contract import ENUMS, valid_id

# Relay diagnostics name a detected container; "unknown" is never accepted here.
INPUT_FORMATS = ENUMS["input_format"] - {"unknown"}


def sanitize_telemetry(value: object) -> dict:
    """Allow only bounded measurements and fixed categories, never arbitrary text."""
    if not isinstance(value, dict):
        return {}
    clean: dict[str, Any] = {}
    batch_id = valid_id(value.get("batch_id"))
    if batch_id:
        clean["batch_id"] = batch_id
    for key in (
        "engine_seconds",
        "pre_upload_seconds",
        "hidden_seconds",
        "upload_attempts",
        "encoding_attempts",
        "input_width",
        "input_height",
        "output_width",
        "output_height",
        "input_fps",
        "output_fps",
        "total_seconds",
        "upload_seconds",
        "processing_failures",
        "delivery_failures",
    ):
        number: Any = value.get(key)
        if type(number) in (int, float) and 0 <= number <= 86400:
            clean[key] = number
    for key in ("path", "engine"):
        item = value.get(key)
        if isinstance(item, str) and item in ENUMS[key]:
            clean[key] = item
    return clean


def parse_compression_diagnostics(
    raw: bytes, *, max_clips: int
) -> list[CompressionDiagnostic]:
    """Accept optional, bounded browser measurements; never trust them for delivery."""
    try:
        values = json.loads(raw)
        if not isinstance(values, list) or not 1 <= len(values) <= max_clips:
            return []
        diagnostics = []
        for value in values:
            if not isinstance(value, dict):
                return []
            input_bytes = value.get("input_bytes")
            duration: Any = value.get("duration_seconds")
            video_kbps = value.get("video_kbps")
            copied = value.get("copied")
            input_format = value.get("input_format")
            encoding_seconds = value.get("encoding_seconds")
            if encoding_seconds is not None and (
                type(encoding_seconds) not in (int, float)
                or not 0 <= encoding_seconds <= 86400
            ):
                encoding_seconds = None
            if input_format is not None and input_format not in INPUT_FORMATS:
                return []
            if (
                type(input_bytes) is not int
                or not 0 < input_bytes <= 10**12
                or type(copied) is not bool
            ):
                return []
            if copied:
                if video_kbps is not None or (
                    duration is not None
                    and (
                        type(duration) not in (int, float) or not 0 < duration <= 86400
                    )
                ):
                    return []
            elif (
                type(duration) not in (int, float)
                or not 0 < duration <= 86400
                or (
                    video_kbps is not None
                    and (
                        type(video_kbps) is not int
                        or not 100 <= video_kbps <= 1_000_000_000
                    )
                )
            ):
                return []
            diagnostics.append(
                CompressionDiagnostic(
                    input_bytes,
                    float(duration) if duration is not None else None,
                    video_kbps,
                    copied,
                    input_format,
                    float(encoding_seconds) if encoding_seconds is not None else None,
                    sanitize_telemetry(value.get("telemetry")),
                )
            )
        return diagnostics
    except (ValueError, TypeError, UnicodeDecodeError):
        return []
