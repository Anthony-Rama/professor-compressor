"""Allowlisted event names and scalar facts for diagnostics."""

import math
import re

HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
EVENTS = frozenset(
    {
        "created",
        "opened",
        "refreshed",
        "selected",
        "started",
        "page_left",
        "cancelled",
        "expired",
        "replaced",
        "interrupted",
        "stage_started",
        "stage_finished",
        "attempt_started",
        "processing_progress",
        "processing_stalled",
        "file_ready",
        "retry",
        "client_failed",
        "client_complete",
        "upload_started",
        "received",
        "delivery_started",
        "delivered",
        "delivery_failed",
        "upload_failed",
        "installed",
        "removed",
        "file_received",
    }
)
CLIENT_EVENTS = frozenset(
    {
        "stage_started",
        "stage_finished",
        "attempt_started",
        "processing_progress",
        "processing_stalled",
        "file_ready",
        "retry",
        "client_failed",
    }
)
ENUMS = {
    "stage": {
        "preparation",
        "validation",
        "session",
        "engine",
        "file_read",
        "metadata",
        "encoding",
        "remux",
        "conversion",
        "upload",
        "delivery",
        "processing",
        "output_metadata",
    },
    "error_code": {
        "INPUT_STORAGE_FULL",
        "INPUT_STORAGE_UNAVAILABLE",
        "INPUT_WRITE_UNSUPPORTED",
        "INPUT_TOO_LARGE",
        "INPUT_PREPARATION_TIMEOUT",
        "UNKNOWN",
        "WORKER_CRASH",
        "WORKER_MESSAGE_ERROR",
        "FILE_READ_FAILED",
        "UNSUPPORTED_CONTAINER",
        "INVALID_SIGNATURE",
        "METADATA_TIMEOUT",
        "METADATA_INVALID",
        "NO_VIDEO_STREAM",
        "DURATION_UNKNOWN",
        "UNSUPPORTED_CODEC",
        "INVALID_MEDIA",
        "ENGINE_LOAD_FAILED",
        "ENGINE_LOAD_TIMEOUT",
        "ENCODE_FAILED",
        "OUTPUT_TOO_LARGE",
        "TARGET_NOT_FEASIBLE",
        "BROWSER_MEMORY",
        "WASM_TRAP",
        "NETWORK",
        "HTTP",
        "SESSION_EXPIRED",
        "CANCELLED",
        "UPLOAD_TIMEOUT",
        "UPLOAD_CONNECTION_RESET",
        "INVALID_UPLOAD",
        "RELAY_ERROR",
        "DISCORD_REJECTED",
        "DELIVERY_TIMEOUT",
        "DELIVERY_UNCONFIRMED",
        "DELIVERY_ACCESS_DENIED",
        "DELIVERY_LIMIT_CHANGED",
        "ENCODE_TIMEOUT",
    },
    "input_format": {
        "mp4",
        "mov",
        "3gp",
        "webm",
        "mkv",
        "webm/mkv",
        "avi",
        "ogg",
        "flv",
        "asf/wmv",
        "mpeg",
        "ts",
        "m2ts",
        "unknown",
    },
    "video_codec": {
        "h264",
        "hevc",
        "vp8",
        "vp9",
        "av1",
        "mpeg4",
        "mpeg2video",
        "mpeg1video",
        "theora",
        "wmv1",
        "wmv2",
        "wmv3",
        "vc1",
        "flv1",
        "prores",
        "mjpeg",
        "other",
    },
    "audio_codec": {
        "aac",
        "opus",
        "vorbis",
        "mp3",
        "mp2",
        "flac",
        "alac",
        "ac3",
        "eac3",
        "pcm_s16le",
        "pcm_s24le",
        "none",
        "other",
    },
    "browser": {"Chrome", "Edge", "Firefox", "Safari", "Opera", "Other"},
    "engine_signal": {
        "oom",
        "allocation_failed",
        "wasm_bounds",
        "wasm_trap",
        "unsupported_codec",
        "invalid_media",
    },
    "signal_source": {"engine_log", "exception"},
    "exception_kind": {
        "Error",
        "TypeError",
        "RangeError",
        "RuntimeError",
        "AbortError",
        "other",
    },
    "decoder_mode": {"auto", "fixed"},
    "command_mode": {"bitrate", "quality", "copy"},
    "video_encoder": {"libx264", "copy"},
    "encoder_preset": {"veryfast"},
    "retry_reason": {"same_settings", "size_adjustment"},
    "video_profile": {
        "Main",
        "Main 10",
        "Main Still Picture",
        "Baseline",
        "Constrained Baseline",
        "High",
        "High 10",
        "High 4:2:2",
        "High 4:4:4 Predictive",
        "Simple Profile",
        "Advanced Simple Profile",
        "Profile 0",
        "Profile 1",
        "Profile 2",
        "Profile 3",
        "other",
    },
    "pixel_format": {
        "yuv420p",
        "yuv420p10le",
        "yuv420p12le",
        "yuv422p",
        "yuv422p10le",
        "yuv444p",
        "yuv444p10le",
        "nv12",
        "p010le",
        "other",
    },
    "color_transfer": {
        "bt709",
        "smpte2084",
        "arib-std-b67",
        "smpte170m",
        "bt2020-10",
        "bt2020-12",
        "unknown",
        "other",
    },
    "chroma_subsampling": {"420", "422", "444", "other"},
    "device": {"desktop", "mobile", "unknown"},
    "engine": {"multithreaded", "single-threaded", "unused"},
    "path": {"unchanged", "remuxed", "converted", "compressed"},
    "outcome": {"delivered", "failed", "cancelled"},
    "category": {
        "none",
        "network",
        "http",
        "processing",
        "cancelled",
        "memory",
        "timeout",
    },
}
NUMBERS = {
    **dict.fromkeys(
        (
            "seconds",
            "total_seconds",
            "encoding_seconds",
            "duration_seconds",
            "engine_seconds",
            "pre_upload_seconds",
            "hidden_seconds",
            "upload_seconds",
            "relay_receive_seconds",
            "receive_idle_seconds",
            "queue_seconds",
            "discord_delivery_seconds",
            "last_media_seconds",
            "progress_idle_seconds",
            "client_elapsed_seconds",
        ),
        86400,
    ),
    **dict.fromkeys(
        (
            "input_bytes",
            "output_bytes",
            "target_bytes",
            "discord_limit",
            "received_bytes",
        ),
        2**40,
    ),
    **dict.fromkeys(
        ("input_width", "input_height", "output_width", "output_height"), 32768
    ),
    **dict.fromkeys(("input_fps", "output_fps"), 1000),
    **dict.fromkeys(("video_kbps", "audio_kbps"), 1000000),
    **dict.fromkeys(
        (
            "encoding_attempts",
            "upload_attempts",
            "processing_failures",
            "delivery_failures",
            "attempt",
            "failed_attempt",
            "encode_pass",
            "files",
            "selected_count",
        ),
        100,
    ),
    "browser_major": 999,
    "client_sequence": 100000,
    "decoder_threads": 64,
    "encoder_threads": 64,
    "exit_code": 255,
    "http_status": 599,
    "last_progress_percent": 100,
    "video_level": 1000,
    "bit_depth": 32,
    "audio_channels": 64,
    "audio_sample_rate": 384000,
    "rotation_quarter_turns": 3,
    "connected_servers": 10000000,
}


def diagnostic_reference(diagnostic_id: str) -> str:
    """Return the support reference shown to users for a diagnostic session."""
    return "PC-" + diagnostic_id[:12].upper()


def valid_id(value: object) -> str:
    return value if isinstance(value, str) and HEX_ID.fullmatch(value) else ""


def sanitize(data: object) -> dict:
    if not isinstance(data, dict):
        return {}
    clean = {}
    for key, allowed in ENUMS.items():
        value = data.get(key)
        if isinstance(value, str) and value in allowed:
            clean[key] = value
    for key, maximum in NUMBERS.items():
        value = data.get(key)
        if (
            type(value) in {int, float}
            and 0 <= value <= maximum
            and math.isfinite(value)
        ):
            clean[key] = round(value, 4)
    for key in (
        "retryable",
        "retry_recovered",
        "cross_origin_isolated",
        "shared_array_buffer",
        "webassembly",
        "page_hidden",
        "frame_rates_differ",
        "size_adjustment",
    ):
        if type(data.get(key)) is bool:
            clean[key] = data[key]
    build = data.get("asset_build")
    if isinstance(build, str) and re.fullmatch(r"[a-f0-9]{16}", build):
        clean["asset_build"] = build
    return clean
