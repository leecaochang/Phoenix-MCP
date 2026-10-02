"""JSON file storage backend: one file per profile, keyed by URL-quoted key.

Quoting (rather than lossy character replacement) keeps the filename-to-key
mapping reversible, so ``list_keys`` can reconstruct keys exactly.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from custom_components.phoenix_mcp.mesa_core import backends
from custom_components.phoenix_mcp.mesa_core.exceptions import MesaValidationError
from custom_components.phoenix_mcp.mesa_core.json_io import loads


class JsonFileBackend(backends.StorageBackend):
    def __init__(self, base_path: str | Path, create_if_missing: bool = True) -> None:
        self.base_path = Path(base_path)
        self._directory_stamp: int | None = None
        self._filenames: set[str] = set()
        if create_if_missing:
            self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if not isinstance(key, str) or not key or key in (".", ".."):
            raise MesaValidationError("storage key must be a non-empty identifier")
        # Percent-encode uppercase characters too: keys remain distinct on
        # case-insensitive filesystems, while existing canonical HA keys stay put.
        encoded = "".join(
            f"%{ord(c):02X}" if c.isupper() and c.isascii() else quote(c, safe="") for c in key
        )
        if len(encoded.encode()) > 250:
            raise MesaValidationError("storage key exceeds filename limit")
        return self.base_path / f"{encoded}.json"

    def _names(self) -> set[str]:
        stamp = self.base_path.stat().st_mtime_ns
        if stamp != self._directory_stamp:
            self._filenames = {path.name for path in self.base_path.iterdir()}
            self._directory_stamp = stamp
        return self._filenames

    def _existing_path(self, key: str) -> Path:
        canonical = self._path(key)
        legacy_name = quote(key, safe="") + ".json"
        names = self._names()
        if canonical.name in names:
            if legacy_name != canonical.name and legacy_name in names:
                raise MesaValidationError("conflicting legacy and canonical profile filenames")
            return canonical
        if legacy_name in names:
            return self.base_path / legacy_name
        return canonical

    def read(self, key: str) -> dict[str, Any] | None:
        path = self._existing_path(key)
        # Check exact spelling before opening on case-insensitive filesystems.
        if path.name not in self._names():
            return None
        if not path.exists():
            return None
        data: dict[str, Any] = loads(path.read_text(encoding="utf-8"))
        return data

    def write(self, key: str, data: dict[str, Any]) -> None:
        destination = self._path(key)
        existing = self._existing_path(key)
        payload = json.dumps(data, indent=2) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.base_path, suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if existing.name in self._names():
                os.chmod(temporary, existing.stat().st_mode & 0o777)
            os.replace(temporary, destination)
            if existing != destination:
                existing.unlink(missing_ok=True)
            self._directory_stamp = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        path = self._existing_path(key)
        if path.name in self._names():
            path.unlink(missing_ok=True)
        self._directory_stamp = None

    def list_keys(self, prefix: str | None = None) -> list[str]:
        keys = []
        for path in self.base_path.iterdir():
            if path.suffix != ".json" or not path.is_file():
                continue
            key = unquote(path.stem)
            try:
                canonical = self._path(key)
            except MesaValidationError:
                continue
            if path.name in (canonical.name, quote(key, safe="") + ".json"):
                keys.append(key)
        keys = sorted(set(keys))
        if prefix is not None:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys
