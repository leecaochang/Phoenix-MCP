"""Resolve configured consumers against current HA identities for one read request."""

from __future__ import annotations

import json
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .policy_engine import Permission, resolve_registry_access
from .token_store import TokenRecord


IDENTITY_COVERAGE = {
    "kind": "consumer_identity",
    "reason": "YAML consumers without a verified Home Assistant identity are omitted; an empty result does not prove there are no consumers.",
}


def _config_key(config: Any) -> str | None:
    """Unsupported values cannot become a guessed identity through stringification."""
    try:
        return json.dumps(config, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return None


def _scene_key(name: Any, icon: Any, states: dict) -> str | None:
    return _config_key({"name": name, "icon": icon, "entities": {
        eid: {"state": state.state, "attributes": dict(state.attributes)}
        for eid, state in states.items()
    }})


class ConsumerIdentityIndex:
    """Snapshot identities once, then memoize visibility only within a synchronous read."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._registry: dict[tuple[str, str], set[str]] = {}
        self._loaded: dict[tuple[str, str], list[str]] = {}
        self._visible: dict[tuple[str, str, str | None], bool] = {}
        for entry in er.async_get(hass).entities.values():
            self._registry.setdefault((entry.domain, entry.unique_id), set()).add(entry.entity_id)
        for kind in ("automation", "scene"):
            component = hass.data.get(kind)
            for entity in getattr(component, "entities", ()):
                eid = getattr(entity, "entity_id", None)
                if not isinstance(eid, str) or not eid.startswith(kind + "."):
                    continue
                if kind == "automation":
                    raw = getattr(entity, "raw_config", None)
                    key = _config_key(raw) if isinstance(raw, dict) and not raw.get("id") else None
                else:
                    scene = getattr(entity, "scene_config", None)
                    key = _scene_key(scene.name, scene.icon, scene.states) if scene is not None and not scene.id else None
                if key is not None:
                    self._loaded.setdefault((kind, key), []).append(eid)

    def config_identity(self, kind: str, config: dict) -> str | None:
        """Match loaded configuration, never a slug or a blanket domain grant."""
        if config.get("id"):
            return str(config["id"])
        if kind == "scene":
            import voluptuous as vol
            from homeassistant.components.homeassistant.scene import STATES_SCHEMA

            try:
                states = STATES_SCHEMA(config.get("entities"))
            except (vol.Invalid, TypeError, ValueError):
                return None
            key = _scene_key(config.get("name"), config.get("icon"), states)
        else:
            key = _config_key(config)
        matches = self._loaded.get((kind, key), []) if key is not None else []
        # Duplicate identical configurations cannot prove which identity owns a row.
        return matches[0].partition(".")[2] if len(matches) == 1 else None

    def bind(self, rows: list[dict]) -> list[dict]:
        """Remove private configuration evidence before any projection can escape."""
        for row in rows:
            config = row.pop("_identity_config", None)
            if config is not None:
                row["id"] = self.config_identity(row["kind"], config) or ""
                if row["id"]:
                    row["entity_id"] = f"{row['kind']}.{row['id']}"
        return rows

    def visible(self, kind: str, identity: str, token: TokenRecord, hass: HomeAssistant, entity_id: str | None = None) -> bool:
        key = (kind, identity, entity_id)
        if key not in self._visible:
            candidates = ({entity_id} if entity_id else
                self._registry.get((kind, identity), {f"{kind}.{identity}"}) if identity else set())
            self._visible[key] = any(
                resolve_registry_access(eid, token, hass) in (Permission.READ, Permission.WRITE)
                for eid in candidates
            )
        return self._visible[key]
