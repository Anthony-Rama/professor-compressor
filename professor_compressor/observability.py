"""Bounded, best-effort diagnostic events; never a dependency of delivery.

One worker owns SQLite writes. Readers use separate read-only connections. Only
allowlisted scalar measurements cross this boundary, including for server data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import math
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)
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
    {"stage_started", "stage_finished", "file_ready", "retry", "client_failed"}
)
ENUMS = {
    "stage": {
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
        "UNKNOWN",
        "FILE_READ_FAILED",
        "UNSUPPORTED_CONTAINER",
        "INVALID_SIGNATURE",
        "METADATA_TIMEOUT",
        "METADATA_INVALID",
        "NO_VIDEO_STREAM",
        "DURATION_UNKNOWN",
        "UNSUPPORTED_CODEC",
        "ENGINE_LOAD_FAILED",
        "ENGINE_LOAD_TIMEOUT",
        "ENCODE_FAILED",
        "OUTPUT_TOO_LARGE",
        "BROWSER_MEMORY",
        "NETWORK",
        "HTTP",
        "SESSION_EXPIRED",
        "CANCELLED",
        "UPLOAD_TIMEOUT",
        "INVALID_UPLOAD",
        "RELAY_ERROR",
        "DISCORD_REJECTED",
        "DELIVERY_TIMEOUT",
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
            "queue_seconds",
            "discord_delivery_seconds",
        ),
        86400,
    ),
    **dict.fromkeys(
        ("input_bytes", "output_bytes", "target_bytes", "discord_limit"), 2**40
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
            "files",
            "selected_count",
        ),
        100,
    ),
    "browser_major": 999,
    "connected_servers": 10000000,
}


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
    for key in ("retryable", "retry_recovered"):
        if type(data.get(key)) is bool:
            clean[key] = data[key]
    return clean


SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
 id TEXT PRIMARY KEY, reference TEXT UNIQUE NOT NULL, created REAL NOT NULL,
 updated REAL NOT NULL, revision TEXT NOT NULL, user_key TEXT, guild_key TEXT,
 is_test INTEGER NOT NULL, status TEXT NOT NULL, opened INTEGER DEFAULT 0,
 selected INTEGER DEFAULT 0, started INTEGER DEFAULT 0, delivered INTEGER DEFAULT 0,
 files INTEGER DEFAULT 0, failures INTEGER DEFAULT 0, active_run TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, at REAL NOT NULL,
 session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 run_id TEXT NOT NULL, file_index INTEGER NOT NULL, event TEXT NOT NULL,
 source TEXT NOT NULL, data TEXT NOT NULL, UNIQUE(session_id, event_id)
);
CREATE INDEX IF NOT EXISTS events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS events_time ON events(at);
CREATE INDEX IF NOT EXISTS sessions_time ON sessions(created);
CREATE INDEX IF NOT EXISTS sessions_reference ON sessions(reference);
"""


