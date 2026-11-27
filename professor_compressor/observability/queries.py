"""Read-only dashboard queries over one bounded SQLite snapshot."""

import bisect
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


FAILURE_EVENTS = "('client_failed','upload_failed','delivery_failed')"
# A session without an update for this long is no longer treated as active.
STALE_SECONDS = 7200
# One final outcome per session; the buckets are exclusive and sum to all
# sessions. Its single placeholder is the STALE_SECONDS cutoff.
OUTCOME = (
    "CASE WHEN s.delivered=1 THEN 'delivered' "
    "WHEN s.status IN ('client_failed','upload_failed','delivery_failed') THEN 'failed' "
    "WHEN s.status='cancelled' THEN 'cancelled' "
    "WHEN s.updated >= ? AND s.status IN ('created','opened','selected','processing','uploading','queued','delivering') THEN 'in_progress' "
    "WHEN s.started=1 THEN 'unknown' "
    "WHEN s.opened=1 OR s.selected=1 THEN 'abandoned' ELSE 'expired' END"
)
OUTCOMES = (
    "delivered",
    "failed",
    "cancelled",
    "in_progress",
    "unknown",
    "abandoned",
    "expired",
)
# Bounds for the grouped and listed panels.
MAX_FAILURE_GROUPS = 12
MAX_GROUP_REFERENCES = 3
MAX_FAILURE_ROWS = 20000
MAX_SAMPLES = 5000
MAX_MEMBERSHIP_EVENTS = 2000
# Removals this soon after a recorded process start are likely reconnect
# artifacts: Discord can report servers as removed while the cache refills.
RECONNECT_SECONDS = 60
# A shutdown marker is recorded before the restart it precedes.
SHUTDOWN_RECONNECT_SECONDS = 180


