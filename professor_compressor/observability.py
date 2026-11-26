"""Bounded, best-effort diagnostic events; never a dependency of delivery.

One worker owns SQLite writes. Readers use separate read-only connections. Only
allowlisted scalar measurements cross this boundary, including for server data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import queue
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .observability_contract import (
    CLIENT_EVENTS,
    EVENTS,
    diagnostic_reference,
    sanitize,
    valid_id,
)
from .observability_queries import query_dashboard

logger = logging.getLogger(__name__)

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
        retention_days: int = 90,
        max_events: int = 300000,
        queue_size: int = 2048,
        max_mib: int = 384,
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
            if reference != diagnostic_reference(session_id):
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
                # Receipt handling can report a generic browser HTTP failure
                # after the server has already classified the delivery outcome.
                current[0] in {"uploading", "queued", "delivering", "delivery_failed"}
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
        """Return the current operator view without exposing the write connection."""
        if not self.ready:
            return {"storage": self.health(), "sessions": [], "events": []}
        return query_dashboard(
            self.path,
            self.health,
            days=days,
            include_tests=include_tests,
            reference=reference,
            dimension=dimension,
            value=value,
        )
