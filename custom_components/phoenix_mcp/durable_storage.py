"""Propagate HA storage failures before acknowledging critical write barriers."""

from collections.abc import Mapping, Sequence
from typing import Any

from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store
from homeassistant.util.file import WriteError
from homeassistant.util.json import SerializationError


class DurableStorageError(OSError):
    """A critical record was not confirmed written; no new effect may be dispatched."""


class DurableStore[T: Mapping[str, Any] | Sequence[Any]](Store[T]):
    """Use HA's writer while preventing its log-only error handling for barriers.

    HA 2026.7+ catches WriteError and SerializationError above _async_write_data.
    Translate those failures at that seam so callers retain their recovery state.
    A deferred shutdown save or read-only store cannot acknowledge a durable claim.
    """

    async def async_save(self, data: T) -> None:
        if self._read_only or self.hass.state is CoreState.stopping:
            raise DurableStorageError("Critical storage is unavailable for an immediate write.")
        await super().async_save(data)

    async def _async_write_data(self, data: dict) -> None:
        try:
            await super()._async_write_data(data)
        except (WriteError, SerializationError):
            # Store's outer handler must not turn a failed barrier into success.
            raise DurableStorageError("Critical storage could not be written.") from None