def query_dashboard(
    path: str,
    health: Callable[[], Mapping[str, object]],
    *,
    days: int = 7,
    include_tests: bool = False,
    reference: str = "",
    dimension: str = "",
    value: str = "",
    process_started: float | None = None,
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
        now = time.time()
        span = max(1, min(MAX_DAYS, days)) * 86400
        cutoff = now - span
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
        bucket = 3600 if span <= 86400 else 86400
        funnel = funnel_panel(connection, where, args, now, bucket)
        previous_where, previous_args, _ = session_filter(
            cutoff - span, include_tests, reference, dimension, value
        )
        previous_where += " AND s.created < ?"
        previous_args.append(cutoff)
        comparison = comparison_panel(connection, previous_where, previous_args, now)
        result = {
            "window": {
                "days": span // 86400,
                "start": cutoff,
                "end": now,
                "previous_start": cutoff - span,
                "include_tests": include_tests,
            },
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
            "funnel": funnel,
            "comparison": comparison,
            "failures": failures_panel(connection, where, args),
            "percentiles": percentiles_panel(connection, where, args),
            "growth": growth_panel(connection, cutoff, include_tests, process_started),
            "store": store_panel(connection, path),
        }
        if search:
            result["lookup"] = lookup_panel(search, sessions, history)
        return result
    finally:
        connection.close()


def percentile(samples: list[float], fraction: float) -> float:
    """Nearest-rank percentile of already sorted samples."""
    return samples[max(0, math.ceil(len(samples) * fraction) - 1)]


def summarize(samples: list[float]) -> dict:
    samples = sorted(samples)
    return {
        "samples": len(samples),
        "p50": percentile(samples, 0.5),
        "p90": percentile(samples, 0.9),
    }


def funnel_panel(
    connection: sqlite3.Connection, where: str, args: list, now: float, bucket: int
) -> dict:
    """Sessions reaching each step, final outcomes, and an outcome trend.

    A later step implies the earlier ones, so the steps never increase even
    when an older client skipped a report.
    """
    reached = (
        "SELECT CAST(s.created / ? AS INTEGER) * ? start, "
        + OUTCOME
        + " outcome, COUNT(*) created, "
        "SUM(MAX(s.opened,s.selected,s.started,s.uploaded,s.delivered)) opened, "
        "SUM(MAX(s.selected,s.started,s.uploaded,s.delivered)) selected, "
        "SUM(MAX(s.started,s.uploaded,s.delivered)) started, "
        "SUM(MAX(s.uploaded,s.delivered)) uploaded, SUM(s.delivered) delivered "
        "FROM (SELECT s.created, s.updated, s.status, s.opened, s.selected, s.started, "
        "s.delivered, EXISTS(SELECT 1 FROM events r WHERE r.session_id=s.id "
        "AND r.event='received') uploaded FROM sessions s WHERE " + where + ") s "
        "GROUP BY 1, 2"
    )
    names = ("created", "opened", "selected", "started", "uploaded", "delivered")
    reach = dict.fromkeys(names, 0)
    outcomes = dict.fromkeys(OUTCOMES, 0)
    trend: dict[int, dict] = {}
    for item in connection.execute(
        reached, [bucket, bucket, now - STALE_SECONDS, *args]
    ):
        for name in names:
            reach[name] += item[name]
        outcomes[item["outcome"]] += item["created"]
        point = trend.setdefault(
            item["start"],
            {"start": item["start"], "sessions": 0, "delivered": 0, "failed": 0},
        )
        point["sessions"] += item["created"]
        if item["outcome"] in {"delivered", "failed"}:
            point[item["outcome"]] += item["created"]
    steps = []
    previous = None
    for name in names:
        count = reach[name]
        steps.append(
            {
                "step": name,
                "sessions": count,
                "step_percent": (
                    round(100 * count / previous, 1) if previous else None
                ),
                "overall_percent": (
                    round(100 * count / reach["created"], 1)
                    if reach["created"]
                    else None
                ),
            }
        )
        previous = count
    finished = outcomes["delivered"] + outcomes["failed"]
    return {
        "steps": steps,
        "outcomes": outcomes,
        "failure_percent": (
            round(100 * outcomes["failed"] / finished, 1) if finished else None
        ),
        "bucket_seconds": bucket,
        "trend": [trend[start] for start in sorted(trend)],
    }


def comparison_panel(
    connection: sqlite3.Connection, where: str, args: list, now: float
) -> dict:
    """Outcomes and failure codes for the equally long window before this one."""
    outcomes = dict.fromkeys(OUTCOMES, 0)
    for row in connection.execute(
        "SELECT "
        + OUTCOME
        + " outcome, COUNT(*) count FROM sessions s WHERE "
        + where
        + " GROUP BY 1",
        [now - STALE_SECONDS, *args],
    ):
        outcomes[row["outcome"]] = row["count"]
    finished = outcomes["delivered"] + outcomes["failed"]
    codes = {
        row["code"]: row["sessions"]
        for row in connection.execute(
            "SELECT COALESCE(json_extract(e.data,'$.error_code'),'UNKNOWN') code, "
            "COUNT(DISTINCT e.session_id) sessions FROM events e JOIN sessions s ON s.id=e.session_id "
            "WHERE " + where + " AND " + failure_condition("e") + " GROUP BY 1",
            args,
        )
    }
    return {
        "sessions": sum(outcomes.values()),
        "outcomes": outcomes,
        "failure_percent": (
            round(100 * outcomes["failed"] / finished, 1) if finished else None
        ),
        "error_codes": codes,
    }


def failure_condition(alias: str) -> str:
    """Reported failures, including failed preparation steps; never cancellations."""
    return (
        f"({alias}.event IN {FAILURE_EVENTS} OR ({alias}.event='stage_finished' "
        f"AND json_extract({alias}.data,'$.outcome')='failed' "
        f"AND json_extract({alias}.data,'$.error_code') IS NOT NULL)) "
        f"AND COALESCE(json_extract({alias}.data,'$.error_code'),'')!='CANCELLED'"
    )


def failures_panel(connection: sqlite3.Connection, where: str, args: list) -> dict:
    """Failure reports grouped by error code and stage, counted per session."""
    rows = connection.execute(
        "SELECT code, stage, s.reference reference, s.delivered delivered, last_at, reports, "
        "COALESCE(browser, (SELECT json_extract(b.data,'$.browser') FROM events b "
        "WHERE b.session_id=f.session_id AND b.source='browser' "
        "AND json_extract(b.data,'$.browser') IS NOT NULL ORDER BY b.id DESC LIMIT 1)) browser, "
        "COALESCE(device, (SELECT json_extract(b.data,'$.device') FROM events b "
        "WHERE b.session_id=f.session_id AND b.source='browser' "
        "AND json_extract(b.data,'$.device') IS NOT NULL ORDER BY b.id DESC LIMIT 1)) device "
        "FROM (SELECT e.session_id session_id, "
        "COALESCE(json_extract(e.data,'$.error_code'),'UNKNOWN') code, "
        "COALESCE(json_extract(e.data,'$.stage'), CASE e.event WHEN 'upload_failed' THEN 'upload' "
        "WHEN 'delivery_failed' THEN 'delivery' ELSE 'unknown' END) stage, "
        "MAX(e.at) last_at, COUNT(*) reports, MAX(json_extract(e.data,'$.browser')) browser, "
        "MAX(json_extract(e.data,'$.device')) device "
        "FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
        + where
        + " AND "
        + failure_condition("e")
        + " GROUP BY 1, 2, 3) f JOIN sessions s ON s.id=f.session_id "
        "ORDER BY last_at DESC LIMIT ?",
        [*args, MAX_FAILURE_ROWS],
    ).fetchall()
    groups: dict[tuple[str, str], dict] = {}
    failed_sessions = set()
    for row in rows:
        failed_sessions.add(row["reference"])
        group = groups.setdefault(
            (row["code"], row["stage"]),
            {
                "error_code": row["code"],
                "stage": row["stage"],
                "sessions": 0,
                "reports": 0,
                "recovered": 0,
                "last_seen": row["last_at"],
                "browsers": {},
                "devices": {},
                "references": [],
            },
        )
        group["sessions"] += 1
        group["reports"] += row["reports"]
        group["recovered"] += row["delivered"]
        for field, key in (("browsers", "browser"), ("devices", "device")):
            name = row[key] or "Not reported"
            group[field][name] = group[field].get(name, 0) + 1
        # Rows arrive newest first, so the first references are the latest.
        if len(group["references"]) < MAX_GROUP_REFERENCES:
            group["references"].append(row["reference"])
    ranked = sorted(
        groups.values(), key=lambda item: (-item["sessions"], -item["last_seen"])
    )
    for group in ranked:
        group["share_percent"] = round(
            100 * group["sessions"] / len(failed_sessions), 1
        )
    return {
        "sessions": len(failed_sessions),
        "group_count": len(ranked),
        "truncated": len(rows) == MAX_FAILURE_ROWS,
        "groups": ranked[:MAX_FAILURE_GROUPS],
    }


def percentiles_panel(connection: sqlite3.Connection, where: str, args: list) -> dict:
    """p50/p90 of recent samples for speed, waiting and size measurements."""
    queries = {
        # Media seconds encoded per wall-clock second; higher is faster. Files
        # sent unchanged or only stream-copied are not encodes.
        "encode_speed": (
            "json_extract(e.data,'$.duration_seconds') / json_extract(e.data,'$.encoding_seconds')",
            "e.event='file_ready' AND json_extract(e.data,'$.encoding_seconds')>0 "
            "AND json_extract(e.data,'$.duration_seconds')>0 "
            "AND COALESCE(json_extract(e.data,'$.path'),'compressed') IN ('compressed','converted')",
        ),
        # Server arrival of Compress to server-confirmed delivery.
        "time_to_delivery_seconds": (
            "e.at - (SELECT MAX(st.at) FROM events st WHERE st.session_id=e.session_id "
            "AND st.event='started' AND st.id<e.id)",
            "e.event='delivered' AND e.source='server'",
        ),
        "relay_receive_seconds": (
            "json_extract(e.data,'$.relay_receive_seconds')",
            "e.event='received' AND json_type(e.data,'$.relay_receive_seconds') IN ('integer','real')",
        ),
        "queue_seconds": (
            "json_extract(e.data,'$.queue_seconds')",
            "e.event='delivered' AND json_type(e.data,'$.queue_seconds') IN ('integer','real')",
        ),
        "input_bytes": (
            "json_extract(e.data,'$.input_bytes')",
            "e.event='file_received' AND e.source='server' AND json_extract(e.data,'$.input_bytes')>0",
        ),
        "output_bytes": (
            "json_extract(e.data,'$.output_bytes')",
            "e.event='file_received' AND e.source='server' AND json_extract(e.data,'$.output_bytes')>0",
        ),
    }
    result = {}
    for name, (expression, condition) in queries.items():
        samples = [
            row[0]
            for row in connection.execute(
                "SELECT "
                + expression
                + " FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
                + where
                + " AND "
                + condition
                + " ORDER BY e.id DESC LIMIT ?",
                [*args, MAX_SAMPLES],
            )
            if isinstance(row[0], int | float) and row[0] >= 0
        ]
        if samples:
            result[name] = summarize(samples)
    return result


def growth_panel(
    connection: sqlite3.Connection,
    cutoff: float,
    include_tests: bool,
    process_started: float | None,
) -> dict:
    """Server installs and removals, with likely reconnect artifacts marked.

    Stored history is never changed; the marking is computed for this view.
    """
    # Each membership event has its own session row with the event as status;
    # starting from those rows avoids scanning every event in the window.
    rows = connection.execute(
        "SELECT e.at at, e.event event, json_extract(e.data,'$.connected_servers') servers "
        "FROM sessions s CROSS JOIN events e ON e.session_id=s.id "
        "WHERE s.status IN ('installed','removed') AND s.updated >= ? AND e.at >= ? "
        "AND (? OR s.is_test=0) AND e.event IN ('installed','removed') "
        "ORDER BY e.at DESC LIMIT ?",
        [cutoff, cutoff, include_tests, MAX_MEMBERSHIP_EVENTS],
    ).fetchall()
    rows.reverse()
    # A process start, the first event recorded by each release, and the
    # shutdown markers written when a running process stops.
    starts = [process_started] if process_started else []
    releases = [
        row[0]
        for row in connection.execute(
            "SELECT MIN(created) first FROM sessions GROUP BY revision ORDER BY first"
        )
    ]
    shutdowns = [
        row[0]
        for row in connection.execute(
            "SELECT at FROM events WHERE at >= ? AND event='interrupted' ORDER BY at LIMIT 1000",
            [cutoff - SHUTDOWN_RECONNECT_SECONDS],
        )
    ]
    removed = [row["at"] for row in rows if row["event"] == "removed"]
    daily: dict[str, dict] = {}
    series = []
    for row in rows:
        day = time.strftime("%Y-%m-%d", time.gmtime(row["at"]))
        point = daily.setdefault(
            day,
            {
                "day": day,
                "installs": 0,
                "removals": 0,
                "likely_reconnect_removals": 0,
                "connected_servers": None,
            },
        )
        if isinstance(row["servers"], int):
            point["connected_servers"] = row["servers"]
        if row["event"] == "installed":
            point["installs"] += 1
            artifact = False
        else:
            point["removals"] += 1
            artifact = likely_reconnect(row["at"], removed, starts, releases, shutdowns)
            point["likely_reconnect_removals"] += artifact
        series.append(
            {
                "at": row["at"],
                "event": row["event"],
                "connected_servers": row["servers"],
                "likely_reconnect": artifact,
            }
        )
    installs = sum(point["installs"] for point in daily.values())
    removals = sum(point["removals"] for point in daily.values())
    artifacts = sum(point["likely_reconnect_removals"] for point in daily.values())
    return {
        "installs": installs,
        "removals": removals,
        "likely_reconnect_removals": artifacts,
        "net_change": installs - (removals - artifacts),
        "truncated": len(rows) == MAX_MEMBERSHIP_EVENTS,
        "daily": [daily[day] for day in sorted(daily)],
        "events": series[-200:],
    }


def likely_reconnect(
    at: float,
    removals: list[float],
    starts: list[float],
    releases: list[float],
    shutdowns: list[float],
) -> bool:
    """Whether a removal is probably a startup artifact rather than a removal.

    Near the current process start or a recorded shutdown, any removal
    qualifies. Near the first event of a release, only a burst of at least two
    removals does, because a quiet release's first event can be much later
    than its start.
    """

    def within(markers: list[float], seconds: float) -> bool:
        # Markers are sorted: is there one in [at - seconds, at]?
        index = bisect.bisect_right(markers, at)
        return index > 0 and at - markers[index - 1] <= seconds

    if within(starts, RECONNECT_SECONDS) or within(
        shutdowns, SHUTDOWN_RECONNECT_SECONDS
    ):
        return True
    nearby = bisect.bisect_right(removals, at + RECONNECT_SECONDS) - bisect.bisect_left(
        removals, at - RECONNECT_SECONDS
    )
    return nearby >= 2 and within(releases, RECONNECT_SECONDS)


def store_panel(connection: sqlite3.Connection, path: str) -> dict:
    """Size and age of the retained history."""
    row = connection.execute(
        "SELECT (SELECT COUNT(*) FROM events) events, (SELECT COUNT(*) FROM sessions) sessions, "
        "(SELECT MIN(at) FROM events) oldest_event, (SELECT MAX(at) FROM events) newest_event"
    ).fetchone()
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    pages = connection.execute("PRAGMA page_count").fetchone()[0]
    free = connection.execute("PRAGMA freelist_count").fetchone()[0]
    try:
        wal_bytes = Path(path + "-wal").stat().st_size
    except OSError:
        wal_bytes = 0
    return {
        **dict(row),
        "database_bytes": page_size * pages,
        "free_bytes": page_size * free,
        "wal_bytes": wal_bytes,
    }


def lookup_panel(reference: str, sessions: list[dict], history: list[dict]) -> dict:
    """One session's timeline, without its internal or pseudonymous identifiers."""
    if not sessions:
        return {"reference": reference, "found": False, "session": None, "events": []}
    session = {
        key: sessions[0][key]
        for key in (
            "reference",
            "created",
            "updated",
            "revision",
            "is_test",
            "status",
            "files",
            "failures",
            "failure_data",
        )
    }
    return {
        "reference": reference,
        "found": True,
        "session": session,
        "events": history,
    }


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


GROUP_FIELDS = (
    "stage",
    "error_code",
    "engine_signal",
    "input_format",
    "browser",
    "device",
    "engine",
    "path",
)


def groups_panel(
    connection: sqlite3.Connection, where: str, args: list
) -> dict[str, list[dict]]:
    """Sessions and events per category value, top 20 per field.

    The window's events are read once into a temporary result; each field is
    then counted from it rather than by another scan of the event table.
    """
    columns = ", ".join(
        f"json_extract(e.data,'$.{field}') {field}" for field in GROUP_FIELDS
    )
    counts = " UNION ALL ".join(
        f"SELECT '{field}' field, {field} value, COUNT(DISTINCT session_id) sessions, "
        f"COUNT(*) events FROM x WHERE {field} IS NOT NULL GROUP BY 2"
        for field in GROUP_FIELDS
    )
    failure_stage = (
        "SELECT 'failure_stage' field, stage value, COUNT(DISTINCT session_id) sessions, "
        "NULL events FROM x WHERE error_code IS NOT NULL AND error_code!='CANCELLED' "
        "AND stage IS NOT NULL GROUP BY 2"
    )
    groups: dict[str, list[dict]] = {field: [] for field in GROUP_FIELDS}
    groups["failure_stage"] = []
    for row in connection.execute(
        "WITH x AS MATERIALIZED (SELECT e.session_id session_id, "
        + columns
        + " FROM events e JOIN sessions s ON s.id=e.session_id WHERE "
        + where
        + ") "
        + counts
        + " UNION ALL "
        + failure_stage,
        args,
    ):
        item = {"value": row["value"], "sessions": row["sessions"]}
        if row["field"] != "failure_stage":
            item["events"] = row["events"]
        groups[row["field"]].append(item)
    for rows in groups.values():
        rows.sort(key=lambda item: -item["sessions"])
        del rows[20:]
    groups["revision"] = [
        dict(row)
        for row in connection.execute(
            "SELECT revision value,COUNT(*) sessions,SUM(delivered) delivered FROM sessions s WHERE "
            + where
            + " GROUP BY revision ORDER BY sessions DESC LIMIT 20",
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
                "p50": percentile(samples, 0.5),
                "p90": percentile(samples, 0.9),
                "p95": percentile(samples, 0.95),
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
    for path in ("unchanged", "remuxed"):
        files[path + "_percent"] = (
            round(100 * files[path] / files["measured_files"], 1)
            if files["measured_files"]
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
