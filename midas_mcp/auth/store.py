"""SQLite persistence for the authorization server.

Kept deliberately small: clients, in-flight authorization requests, one-time
codes, and issued tokens. Volume is a handful of writes per user per month, so
a single connection behind a lock costs nothing and sidesteps every threading
pitfall sqlite3 has.

The file lives on the instance's disk (a Docker volume in the AWS deployment)
so authorizations survive an image swap. Replacing the instance loses them and
users re-authorize.
"""

from __future__ import annotations

import os
import sqlite3
import threading

DEFAULT_DB_PATH = "/data/auth.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    info      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
    rid        TEXT PRIMARY KEY,
    params     TEXT NOT NULL,
    client_id  TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS codes (
    code       TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    mapi_key   TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,           -- access | refresh
    client_id  TEXT NOT NULL,
    scopes     TEXT NOT NULL,
    subject    TEXT NOT NULL,
    resource   TEXT,
    mapi_key   TEXT NOT NULL,
    expires_at INTEGER
);
"""


class Store:
    """Thread-safe, tiny SQLite wrapper."""

    def __init__(self, path: str | None = None) -> None:
        # Open (creating parent dirs) the SQLite file and install the schema.
        path = path or os.environ.get("MIDAS_AUTH_DB", DEFAULT_DB_PATH)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def run(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        # Execute one statement under the lock, commit, and return every row.
        with self._lock:
            cur = self._db.execute(sql, args)
            rows = cur.fetchall()
            self._db.commit()
            return rows

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        # Run the query and hand back just the first row, or None if empty.
        rows = self.run(sql, args)
        return rows[0] if rows else None