class EventStore:
    def __init__(
        self,
        path: str = "",
        *,
        key: str = "",
        retention_days: int = 30,
        max_events: int = 100000,
        queue_size: int = 2048,
        max_mib: int = 128,
    ) -> None:
        self.path = path
        self._key = key.encode()
        self.retention_days = retention_days
        self.max_events = max_events
        self.max_mib = max_mib
        self._queue: queue.Queue = queue.Queue(queue_size)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ready = False
        self.dropped = 0
        self.errors = 0

    def pseudonym(self, kind: str, value: object) -> str:
        if not self._key or value is None:
            return ""
        return hmac.new(
            self._key, f"{kind}:{value}".encode(), hashlib.sha256
        ).hexdigest()[:32]

    def start(self) -> None:
        if self.path and self._thread is None:
            self._thread = threading.Thread(
                target=self._worker, name="diagnostic-writer", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.ready = False

    def record(
        self,
        session_id: str,
        reference: str,
        event: str,
        *,
        run_id: str = "",
        file_index: int = 0,
        source: str = "server",
        data: object = None,
        revision: str = "development",
        user_key: str = "",
        guild_key: str = "",
        is_test: bool = False,
        event_id: str = "",
    ) -> None:
        """Never raise or wait for disk; backpressure loses diagnostics, not videos."""
        if not self.path or self._stop.is_set():
            return
        try:
            if not valid_id(session_id) or event not in EVENTS:
                return
            if source == "browser" and event not in CLIENT_EVENTS | {"client_complete"}:
                return
            if reference != "PC-" + session_id[:12].upper():
                return
            if type(file_index) is not int or not 0 <= file_index <= 10:
                return
            row = (
                session_id,
                reference,
                event,
                valid_id(run_id),
                file_index,
                "browser" if source == "browser" else "server",
                sanitize(data),
                re.sub(r"[^a-zA-Z0-9._-]", "", revision)[:64],
                valid_id(user_key),
                valid_id(guild_key),
                bool(is_test),
                valid_id(event_id) or uuid.uuid4().hex,
                time.time(),
            )
            self._queue.put_nowait(row)
        except queue.Full:
            self.dropped += 1
        except Exception:
            self.errors += 1

    def _connection(self) -> sqlite3.Connection:
        path = Path(self.path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=0.25)
        try:
            self._initialize_connection(connection, path)
        except Exception:
            connection.close()
            raise
        return connection

    def _initialize_connection(
        self, connection: sqlite3.Connection, path: Path
    ) -> None:
        path.chmod(0o600)
        if connection.execute("PRAGMA user_version").fetchone()[0] not in {0, 1}:
            raise RuntimeError("Unsupported diagnostic schema version")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA journal_size_limit=2097152")
        connection.execute("PRAGMA wal_autocheckpoint=100")
        connection.execute(
            f"PRAGMA max_page_count={self.max_mib * 1024 * 1024 // 4096}"
        )
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version=1")
        # Process restarts cannot recover browser processing or volatile deliveries.
        connection.execute(
            "UPDATE sessions SET status='unknown' WHERE status IN ('processing','uploading','queued','delivering')"
        )
        connection.commit()

    def _save(self, connection: sqlite3.Connection, row: tuple) -> None:
        (
            session_id,
            reference,
            event,
            run_id,
            index,
            source,
            data,
            revision,
            user_key,
            guild_key,
            is_test,
            event_id,
            now,
        ) = row
        connection.execute(
            "INSERT OR IGNORE INTO sessions(id,reference,created,updated,revision,user_key,guild_key,is_test,status) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                reference,
                now,
                now,
                revision,
                user_key,
                guild_key,
                is_test,
                "unknown",
            ),
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO events(event_id,at,session_id,run_id,file_index,event,source,data) VALUES(?,?,?,?,?,?,?,?)",
            (event_id, now, session_id, run_id, index, event, source, json.dumps(data)),
        )
        if cursor.rowcount == 0:
            if event == "client_failed":
                previous = connection.execute(
                    "SELECT data FROM events WHERE session_id=? AND event_id=? AND event='client_failed' AND run_id=?",
                    (session_id, event_id, run_id),
                ).fetchone()
                if previous:
                    existing = json.loads(previous[0])
                    merged = {**existing, **data}
                    if data.get("error_code") == "UNKNOWN" and existing.get(
                        "error_code"
                    ):
                        merged["error_code"] = existing["error_code"]
                    connection.execute(
                        "UPDATE events SET data=?,file_index=CASE WHEN ?>0 THEN ? ELSE file_index END WHERE session_id=? AND event_id=?",
                        (json.dumps(merged), index, index, session_id, event_id),
                    )
            return
        statuses = {
            "created": "created",
            "opened": "opened",
            "selected": "selected",
            "started": "processing",
            "client_failed": "client_failed",
            "cancelled": "cancelled",
            "expired": "expired",
            "replaced": "replaced",
            "interrupted": "unknown",
            "upload_started": "uploading",
            "received": "queued",
            "delivery_started": "delivering",
            "delivered": "delivered",
            "delivery_failed": "delivery_failed",
            "upload_failed": "upload_failed",
            "installed": "installed",
            "removed": "removed",
        }
        status = statuses.get(event)
        current = connection.execute(
            "SELECT status,started,active_run FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if event in {"opened", "selected"} and current[0] not in {
            "unknown",
            "created",
            "opened",
            "selected",
        }:
            status = None
        if event == "expired":
            status = (
                current[0]
                if current[0]
                in {"client_failed", "delivery_failed", "upload_failed", "cancelled"}
                else ("unknown" if current[1] else "expired")
            )
        if (
            source == "browser"
            and event == "client_failed"
            and (
                current[0] in {"uploading", "queued", "delivering"}
                or (current[2] and run_id != current[2])
            )
        ):
            status = None
        connection.execute(
            "UPDATE sessions SET updated=?, "
            "status=CASE WHEN delivered=1 THEN status ELSE COALESCE(?,status) END, "
            "opened=MAX(opened,?), selected=MAX(selected,?), started=MAX(started,?), "
            "delivered=MAX(delivered,?), "
            "files=CASE WHEN ?='delivered' AND ?='server' THEN ? ELSE files END, failures=failures+?, "
            "active_run=CASE WHEN ?='started' THEN ? ELSE active_run END WHERE id=?",
            (
                now,
                status,
                event == "opened",
                event == "selected",
                event in {"started", "received", "delivered"},
                event == "delivered" and source == "server",
                event,
                source,
                data.get("files", 0),
                event in {"client_failed", "delivery_failed", "upload_failed"},
                event,
                run_id,
                session_id,
            ),
        )

    def _prune(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "DELETE FROM sessions WHERE updated < ?",
            (time.time() - self.retention_days * 86400,),
        )
        connection.execute(
            "DELETE FROM events WHERE id IN (SELECT id FROM events ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (self.max_events,),
        )
        connection.execute(
            "DELETE FROM sessions WHERE id IN (SELECT id FROM sessions ORDER BY updated DESC LIMIT -1 OFFSET ?)",
            (max(1000, self.max_events // 5),),
        )

    def _worker(self) -> None:
        connection = None
        try:
            connection = self._connection()
            self._prune(connection)
            connection.commit()
            self.ready = True
            last_prune = time.monotonic()
            while not self._stop.is_set() or not self._queue.empty():
                try:
                    row = self._queue.get(timeout=0.25)
                except queue.Empty:
                    row = None
                try:
                    if time.monotonic() - last_prune >= 60:
                        self._prune(connection)
                        # Commit pruning separately: a failed insert must not undo
                        # reclaimed space and prevent recovery at the storage cap.
                        connection.commit()
                        last_prune = time.monotonic()
                    if row is not None:
                        self._save(connection, row)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    self.errors += 1
                    if self.errors == 1 or self.errors % 100 == 0:
                        logger.warning(
                            "Diagnostic storage write failed; video delivery is unaffected"
                        )
                finally:
                    if row is not None:
                        self._queue.task_done()
        except Exception:
            self.errors += 1
            logger.warning(
                "Diagnostic storage unavailable; video delivery is unaffected"
            )
        finally:
            self.ready = False
            if connection is not None:
                connection.close()

    def health(self) -> dict:
        return {
            "configured": bool(self.path),
            "ready": self.ready,
            "queued": self._queue.qsize(),
            "dropped": self.dropped,
            "errors": self.errors,
            "retention_days": self.retention_days,
            "max_events": self.max_events,
        }

    def query(
        self,
        *,
        days: int = 7,
        include_tests: bool = False,
        reference: str = "",
        dimension: str = "",
        value: str = "",
    ) -> dict:
        """Bounded operator-only query. All browser facts are explicitly untrusted."""
        if not self.ready:
            return {"storage": self.health(), "sessions": [], "events": []}
        connection = sqlite3.connect(
            Path(self.path).resolve().as_uri() + "?mode=ro", uri=True, timeout=0.5
        )
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")  # One consistent WAL snapshot for all panels.
            deadline = time.monotonic() + 3
            connection.set_progress_handler(
                lambda: int(time.monotonic() > deadline), 10000
            )
            cutoff = time.time() - max(1, min(30, days)) * 86400
            where = "s.created >= ? AND (? OR s.is_test=0) AND s.status NOT IN ('installed','removed')"
            args: list = [cutoff, include_tests]
            if (
                dimension
                in {
                    "stage",
                    "error_code",
                    "input_format",
                    "browser",
                    "device",
                    "engine",
                    "path",
                    "revision",
                }
                and value
            ):
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
            totals = dict(
                connection.execute(
                    "SELECT COUNT(*) sessions, COALESCE(SUM(opened),0) opened, COALESCE(SUM(selected),0) selected, "
                    "COALESCE(SUM(started),0) started, COALESCE(SUM(delivered),0) delivered, "
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
            retries = dict(
                connection.execute(
                    "SELECT COUNT(DISTINCT CASE WHEN e.event='started' AND e.run_id!='' THEN e.session_id||e.run_id END) runs, "
                    "COALESCE(SUM(e.event='retry'),0) automatic_retries "
                    "FROM events e JOIN sessions s ON s.id=e.session_id WHERE " + where,
                    args,
                ).fetchone()
            )
            session_where, session_args = where, list(args)
            sessions = [
                dict(row)
                for row in connection.execute(
                    "SELECT id,reference,created,updated,revision,is_test, "
                    "CASE WHEN status IN ('processing','uploading','queued','delivering') AND updated < strftime('%s','now')-7200 THEN 'unknown' ELSE status END status, "
                    "CASE WHEN delivered=1 THEN files ELSE 0 END files,failures,user_key,guild_key "
                    "FROM sessions s WHERE "
                    + session_where
                    + " ORDER BY updated DESC LIMIT 100",
                    session_args,
                )
            ]
            history = []
            if search and sessions:
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
            groups = {}
            for field in (
                "stage",
                "error_code",
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
                    if field not in {"engine_seconds", "encoding_seconds"}
                    or row["value"] > 0
                )
                if samples:
                    timings[field] = {
                        "samples": len(samples),
                        "p50": samples[math.ceil(len(samples) * 0.5) - 1],
                        "p95": samples[math.ceil(len(samples) * 0.95) - 1],
                    }
            activity_where = "e.at >= ? AND (? OR s.is_test=0)"
            activity = [
                dict(row)
                for row in connection.execute(
                    "SELECT date(e.at,'unixepoch') day,e.event,COUNT(*) count FROM events e JOIN sessions s ON s.id=e.session_id "
                    "WHERE "
                    + activity_where
                    + " AND e.event IN ('installed','removed','created','delivered') GROUP BY 1,2 ORDER BY 1",
                    [cutoff, include_tests],
                )
            ]
            repeat = dict(
                connection.execute(
                    "SELECT COUNT(*) activated_servers,COALESCE(SUM(deliveries>1),0) repeat_servers FROM "
                    "(SELECT guild_key,COUNT(*) deliveries FROM sessions s WHERE "
                    + where
                    + " AND delivered=1 AND guild_key!='' GROUP BY guild_key)",
                    args,
                ).fetchone()
            )
            performance = [
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
                    "SELECT COUNT(*) FROM sessions s WHERE "
                    + where
                    + " AND s.is_test=1",
                    [cutoff, True, *args[2:]],
                ).fetchone()[0]
            )
            return {
                "storage": self.health(),
                "totals": totals,
                "statuses": statuses,
                "groups": groups,
                "timings": timings,
                "activity": activity,
                "adoption": repeat,
                "performance": performance,
                "files": files,
                "retries": retries,
                "collection": collection,
                "sessions": sessions,
                "events": history,
            }
        finally:
            connection.close()
