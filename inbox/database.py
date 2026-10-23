from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS import_batches (
    batch_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    exported_at TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    records_count INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS source_records (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    record_ref TEXT NOT NULL,
    applied_revision INTEGER NOT NULL,
    raw_payload_json TEXT NOT NULL,
    raw_transcript_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_type, source_id)
);

CREATE TABLE IF NOT EXISTS analyses (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL,
    output_kind TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('current', 'stale', 'pending')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_type, source_id),
    FOREIGN KEY (source_type, source_id)
        REFERENCES source_records(source_type, source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS processing_states (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    intake_state TEXT NOT NULL,
    source_reason TEXT,
    usable_text INTEGER NOT NULL CHECK (usable_text IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_type, source_id),
    FOREIGN KEY (source_type, source_id)
        REFERENCES source_records(source_type, source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS route_decisions (
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    suggested_routes_json TEXT NOT NULL DEFAULT '[]',
    route_reasons_json TEXT NOT NULL DEFAULT '[]',
    manual_routes_json TEXT NOT NULL DEFAULT '[]',
    manual_flag INTEGER NOT NULL DEFAULT 0 CHECK (manual_flag IN (0, 1)),
    note TEXT,
    who TEXT,
    decided_at TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_type, source_id),
    FOREIGN KEY (source_type, source_id)
        REFERENCES source_records(source_type, source_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_pk INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    record_ref TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('new', 'replaced', 'ignored-same', 'ignored-older')),
    logged_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (batch_pk) REFERENCES import_batches(batch_pk) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    source_type TEXT,
    source_id TEXT,
    record_ref TEXT,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TEXT
);
"""


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Database:
    def __init__(self, path: str | Path = "state/app.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    def import_delivery(self, delivery: dict[str, Any]) -> dict[str, Any]:
        batch_id = delivery.get("batch_id")
        revision = delivery.get("revision")
        records = delivery.get("records")
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("Delivery batch_id must be a non-empty string")
        if not isinstance(revision, int) or revision < 1:
            raise ValueError("Delivery revision must be a positive integer")
        if not isinstance(records, list):
            raise ValueError("Delivery records must be an array")

        for record in records:
            self._validate_record(record)

        actions: dict[str, int] = {
            "new": 0,
            "replaced": 0,
            "ignored-same": 0,
            "ignored-older": 0,
        }
        changed_records: list[tuple[str, str, str, str]] = []
        with self.connect() as connection:
            with connection:
                cursor = connection.execute(
                    "INSERT INTO import_batches (batch_id, revision, exported_at, records_count) "
                    "VALUES (?, ?, ?, ?)",
                    (batch_id, revision, delivery.get("exported_at"), len(records)),
                )
                batch_pk = cursor.lastrowid
                for record in records:
                    source_type = record["source_type"]
                    source_id = record["source_id"]
                    record_ref = record["record_ref"]
                    existing = connection.execute(
                        "SELECT applied_revision FROM source_records "
                        "WHERE source_type = ? AND source_id = ?",
                        (source_type, source_id),
                    ).fetchone()
                    if existing is None:
                        action = "new"
                        connection.execute(
                            "INSERT INTO source_records "
                            "(source_type, source_id, record_ref, applied_revision, raw_payload_json, raw_transcript_json) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                source_type,
                                source_id,
                                record_ref,
                                revision,
                                json_text(record["payload"]),
                                json_text(record.get("transcript_payload")),
                            ),
                        )
                    elif revision > existing["applied_revision"]:
                        action = "replaced"
                        connection.execute(
                            "UPDATE source_records SET record_ref = ?, applied_revision = ?, "
                            "raw_payload_json = ?, raw_transcript_json = ?, updated_at = CURRENT_TIMESTAMP "
                            "WHERE source_type = ? AND source_id = ?",
                            (
                                record_ref,
                                revision,
                                json_text(record["payload"]),
                                json_text(record.get("transcript_payload")),
                                source_type,
                                source_id,
                            ),
                        )
                        connection.execute(
                            "UPDATE analyses SET status = 'stale', updated_at = CURRENT_TIMESTAMP "
                            "WHERE source_type = ? AND source_id = ? AND source_revision <> ?",
                            (source_type, source_id, revision),
                        )
                    elif revision == existing["applied_revision"]:
                        action = "ignored-same"
                    else:
                        action = "ignored-older"

                    actions[action] += 1
                    if action in {"new", "replaced"}:
                        changed_records.append((action, source_type, source_id, record_ref))
                    connection.execute(
                        "INSERT INTO import_log "
                        "(batch_pk, source_type, source_id, record_ref, revision, action) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (batch_pk, source_type, source_id, record_ref, revision, action),
                    )

                new_count = actions["new"]
                if new_count:
                    connection.execute(
                        "INSERT INTO notifications (batch_id, revision, action, title, message) "
                        "VALUES (?, ?, 'new', ?, ?)",
                        (
                            batch_id,
                            revision,
                            "New records imported",
                            f"{new_count} new source record{'s were' if new_count != 1 else ' was'} added. Review the dashboard for call, meeting and attention totals.",
                        ),
                    )
                for action, source_type, source_id, record_ref in changed_records:
                    if action != "replaced":
                        continue
                    connection.execute(
                        "INSERT INTO notifications "
                        "(batch_id, revision, action, source_type, source_id, record_ref, title, message) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            batch_id,
                            revision,
                            action,
                            source_type,
                            source_id,
                            record_ref,
                            f"Source data changed: {record_ref}",
                            f"Revision {revision} replaced the complete source record and transcript. Review the latest conversation before relying on older analysis; any manual route is kept.",
                        ),
                    )

        return {"batch_id": batch_id, "revision": revision, "actions": actions}

    @staticmethod
    def _validate_record(record: Any) -> None:
        if not isinstance(record, dict):
            raise ValueError("Every delivery record must be an object")
        for field in ("record_ref", "source_type", "source_id", "payload"):
            if field not in record:
                raise ValueError(f"Delivery record is missing {field}")
        if record["source_type"] not in {"call", "meeting"}:
            raise ValueError("source_type must be call or meeting")
        if not isinstance(record["source_id"], str) or not record["source_id"]:
            raise ValueError("source_id must be a non-empty string")
        if not isinstance(record["record_ref"], str) or not record["record_ref"]:
            raise ValueError("record_ref must be a non-empty string")
        if not isinstance(record["payload"], dict):
            raise ValueError("payload must be an object")
        if record["payload"].get("id") != record["source_id"]:
            raise ValueError("payload.id must match the outer source_id")
        transcript = record.get("transcript_payload")
        if transcript is not None and not isinstance(transcript, dict):
            raise ValueError("transcript_payload must be an object or null")

    def clear(self) -> None:
        with self.connect() as connection:
            with connection:
                connection.execute("DELETE FROM notifications")
                connection.execute("DELETE FROM import_batches")
                connection.execute("DELETE FROM source_records")

    def notifications(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notifications ORDER BY notification_id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notifications_read(self) -> None:
        with self.connect() as connection:
            with connection:
                connection.execute(
                    "UPDATE notifications SET read_at = CURRENT_TIMESTAMP WHERE read_at IS NULL"
                )

    def records(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_records ORDER BY source_type, record_ref"
            ).fetchall()
        return [dict(row) for row in rows]