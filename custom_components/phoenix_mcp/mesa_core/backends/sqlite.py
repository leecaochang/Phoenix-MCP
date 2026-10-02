"""SQLite storage backend using the standard library.

One synchronized connection per backend instance; close it at host shutdown.
Async access goes through the ProfileStore's
``a``-prefixed methods, which offload to a thread.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from custom_components.phoenix_mcp.mesa_core import backends
from custom_components.phoenix_mcp.mesa_core.json_io import loads


class SqliteBackend(backends.StorageBackend):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS profiles (key TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SqliteBackend:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        if hasattr(self, "_connection"):
            self.close()

    def read(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM profiles WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = loads(row[0])
        return data

    def write(self, key: str, data: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO profiles (key, data) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (key, json.dumps(data)),
            )

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profiles WHERE key = ?", (key,))

    def list_keys(self, prefix: str | None = None) -> list[str]:
        query = "SELECT key FROM profiles"
        params: tuple[Any, ...] = ()
        if prefix is not None:
            query += " WHERE substr(key, 1, ?) = ? COLLATE BINARY"
            params = (len(prefix), prefix)
        query += " ORDER BY key"
        with self._connect() as conn:
            return [row[0] for row in conn.execute(query, params)]
