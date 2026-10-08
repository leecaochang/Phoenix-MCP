"""Select the validation engine of Home Assistant or an existing schema."""

from __future__ import annotations

import importlib
from typing import Any

import voluptuous as vol


def schema_engine(schema: Any = None) -> Any:
    """Use the owning engine, even when both validation packages are installed."""
    if schema is None:
        from homeassistant.helpers import config_validation as cv

        schema = cv.PLATFORM_SCHEMA
    if type(schema).__module__.startswith("probatio."):
        return importlib.import_module("probatio")
    if isinstance(schema, vol.Schema):
        return vol
    raise TypeError("Unsupported Home Assistant validation engine")
