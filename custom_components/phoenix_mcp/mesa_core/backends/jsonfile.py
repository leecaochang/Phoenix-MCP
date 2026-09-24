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


class JsonFileBackend(backends.StorageBackend):
    def __init__(self, base_path: str | Path, create_if_missing: bool = True) -> None:
        self.base_path = Path(base_path)
        if create_if_missing:
            self.base_path.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.base_path / f"{quote(key, safe='')}.json"

    def read(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def write(self, key: str, data: dict[str, Any]) -> None:
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
            os.replace(temporary, self._path(key))
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def list_keys(self, prefix: str | None = None) -> list[str]:
        keys = sorted(unquote(p.stem) for p in self.base_path.glob("*.json"))
        if prefix is not None:
            keys = [k for k in keys if k.startswith(prefix)]
        return keys
