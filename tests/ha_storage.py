"""Home Assistant's real storage writer, on every release the suite runs on.

Core moved file access out of `Store` into `_FileStoreIO` in 2026.11. This module
is imported during collection, so it captures the real classes before the test
package's fixtures swap in their in-memory stand-ins.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

from homeassistant.helpers import storage
from homeassistant.helpers.storage import Store

_REAL_IO = getattr(storage, "_FileStoreIO", None)
_REAL_WRITE = Store._async_write_data
_REAL_LOAD = Store._async_load
_REAL_REMOVE = Store.async_remove

# The class and method holding the final write, which takes (owner, mode, data).
WRITER = _REAL_IO or Store
WRITE = "_write" if _REAL_IO else "_write_prepared_data"
real_writer = getattr(WRITER, WRITE)


@contextmanager
def real_storage() -> Iterator[None]:
    """Run stores created inside this block against the real filesystem."""
    if _REAL_IO:
        with patch.object(storage, "_FileStoreIO", _REAL_IO):
            yield
        return
    with (patch.object(Store, "_async_write_data", _REAL_WRITE),
          patch.object(Store, "_async_load", _REAL_LOAD),
          patch.object(Store, "async_remove", _REAL_REMOVE)):
        yield


def key_of(owner) -> str:
    """The storage key behind the writer's owner, a store or its I/O object."""
    return getattr(owner, "key", None) or owner._key


def write_prepared(store: Store, mode: str, data: str) -> None:
    """Call the final write of this store directly, in the executor."""
    getattr(getattr(store, "_io", store), WRITE)(mode, data)
