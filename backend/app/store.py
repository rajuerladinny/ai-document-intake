"""Persistence for clients, documents, original files and the activity log.

Two backends share the same queries:

* **SQLite** (default): standard library only, ideal for the demo, tests and
  a single small office. ``Store(":memory:")`` or ``Store("intake.db")``.
* **PostgreSQL**: for real deployments. ``Store("postgresql://user:pass@host/db")``
  needs the ``psycopg`` package (``pip install "psycopg[binary]"``).

Queries are written once with ``?`` placeholders and converted for PostgreSQL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contact_email TEXT NOT NULL,
    required_types TEXT NOT NULL            -- JSON list of document types
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES clients(id),
    file_name TEXT NOT NULL,
    text TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('needs_review', 'ready', 'approved', 'rejected')),
    extraction TEXT NOT NULL,               -- JSON produced by the extractor
    extractor TEXT NOT NULL,
    reviewed_by TEXT,
    reviewed_at TEXT,
    reject_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_client ON documents(client_id);
CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    document_id TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_activity_document ON activity(document_id);
-- Original uploads, kept apart so listing documents stays light.
CREATE TABLE IF NOT EXISTS files (
    document_id TEXT PRIMARY KEY REFERENCES documents(id),
    media_type TEXT NOT NULL,
    data BLOB NOT NULL
);
"""

# Differences from the SQLite schema: binary column type, and an explicit
# insertion-order column (SQLite has the implicit rowid for that).
PG_SCHEMA = (
    SCHEMA.replace("data BLOB NOT NULL", "data BYTEA NOT NULL")
    .replace("    reject_reason TEXT\n);", "    reject_reason TEXT,\n    seq BIGSERIAL\n);")
    .replace("    detail TEXT\n);", "    detail TEXT,\n    seq BIGSERIAL\n);")
)

TABLES = ("files", "activity", "documents", "clients")  # child tables first


