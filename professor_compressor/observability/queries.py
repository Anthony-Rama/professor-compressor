"""Read-only dashboard queries over one bounded SQLite snapshot."""

import json
import math
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path

# Bounds the whole dashboard read; SQLite aborts statements past this budget.
QUERY_BUDGET_SECONDS = 3
MAX_DAYS = 90
FILTER_DIMENSIONS = frozenset(
    {
        "stage",
        "error_code",
        "engine_signal",
        "input_format",
        "browser",
        "device",
        "engine",
        "path",
        "revision",
    }
)


def query_dashboard(
    path: str,
    health: Callable[[], Mapping[str, object]],
    *,
    days: int = 7,
    include_tests: bool = False,
    reference: str = "",
    dimension: str = "",
    value: str = "",
) -> dict:
    """Bounded operator-only query. All browser facts are explicitly untrusted."""
    connection = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")  # One consistent WAL snapshot for all panels.
        deadline = time.monotonic() + QUERY_BUDGET_SECONDS
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        cutoff = time.time() - max(1, min(MAX_DAYS, days)) * 86400
        where, args, search = session_filter(
            cutoff, include_tests, reference, dimension, value
        )
        totals, statuses = totals_panel(connection, where, args)
        retries = retries_panel(connection, where, args)
        sessions = sessions_panel(connection, where, args)
        history = history_panel(connection, sessions) if search and sessions else []
        groups = groups_panel(connection, where, args)
        timings = timings_panel(connection, where, args)
        activity = activity_panel(connection, cutoff, include_tests)
        adoption = adoption_panel(connection, where, args)
        performance = performance_panel(connection, where, args)
        files = files_panel(connection, where, args)
        collection = collection_panel(connection, where, args, cutoff, include_tests)
        return {
            "storage": health(),
            "totals": totals,
            "statuses": statuses,
            "groups": groups,
            "timings": timings,
            "activity": activity,
            "adoption": adoption,
            "performance": performance,
            "files": files,
            "retries": retries,
            "collection": collection,
            "sessions": sessions,
            "events": history,
        }
    finally:
        connection.close()


def session_filter(
    cutoff: float, include_tests: bool, reference: str, dimension: str, value: str
) -> tuple[str, list, str]:
    """Return the shared session WHERE clause, its arguments, and the reference."""
    where = "s.created >= ? AND (? OR s.is_test=0) AND s.status NOT IN ('installed','removed')"
    args: list = [cutoff, include_tests]
    if dimension in FILTER_DIMENSIONS and value:
        if dimension == "revision":
            where += " AND s.revision=?"
        else:
            where += " AND EXISTS(SELECT 1 FROM events d WHERE d.session_id=s.id AND json_extract(d.data,?)=?)"
            args.append("$." + dimension)
        args.append(value[:64])
    search = reference.upper().strip()[:64]
    if search:
        where += " AND s.reference=?"
        args.append(search)
    return where, args, search


def totals_panel(
    connection: sqlite3.Connection, where: str, args: list
) -> tuple[dict, list[dict]]:
    totals = dict(
        connection.execute(
            "SELECT COUNT(*) sessions, COALESCE(SUM(opened),0) opened, COALESCE(SUM(selected),0) selected, "
            "COALESCE(SUM(started),0) started, COALESCE(SUM(delivered),0) delivered, "
            "COUNT(CASE WHEN s.started=0 AND EXISTS(SELECT 1 FROM events p WHERE p.session_id=s.id "
            "AND p.source='browser' AND p.event='stage_finished' AND json_extract(p.data,'$.stage')='preparation' "
            "AND json_extract(p.data,'$.outcome')='failed') THEN 1 END) blocked_before_compression, "
            "COUNT(DISTINCT NULLIF(user_key,'')) users, COUNT(DISTINCT NULLIF(guild_key,'')) servers, "
            "COALESCE(SUM(CASE WHEN delivered=1 THEN files ELSE 0 END),0) files, "
            "COALESCE(SUM(CASE WHEN delivered=1 AND EXISTS(SELECT 1 FROM events f WHERE f.session_id=s.id "
            "AND f.event IN ('client_failed','upload_failed','delivery_failed') "
            "AND f.id<(SELECT MIN(d.id) FROM events d WHERE d.session_id=s.id AND d.event='delivered' AND d.source='server')) "
            "THEN 1 ELSE 0 END),0) recovered "
            "FROM sessions s WHERE " + where,
            args,
        ).fetchone()
    )
    totals["delivery_percent"] = (
        round(100 * totals["delivered"] / totals["started"], 1)
        if totals["started"]
        else None
    )
    statuses = [
        dict(row)
        for row in connection.execute(
            "SELECT CASE WHEN status IN ('processing','uploading','queued','delivering') AND updated < ? "
            "THEN 'unknown' ELSE status END status, COUNT(*) count FROM sessions s WHERE "
            + where
            + " GROUP BY 1",
            [time.time() - 7200, *args],
        )
    ]
    state_counts = {row["status"]: row["count"] for row in statuses}
    totals["needs_attention"] = sum(
        state_counts.get(state, 0)
        for state in ("client_failed", "upload_failed", "delivery_failed")
    )
    totals["in_progress"] = sum(
        state_counts.get(state, 0)
        for state in ("processing", "uploading", "queued", "delivering")
    )
    totals["cancelled"] = state_counts.get("cancelled", 0)
    totals["unknown"] = state_counts.get("unknown", 0)
    return totals, statuses


