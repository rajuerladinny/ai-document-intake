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
    data BLOB NOT NULL,                     -- empty when the file lives in external storage
    storage_key TEXT                        -- key in the local folder or S3 bucket, if any
);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,             -- stored lower-case
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'reviewer', 'client')),
    client_id TEXT REFERENCES clients(id),  -- set for client users only
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,            -- SHA-256 of the bearer token; the token itself is never stored
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

# Differences from the SQLite schema: binary column type, and an explicit
# insertion-order column (SQLite has the implicit rowid for that).
PG_SCHEMA = (
    SCHEMA.replace("data BLOB NOT NULL", "data BYTEA NOT NULL")
    .replace("    reject_reason TEXT\n);", "    reject_reason TEXT,\n    seq BIGSERIAL\n);")
    .replace("    detail TEXT\n);", "    detail TEXT,\n    seq BIGSERIAL\n);")
)

TABLES = ("sessions", "users", "files", "activity", "documents", "clients")  # child tables first


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
            self._db.execute("ALTER TABLE files ADD COLUMN IF NOT EXISTS storage_key TEXT")
        else:
            self._db = sqlite3.connect(target, check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA foreign_keys = ON")
            self._db.executescript(SCHEMA)
            self._order = "rowid"
            # Databases created before external storage existed lack this column.
            columns = {r[1] for r in self._db.execute("PRAGMA table_info(files)")}
            if "storage_key" not in columns:
                self._db.execute("ALTER TABLE files ADD COLUMN storage_key TEXT")
                self._db.commit()

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

    def save_file(self, document_id: str, media_type: str, data: bytes, storage_key: str | None = None) -> None:
        self._write(
            "INSERT INTO files (document_id, media_type, data, storage_key) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (document_id) DO UPDATE SET media_type = excluded.media_type, data = excluded.data,"
            " storage_key = excluded.storage_key",
            (document_id, media_type, data, storage_key),
        )

    def get_file(self, document_id: str) -> tuple[str, bytes] | None:
        found = self.get_file_record(document_id)
        return (found[0], found[1]) if found else None

    def get_file_record(self, document_id: str) -> tuple[str, bytes, str | None] | None:
        """(media type, bytes stored in the database, external storage key or None)."""
        row = self._one("SELECT media_type, data, storage_key FROM files WHERE document_id = ?", (document_id,))
        if not row:
            return None
        return row["media_type"], bytes(row["data"] or b""), row["storage_key"] or None

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

    # Users and sessions ------------------------------------------------------

    _USER_COLUMNS = "id, email, name, role, client_id, created_at, disabled"

    def add_user(self, email: str, name: str, role: str, password_hash: str,
                 client_id: str | None = None) -> dict[str, Any]:
        uid = new_id("usr")
        self._write(
            "INSERT INTO users (id, email, name, role, client_id, password_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, email.strip().lower(), name, role, client_id, password_hash, utc_now()),
        )
        return self.get_user(uid)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        row = self._one(f"SELECT {self._USER_COLUMNS} FROM users WHERE id = ?", (user_id,))
        return self._user(row) if row else None

    def user_with_password(self, email: str) -> tuple[dict[str, Any], str] | None:
        row = self._one(f"SELECT {self._USER_COLUMNS}, password_hash FROM users WHERE email = ?",
                        (email.strip().lower(),))
        return (self._user(row), row["password_hash"]) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        return [self._user(r) for r in self._all(f"SELECT {self._USER_COLUMNS} FROM users ORDER BY email")]

    def update_user(self, user_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {"name", "role", "client_id", "password_hash", "disabled"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Cannot update {sorted(unknown)}")
        if "disabled" in changes:
            changes["disabled"] = 1 if changes["disabled"] else 0
        sets = ", ".join(f"{k} = ?" for k in changes)
        self._write(f"UPDATE users SET {sets} WHERE id = ?", (*changes.values(), user_id))
        return self.get_user(user_id)

    @staticmethod
    def _user(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "client_id": row["client_id"],
            "created_at": row["created_at"],
            "disabled": str(row["disabled"]) in ("1", "True", "true"),
        }

    def add_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        self._write(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, user_id, utc_now(), expires_at),
        )

    def session_user(self, token_hash: str, now: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT u.id FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ? AND u.disabled = 0",
            (token_hash, now),
        )
        return self.get_user(row["id"]) if row else None

    def delete_sessions(self, *, token_hash: str | None = None, user_id: str | None = None) -> None:
        if token_hash:
            self._write("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        if user_id:
            self._write("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def delete_expired_sessions(self, now: str) -> None:
        self._write("DELETE FROM sessions WHERE expires_at <= ?", (now,))

    # Activity ----------------------------------------------------------------

    def log(self, actor: str, action: str, document_id: str | None = None, detail: str | None = None) -> None:
        self._write(
            "INSERT INTO activity (id, at, actor, action, document_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("act"), utc_now(), actor, action, document_id, detail),
        )

    def activity(self, document_id: str | None = None, limit: int = 100, *,
                 since: str | None = None, until: str | None = None,
                 document_ids: list[str] | None = None) -> list[dict[str, Any]]:
        query, params = "SELECT id, at, actor, action, document_id, detail FROM activity WHERE 1 = 1", []
        if document_id:
            query += " AND document_id = ?"
            params.append(document_id)
        if document_ids is not None:
            if not document_ids:
                return []
            query += f" AND document_id IN ({', '.join('?' for _ in document_ids)})"
            params.extend(document_ids)
        if since:
            query += " AND at >= ?"
            params.append(since)
        if until:
            query += " AND at < ?"
            params.append(until)
        rows = self._all(query + f" ORDER BY at DESC, {self._order} DESC LIMIT ?", (*params, limit))
        keys = ("id", "at", "actor", "action", "document_id", "detail")
        return [{k: r[k] for k in keys} for r in rows]


def _statements(script: str) -> Iterable[str]:
    # Drop "--" comments first: they may contain semicolons. The schema has no string literals.
    code = "\n".join(line.split("--", 1)[0] for line in script.splitlines())
    for chunk in code.split(";"):
        cleaned = chunk.strip()
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
