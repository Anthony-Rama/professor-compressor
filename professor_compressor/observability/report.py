"""Anomaly checks and a shareable Markdown summary of one dashboard view.

Both are built only from the aggregated, privacy-safe dashboard result: counts,
allowlisted categories, timings, releases and diagnostic references. They never
read pseudonymous user/server keys or internal session IDs.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

# Version of the documented /ops/data shape (docs/observability.md).
SCHEMA_VERSION = 1

# Failure-rate change, in percentage points, that counts as a spike.
SPIKE_POINTS = 10
# Fewest finished sessions (delivered + failed) needed to compare rates.
MIN_FINISHED = 5

STEP_NAMES = {
    "created": "Link created",
    "opened": "Link opened",
    "selected": "Files selected",
    "started": "Compression started",
    "uploaded": "Uploaded to relay",
    "delivered": "Delivered to Discord",
}
OUTCOME_NAMES = {
    "delivered": "delivered",
    "failed": "failed",
    "cancelled": "cancelled",
    "in_progress": "in progress",
    "unknown": "unknown outcome",
    "abandoned": "abandoned before start",
    "expired": "expired unopened",
}
PERCENTILE_NAMES = {
    "encode_speed": ("Encode speed", "speed"),
    "time_to_delivery_seconds": ("Compress to delivery", "seconds"),
    "relay_receive_seconds": ("Relay receive", "seconds"),
    "queue_seconds": ("Delivery queue wait", "seconds"),
    "input_bytes": ("Input file size", "bytes"),
    "output_bytes": ("Output file size", "bytes"),
}
TIMING_NAMES = {
    "discord_delivery_seconds": "Discord delivery",
    "total_seconds": "Browser end to end",
    "encoding_seconds": "Encoding per file",
    "engine_seconds": "Engine loading",
}


def anomalies(result: Mapping[str, Any]) -> list[dict]:
    """Conditions worth a look, most severe first."""
    found: list[dict] = []

    def add(severity: str, code: str, message: str) -> None:
        found.append({"severity": severity, "code": code, "message": message})

    runtime = result.get("runtime") or {}
    storage = result.get("storage") or {}
    if runtime:
        if not runtime.get("ready"):
            add("warning", "service_not_ready", "The service is not ready.")
        if runtime.get("discord_ready") is False:
            add("warning", "discord_disconnected", "Discord is not connected.")
        if runtime.get("draining"):
            add("info", "draining", "The service is draining for shutdown.")
        depth, capacity = runtime.get("queue_depth"), runtime.get("queue_capacity")
        if isinstance(depth, int) and capacity and depth >= 0.8 * capacity:
            add(
                "warning",
                "queue_near_capacity",
                f"Delivery queue is at {depth} of {capacity}.",
            )
    if storage.get("configured") and not storage.get("ready"):
        add("warning", "storage_unavailable", "Diagnostic storage is not ready.")
    if storage.get("errors") or storage.get("dropped"):
        add(
            "warning",
            "storage_loss",
            f"Diagnostic storage reported {storage.get('errors', 0)} write error(s) "
            f"and {storage.get('dropped', 0)} dropped event(s) since start.",
        )
    funnel = result.get("funnel") or {}
    previous = result.get("comparison") or {}
    outcomes = funnel.get("outcomes") or {}
    finished = outcomes.get("delivered", 0) + outcomes.get("failed", 0)
    rate, before = funnel.get("failure_percent"), previous.get("failure_percent")
    previous_outcomes = previous.get("outcomes") or {}
    previous_finished = previous_outcomes.get("delivered", 0) + previous_outcomes.get(
        "failed", 0
    )
    if rate is not None and finished >= MIN_FINISHED:
        if before is not None and previous_finished >= MIN_FINISHED:
            if rate >= before + SPIKE_POINTS and rate >= 1.5 * before:
                add(
                    "warning",
                    "failure_rate_spike",
                    f"Failure rate {rate}% versus {before}% in the previous window.",
                )
        elif rate >= 25:
            add(
                "warning",
                "failure_rate_high",
                f"Failure rate {rate}% with no comparable previous window.",
            )
    previous_codes = previous.get("error_codes") or {}
    if previous.get("sessions"):
        new_codes: dict[str, int] = {}
        for group in (result.get("failures") or {}).get("groups", []):
            code = group["error_code"]
            if not previous_codes.get(code):
                new_codes[code] = new_codes.get(code, 0) + group["sessions"]
        for code, count in sorted(new_codes.items(), key=lambda item: -item[1]):
            if count >= 2:
                add(
                    "warning",
                    "new_error_code",
                    f"{code} affected {count} sessions and was not seen in the "
                    "previous window.",
                )
    started = next(
        (
            step["sessions"]
            for step in funnel.get("steps", [])
            if step["step"] == "started"
        ),
        0,
    )
    unknown = outcomes.get("unknown", 0)
    if unknown >= 3 and started and unknown / started >= 0.2:
        add(
            "info",
            "unknown_outcomes",
            f"{unknown} of {started} started sessions have no confirmed outcome.",
        )
    delivered, delivered_before = (
        outcomes.get("delivered", 0),
        previous_outcomes.get("delivered", 0),
    )
    if delivered_before >= 10 and delivered < 0.5 * delivered_before:
        add(
            "info",
            "deliveries_dropped",
            f"{delivered} deliveries versus {delivered_before} in the previous window.",
        )
    growth = result.get("growth") or {}
    real_removals = growth.get("removals", 0) - growth.get(
        "likely_reconnect_removals", 0
    )
    if real_removals >= 3 and real_removals > growth.get("installs", 0):
        add(
            "info",
            "net_server_loss",
            f"{real_removals} removals (excluding likely reconnect artifacts) "
            f"versus {growth.get('installs', 0)} installs.",
        )
    if (result.get("failures") or {}).get("truncated"):
        add(
            "info",
            "failures_truncated",
            "Failure groups use only the most recent failure reports.",
        )
    found.sort(key=lambda item: item["severity"] != "warning")
    return found


def _utc(value: object) -> str:
    if not isinstance(value, int | float):
        return "—"
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(value))


def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return "—"
    return f"{value:,}" if isinstance(value, int) else f"{value:,.1f}"


def _percent(value: object) -> str:
    return f"{value}%" if isinstance(value, int | float) else "—"


def _seconds(value: object) -> str:
    if not isinstance(value, int | float):
        return "—"
    if value < 1:
        return f"{round(value * 1000)} ms"
    if value < 120:
        return f"{value:.1f} s"
    return f"{value / 60:.1f} min"


def _bytes(value: object) -> str:
    if not isinstance(value, int | float):
        return "—"
    if value < 1024 * 1024:
        return f"{value / 1024:.1f} KiB"
    if value < 1024**3:
        return f"{value / 1024 / 1024:.1f} MiB"
    return f"{value / 1024**3:.2f} GiB"


def _duration(value: object) -> str:
    if not isinstance(value, int | float):
        return "—"
    if value >= 86400:
        return f"{value / 86400:.1f} days"
    if value >= 3600:
        return f"{value / 3600:.1f} hours"
    return f"{int(value // 60)} min"


def _measure(kind: str, value: object) -> str:
    if kind == "speed":
        return f"{value:.2f}x" if isinstance(value, int | float) else "—"
    if kind == "bytes":
        return _bytes(value)
    return _seconds(value)


def _mix(counts: Mapping[str, int]) -> str:
    ranked = sorted(counts.items(), key=lambda item: -item[1])[:3]
    return ", ".join(f"{name} {count}" for name, count in ranked) or "—"


def summary_markdown(result: Mapping[str, Any]) -> str:
    """A concise, privacy-safe Markdown report of the current view."""
    window = result.get("window") or {}
    runtime = result.get("runtime") or {}
    storage = result.get("storage") or {}
    store = result.get("store") or {}
    lines = [
        "# Professor Compressor operations summary",
        "",
        f"- Window: last {_number(window.get('days'))} day(s), "
        f"{_utc(window.get('start'))} to {_utc(window.get('end'))}; "
        f"test servers {'included' if window.get('include_tests') else 'excluded'}",
    ]
    if window.get("dimension"):
        lines.append(f"- Filter: {window['dimension']} = {window.get('value', '')}")
    if window.get("reference"):
        lines.append(f"- Reference filter: {window['reference']}")
    lines.append(f"- Data schema version: {result.get('schema_version', '—')}")
    lines += ["", "## Health now", ""]
    if runtime:
        counters = runtime.get("counters") or {}
        lines += [
            f"- Release {runtime.get('revision') or '—'}; "
            f"ready {'yes' if runtime.get('ready') else 'no'}; "
            "Discord "
            + ("connected" if runtime.get("discord_ready") else "not connected")
            + f"; uptime {_duration(counters.get('uptime_seconds'))}",
            f"- Connected servers {_number(runtime.get('connected_servers'))}; "
            f"active links {_number(runtime.get('active_sessions'))}; "
            f"uploads {_number(runtime.get('active_uploads'))}; "
            f"queue {_number(runtime.get('queue_depth'))}/"
            f"{_number(runtime.get('queue_capacity'))}; "
            f"workers {_number(runtime.get('delivery_workers'))}/"
            f"{_number(runtime.get('worker_capacity'))}",
        ]
    lines.append(
        f"- Diagnostic store {'ready' if storage.get('ready') else 'not ready'}; "
        f"{_number(store.get('events'))} events, {_number(store.get('sessions'))} "
        f"sessions, {_bytes(store.get('database_bytes'))}; oldest event "
        f"{_utc(store.get('oldest_event'))}; write errors "
        f"{_number(storage.get('errors'))}, dropped {_number(storage.get('dropped'))}"
    )
    funnel = result.get("funnel") or {}
    if funnel.get("steps"):
        lines += [
            "",
            "## Funnel",
            "",
            "| Step | Sessions | From previous step | Of created |",
            "| --- | ---: | ---: | ---: |",
        ]
        for step in funnel["steps"]:
            lines.append(
                f"| {STEP_NAMES.get(step['step'], step['step'])} | "
                f"{_number(step['sessions'])} | {_percent(step['step_percent'])} | "
                f"{_percent(step['overall_percent'])} |"
            )
        outcomes = funnel.get("outcomes") or {}
        previous = result.get("comparison") or {}
        lines += [
            "",
            "Outcomes: "
            + ", ".join(
                f"{_number(outcomes.get(key, 0))} {name}"
                for key, name in OUTCOME_NAMES.items()
            ),
            f"Failure rate (failed / delivered + failed): "
            f"{_percent(funnel.get('failure_percent'))}; previous window "
            f"{_percent(previous.get('failure_percent'))}",
        ]
    failures = result.get("failures") or {}
    lines += ["", "## Top failure groups", ""]
    if failures.get("groups"):
        lines += [
            f"{_number(failures.get('sessions'))} sessions reported a failure in "
            f"{_number(failures.get('group_count'))} groups.",
            "",
            "| Error code | Stage | Sessions | Share | Later delivered | Last seen | "
            "Browsers | Devices | Recent references |",
            "| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ]
        for group in failures["groups"][:8]:
            lines.append(
                f"| {group['error_code']} | {group['stage']} | "
                f"{_number(group['sessions'])} | {_percent(group['share_percent'])} | "
                f"{_number(group['recovered'])} | {_utc(group['last_seen'])} | "
                f"{_mix(group['browsers'])} | {_mix(group['devices'])} | "
                f"{', '.join(group['references'])} |"
            )
    else:
        lines.append("No failures reported in this window.")
    percentiles = result.get("percentiles") or {}
    timings = result.get("timings") or {}
    lines += ["", "## Performance (p50 / p90)", ""]
    measured = False
    for key, (name, kind) in PERCENTILE_NAMES.items():
        if key in percentiles:
            item = percentiles[key]
            measured = True
            lines.append(
                f"- {name}: {_measure(kind, item['p50'])} / "
                f"{_measure(kind, item['p90'])} ({_number(item['samples'])} samples)"
            )
    for key, name in TIMING_NAMES.items():
        if key in timings:
            item = timings[key]
            measured = True
            lines.append(
                f"- {name}: {_seconds(item.get('p50'))} / "
                f"{_seconds(item.get('p90'))} ({_number(item.get('samples'))} samples)"
            )
    files = result.get("files") or {}
    if files.get("measured_files"):
        measured = True
        lines.append(
            f"- Delivered files measured: {_number(files['measured_files'])}; "
            f"total {_bytes(files.get('input_bytes'))} in, "
            f"{_bytes(files.get('output_bytes'))} out "
            f"({_percent(files.get('reduction_percent'))} smaller); "
            f"sent unchanged {_percent(files.get('unchanged_percent'))}, "
            f"stream-copied {_percent(files.get('remuxed_percent'))}"
        )
    engines = (result.get("groups") or {}).get("engine") or []
    if engines:
        measured = True
        lines.append(
            "- Engines (sessions): "
            + ", ".join(f"{row['value']} {row['sessions']}" for row in engines)
        )
    if not measured:
        lines.append("No performance samples in this window.")
    growth = result.get("growth") or {}
    if growth:
        lines += [
            "",
            "## Growth",
            "",
            f"- Installs {_number(growth.get('installs'))}; removals "
            f"{_number(growth.get('removals'))} "
            f"({_number(growth.get('likely_reconnect_removals'))} likely reconnect "
            f"artifacts); net change {_number(growth.get('net_change'))}",
        ]
    lines += ["", "## Anomalies", ""]
    found = result.get("anomalies") or []
    lines += [f"- [{item['severity']}] {item['message']}" for item in found] or [
        "None detected."
    ]
    return "\n".join(lines) + "\n"