def retries_panel(connection: sqlite3.Connection, where: str, args: list) -> dict:
    return dict(
        connection.execute(
            "SELECT COUNT(DISTINCT CASE WHEN e.event='started' AND e.run_id!='' THEN e.session_id||e.run_id END) runs, "
            "COALESCE(SUM(e.event='retry'),0) automatic_retries "
            "FROM events e JOIN sessions s ON s.id=e.session_id WHERE " + where,
            args,
        ).fetchone()
    )


def sessions_panel(
    connection: sqlite3.Connection, where: str, args: list
) -> list[dict]:
    """The 100 most recently updated sessions, each with its latest failure."""
    sessions = [
        dict(row)
        for row in connection.execute(
            "SELECT id,reference,created,updated,revision,is_test, "
            "CASE WHEN status IN ('processing','uploading','queued','delivering') AND updated < strftime('%s','now')-7200 THEN 'unknown' ELSE status END status, "
            "CASE WHEN delivered=1 THEN files ELSE 0 END files,failures,user_key,guild_key, "
            "(SELECT e.data FROM events e WHERE e.session_id=s.id "
            "AND (s.active_run='' OR e.run_id=s.active_run) AND e.event IN ('client_failed','upload_failed','delivery_failed') "
            "ORDER BY (e.event='delivery_failed' AND e.source='server') DESC, e.id DESC LIMIT 1) failure_data "
            "FROM sessions s WHERE " + where + " ORDER BY updated DESC LIMIT 100",
            args,
        )
    ]
    for session in sessions:
        session["failure_data"] = json.loads(session["failure_data"] or "{}")
    return sessions


def history_panel(connection: sqlite3.Connection, sessions: list[dict]) -> list[dict]:
    """Event timeline for the session found by a reference search."""
    history = [
        dict(row)
        for row in connection.execute(
            "SELECT at,run_id,file_index,event,source,data FROM events WHERE session_id=? ORDER BY id DESC LIMIT 500",
            (sessions[0]["id"],),
        )
    ]
    for item in history:
        item["data"] = json.loads(item["data"])
    history.reverse()
    return history


def groups_panel(
    connection: sqlite3.Connection, where: str, args: list
) -> dict[str, list[dict]]:
    groups = {}
    for field in (
        "stage",
        "error_code",
        "engine_signal",
        "input_format",
        "browser",
        "device",
        "engine",
        "path",
    ):
        groups[field] = [
            dict(row)
            for row in connection.execute(
                "SELECT json_extract(e.data,?) value, COUNT(DISTINCT e.session_id) sessions, COUNT(*) events "
                "FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
                + where
                + " AND json_extract(e.data,?) IS NOT NULL GROUP BY 1 ORDER BY sessions DESC LIMIT 20",
                ["$." + field, *args, "$." + field],
            )
        ]
    groups["revision"] = [
        dict(row)
        for row in connection.execute(
            "SELECT revision value,COUNT(*) sessions,SUM(delivered) delivered FROM sessions s WHERE "
            + where
            + " GROUP BY revision ORDER BY sessions DESC LIMIT 20",
            args,
        )
    ]
    groups["failure_stage"] = [
        dict(row)
        for row in connection.execute(
            "SELECT json_extract(e.data,'$.stage') value,COUNT(DISTINCT e.session_id) sessions "
            "FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
            + where
            + " AND json_extract(e.data,'$.error_code') IS NOT NULL "
            "AND json_extract(e.data,'$.error_code')!='CANCELLED' "
            "AND json_extract(e.data,'$.stage') IS NOT NULL GROUP BY 1 ORDER BY sessions DESC LIMIT 20",
            args,
        )
    ]
    return groups