def is_postgres_url(target: str) -> bool:
    return target.startswith(("postgres://", "postgresql://"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class StoreConfigError(RuntimeError):
    """The database could not be opened with the given settings."""


class Store:
    def __init__(self, target: str | Path = ":memory:") -> None:
        self._lock = threading.Lock()
        target = str(target)
        if target.startswith("sqlite:///"):
            target = target[len("sqlite:///"):] or ":memory:"
        self.backend = "postgresql" if is_postgres_url(target) else "sqlite"
        if self.backend == "postgresql":
            self._db = _connect_postgres(target)
            self._order = "seq"
            for statement in _statements(PG_SCHEMA):
                self._db.execute(statement)
        else:
            self._db = sqlite3.connect(target, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(SCHEMA)
            self._order = "rowid"

    def close(self) -> None:
        self._db.close()

    def _sql(self, query: str) -> str:
        return query.replace("?", "%s") if self.backend == "postgresql" else query

    def _all(self, query: str, params: tuple | list = ()) -> list[Any]:
        with self._lock:
            return self._db.execute(self._sql(query), params).fetchall()

    def _one(self, query: str, params: tuple | list = ()) -> Any | None:
        with self._lock:
            return self._db.execute(self._sql(query), params).fetchone()

    def _write(self, query: str, params: tuple | list = ()) -> None:
        """One statement, committed straight away (each write here is a single statement)."""
        with self._lock:
            self._db.execute(self._sql(query), params)
            if self.backend == "sqlite":
                self._db.commit()

    def reset(self) -> None:
        """Delete all data. Used by tests and the demo reset."""
        for table in TABLES:
            self._write(f"DELETE FROM {table}")

    # Clients -----------------------------------------------------------------

    def add_client(self, name: str, contact_email: str, required_types: list[str],
                   client_id: str | None = None) -> dict[str, Any]:
        cid = client_id or new_id("cli")
        self._write(
            "INSERT INTO clients (id, name, contact_email, required_types) VALUES (?, ?, ?, ?)",
            (cid, name, contact_email, json.dumps(required_types)),
        )
        return self.get_client(cid)  # type: ignore[return-value]

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM clients WHERE id = ?", (client_id,))
        return self._client(row) if row else None

    def list_clients(self) -> list[dict[str, Any]]:
        rows = self._all("SELECT * FROM clients ORDER BY name")
        return [self._client(r) for r in rows]

    @staticmethod
    def _client(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "contact_email": row["contact_email"],
            "required_types": json.loads(row["required_types"]),
        }

    # Documents ---------------------------------------------------------------

    def add_document(self, client_id: str, file_name: str, text: str, status: str,
                     extraction: dict[str, Any], extractor: str) -> dict[str, Any]:
        did = new_id("doc")
        self._write(
            "INSERT INTO documents (id, client_id, file_name, text, uploaded_at, status, extraction, extractor)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (did, client_id, file_name, text, utc_now(), status, json.dumps(extraction), extractor),
        )
        return self.get_document(did)  # type: ignore[return-value]

    _DOC_SELECT = (
        "SELECT d.*, f.media_type AS file_media_type FROM documents d"
        " LEFT JOIN files f ON f.document_id = d.id"
    )

    def save_file(self, document_id: str, media_type: str, data: bytes) -> None:
        self._write(
            "INSERT INTO files (document_id, media_type, data) VALUES (?, ?, ?)"
            " ON CONFLICT (document_id) DO UPDATE SET media_type = excluded.media_type, data = excluded.data",
            (document_id, media_type, data),
        )

    def get_file(self, document_id: str) -> tuple[str, bytes] | None:
        row = self._one("SELECT media_type, data FROM files WHERE document_id = ?", (document_id,))
        return (row["media_type"], bytes(row["data"])) if row else None

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        row = self._one(self._DOC_SELECT + " WHERE d.id = ?", (document_id,))
        return self._document(row) if row else None

    def list_documents(self, client_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        query, params = self._DOC_SELECT + " WHERE 1 = 1", []
        if client_id:
            query += " AND d.client_id = ?"
            params.append(client_id)
        if status:
            query += " AND d.status = ?"
            params.append(status)
        rows = self._all(query + f" ORDER BY d.uploaded_at DESC, d.{self._order} DESC", params)
        return [self._document(r) for r in rows]

    def update_document(self, document_id: str, **changes: Any) -> dict[str, Any]:
        allowed = {"status", "extraction", "reviewed_by", "reviewed_at", "reject_reason"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Cannot update {sorted(unknown)}")
        if "extraction" in changes:
            changes["extraction"] = json.dumps(changes["extraction"])
        sets = ", ".join(f"{k} = ?" for k in changes)
        self._write(f"UPDATE documents SET {sets} WHERE id = ?", (*changes.values(), document_id))
        return self.get_document(document_id)  # type: ignore[return-value]

    @staticmethod
    def _document(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "client_id": row["client_id"],
            "file_name": row["file_name"],
            "text": row["text"],
            "uploaded_at": row["uploaded_at"],
            "status": row["status"],
            "extraction": json.loads(row["extraction"]),
            "extractor": row["extractor"],
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": row["reviewed_at"],
            "reject_reason": row["reject_reason"],
            "file_media_type": row["file_media_type"],
        }

    # Activity ----------------------------------------------------------------

    def log(self, actor: str, action: str, document_id: str | None = None, detail: str | None = None) -> None:
        self._write(
            "INSERT INTO activity (id, at, actor, action, document_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("act"), utc_now(), actor, action, document_id, detail),
        )

    def activity(self, document_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query, params = "SELECT id, at, actor, action, document_id, detail FROM activity", []
        if document_id:
            query += " WHERE document_id = ?"
            params.append(document_id)
        rows = self._all(query + f" ORDER BY at DESC, {self._order} DESC LIMIT ?", (*params, limit))
        keys = ("id", "at", "actor", "action", "document_id", "detail")
        return [{k: r[k] for k in keys} for r in rows]


def _statements(script: str) -> Iterable[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    for chunk in "\n".join(lines).split(";"):
        cleaned = "\n".join(line.split("--")[0] for line in chunk.splitlines()).strip()
        if cleaned:
            yield cleaned


def _connect_postgres(url: str) -> Any:
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise StoreConfigError(
            'DATABASE_URL points at PostgreSQL, but psycopg is not installed. Run: pip install "psycopg[binary]"'
        ) from exc
    try:
        # Autocommit: every write is a single statement, so each one is its own transaction.
        return psycopg.connect(url, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        raise StoreConfigError(f"Could not connect to PostgreSQL: {exc}") from exc