def timings_panel(connection: sqlite3.Connection, where: str, args: list) -> dict:
    """p50/p95 for each timing field over its latest 5000 samples."""
    timings = {}
    for field, event in {
        "engine_seconds": "client_complete",
        "encoding_seconds": "file_ready",
        "relay_receive_seconds": "received",
        "queue_seconds": "delivered",
        "discord_delivery_seconds": "delivered",
        "total_seconds": "client_complete",
    }.items():
        rows = connection.execute(
            "SELECT json_extract(e.data,?) value FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
            + where
            + " AND e.event=? "
            "AND json_type(e.data,?) IN ('integer','real') ORDER BY e.id DESC LIMIT 5000",
            ["$." + field, *args, event, "$." + field],
        ).fetchall()
        samples = sorted(
            row["value"]
            for row in rows
            if field not in {"engine_seconds", "encoding_seconds"} or row["value"] > 0
        )
        if samples:
            timings[field] = {
                "samples": len(samples),
                "p50": samples[math.ceil(len(samples) * 0.5) - 1],
                "p95": samples[math.ceil(len(samples) * 0.95) - 1],
            }
    return timings


def activity_panel(
    connection: sqlite3.Connection, cutoff: float, include_tests: bool
) -> list[dict]:
    activity_where = "e.at >= ? AND (? OR s.is_test=0)"
    return [
        dict(row)
        for row in connection.execute(
            "SELECT date(e.at,'unixepoch') day,e.event,COUNT(*) count FROM events e JOIN sessions s ON s.id=e.session_id "
            "WHERE "
            + activity_where
            + " AND e.event IN ('installed','removed','created','delivered') GROUP BY 1,2 ORDER BY 1",
            [cutoff, include_tests],
        )
    ]


def adoption_panel(connection: sqlite3.Connection, where: str, args: list) -> dict:
    return dict(
        connection.execute(
            "SELECT COUNT(*) activated_servers,COALESCE(SUM(deliveries>1),0) repeat_servers FROM "
            "(SELECT guild_key,COUNT(*) deliveries FROM sessions s WHERE "
            + where
            + " AND delivered=1 AND guild_key!='' GROUP BY guild_key)",
            args,
        ).fetchone()
    )


def performance_panel(
    connection: sqlite3.Connection, where: str, args: list
) -> list[dict]:
    return [
        dict(row)
        for row in connection.execute(
            "SELECT json_extract(e.data,'$.input_format') format, "
            "CASE WHEN json_extract(e.data,'$.duration_seconds')<60 THEN '<1 min' "
            "WHEN json_extract(e.data,'$.duration_seconds')<300 THEN '1-5 min' ELSE '5+ min' END duration_band, "
            "json_extract(e.data,'$.output_height') height,COUNT(*) files, "
            "AVG(json_extract(e.data,'$.encoding_seconds')) mean_encoding, "
            "1.0*SUM(json_extract(e.data,'$.duration_seconds'))/NULLIF(SUM(json_extract(e.data,'$.encoding_seconds')),0) speed "
            "FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
            + where
            + " AND e.event='file_ready' AND json_extract(e.data,'$.encoding_seconds')>0 "
            "AND json_extract(e.data,'$.duration_seconds')>0 GROUP BY 1,2,3 ORDER BY files DESC LIMIT 50",
            args,
        )
    ]


def files_panel(connection: sqlite3.Connection, where: str, args: list) -> dict:
    # One authoritative receipt per file index. Browser file-ready reports
    # are intentionally not added again to delivered byte totals.
    files = dict(
        connection.execute(
            "SELECT COUNT(*) measured_files,COALESCE(SUM(json_extract(e.data,'$.input_bytes')),0) input_bytes, "
            "COALESCE(SUM(json_extract(e.data,'$.output_bytes')),0) output_bytes, "
            "COALESCE(SUM(CASE WHEN json_extract(e.data,'$.path')='unchanged' THEN 1 ELSE 0 END),0) unchanged, "
            "COALESCE(SUM(CASE WHEN json_extract(e.data,'$.path')='remuxed' THEN 1 ELSE 0 END),0) remuxed "
            "FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
            + where
            + " AND s.delivered=1 AND e.event='file_received' AND e.source='server' "
            "AND json_extract(e.data,'$.input_bytes')>0 AND json_extract(e.data,'$.output_bytes')>=0 "
            "AND e.id=(SELECT MAX(f.id) FROM events f WHERE f.session_id=e.session_id AND f.file_index=e.file_index AND f.event='file_received' AND f.source='server')",
            args,
        ).fetchone()
    )
    files["reduction_percent"] = (
        round(100 * (1 - files["output_bytes"] / files["input_bytes"]), 1)
        if files["input_bytes"]
        else None
    )
    return files


def collection_panel(
    connection: sqlite3.Connection,
    where: str,
    args: list,
    cutoff: float,
    include_tests: bool,
) -> dict:
    collection = dict(
        connection.execute(
            "SELECT MIN(created) since,COUNT(*) retained_sessions FROM sessions "
            "WHERE status NOT IN ('installed','removed')"
        ).fetchone()
    )
    collection["hidden_test_sessions"] = (
        0
        if include_tests
        else connection.execute(
            "SELECT COUNT(*) FROM sessions s WHERE " + where + " AND s.is_test=1",
            [cutoff, True, *args[2:]],
        ).fetchone()[0]
    )
    return collection
