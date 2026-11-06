"""Build permission-filtered template state and render it in a sandboxed environment."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.dt import parse_datetime

from .policy_engine import (
    Permission, assist_expose_check, is_sensitive_key, resolve, scrub_attributes,
    template_blocklist_vars,
)
from .template_diagnostics import TemplateOutputLimitError

if TYPE_CHECKING:
    from .access_key_store import AccessKeyRecord


def build_permitted_states(access_key: AccessKeyRecord, hass: HomeAssistant) -> dict:
    """Return {entity_id: ScrubbedState} for the entities an access key may read.

    The one source for template sandboxes, filtered by resolve() and assist_expose_check.
    """
    expose = assist_expose_check(access_key, hass)
    return {
        s.entity_id: ScrubbedState(s, scrub_attributes(s.attributes, access_key, hass))
        for s in hass.states.async_all()
        if resolve(s.entity_id, access_key, hass) in (Permission.READ, Permission.WRITE)
        and (expose is None or expose(s.entity_id))
    }


class _ContextProxy(dict):
    """Dict subclass that also supports attribute access.

    Used by ScrubbedState.context so templates can use both context.id and
    context | tojson without TypeError. Behaves as a plain dict for json.dumps().
    """

    def __getattr__(self, name: str) -> Any:
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class ScrubbedState:
    """Lightweight State wrapper that strips sensitive attributes for use in template sandboxes."""

    def __init__(self, raw: Any, attributes: dict[str, Any] | None = None) -> None:
        self.entity_id = raw.entity_id
        self.state = raw.state
        self.attributes = {
            k: v for k, v in (raw.attributes if attributes is None else attributes).items()
            if not is_sensitive_key(k)
        }
        self.last_updated = getattr(raw, "last_updated", None)
        self.last_changed = getattr(raw, "last_changed", None)
        self.last_reported = getattr(raw, "last_reported", None)
        # Strip user_id from context to prevent HA user ID enumeration via templates.
        ctx = getattr(raw, "context", None)
        self.context: _ContextProxy | None
        if ctx is not None:
            self.context = _ContextProxy({
                "id": getattr(ctx, "id", None),
                "parent_id": getattr(ctx, "parent_id", None),
                "user_id": None,
            })
        else:
            self.context = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".")[0]

    @property
    def object_id(self) -> str:
        return self.entity_id.split(".", 1)[1] if "." in self.entity_id else self.entity_id

    @property
    def name(self) -> str:
        friendly = self.attributes.get("friendly_name")
        if friendly:
            return str(friendly)
        return self.object_id.replace("_", " ").title()

    @classmethod
    def from_dict(cls, row: dict) -> ScrubbedState:
        """Restore the template facade without HA constructor timestamp defaults."""

        context = row.get("context")
        return cls(SimpleNamespace(
            entity_id=row["entity_id"], state=row["state"],
            attributes=row["attributes"],
            last_changed=parse_datetime(row["last_changed"]) if row.get("last_changed") else None,
            last_updated=parse_datetime(row["last_updated"]) if row.get("last_updated") else None,
            last_reported=parse_datetime(row["last_reported"]) if row.get("last_reported") else None,
            context=SimpleNamespace(**context) if context is not None else None,
        ))

    def as_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "state": self.state,
            "attributes": self.attributes,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "last_reported": self.last_reported.isoformat() if self.last_reported else None,
            "last_changed": self.last_changed.isoformat() if self.last_changed else None,
            "context": {
                "id": getattr(self.context, "id", None),
                "parent_id": getattr(self.context, "parent_id", None),
                "user_id": None,
            } if self.context is not None else None,
        }


class _DomainFilteredStates:
    """Iterable wrapper for a single domain's entities inside FilteredStates.

    Supports both iteration ({% for state in states.light %}) yielding
    ScrubbedState objects, and attribute access (states.light.living_room)
    returning individual entities by object_id.
    """

    def __init__(self, entities: dict) -> None:
        self._entities = entities

    def __iter__(self) -> Iterator[Any]:
        return iter(self._entities.values())

    def __len__(self) -> int:
        return len(self._entities)

    def __getattr__(self, object_id: str) -> Any:
        if object_id.startswith("_"):
            raise AttributeError(object_id)
        return self._entities.get(object_id)


class FilteredStates:
    """Callable proxy over a permitted-entity dict mimicking the HA template 'states' global.

    HA templates use 'states' as both a callable (states('sensor.foo')) and a
    domain-keyed accessor (states.light). A plain dict breaks the callable form,
    so this proxy implements both protocols while restricting access to permitted entities.
    """

    def __init__(self, permitted: dict) -> None:
        self._permitted = permitted

    def __call__(self, entity_id: str, default: str = "unknown") -> str:
        s = self._permitted.get(entity_id)
        return s.state if s is not None else default

    def __getitem__(self, entity_id: str) -> Any:
        return self._permitted.get(entity_id)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._permitted.values())

    def __len__(self) -> int:
        return len(self._permitted)

    def __getattr__(self, domain: str) -> Any:
        if domain.startswith("_"):
            raise AttributeError(domain)
        entities = {
            eid.split(".", 1)[1]: s
            for eid, s in self._permitted.items()
            if eid.split(".")[0] == domain
        }
        return _DomainFilteredStates(entities)


_SAFE_TEMPLATE_ENV = None


def safe_template_env() -> Any:
    """Return the cached hass-less TemplateEnvironment for access key template renders.

    Without hass, no hass-aware helpers register; variables cannot shadow filters or tests.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.template import TemplateEnvironment
    global _SAFE_TEMPLATE_ENV
    if _SAFE_TEMPLATE_ENV is None:
        _SAFE_TEMPLATE_ENV = TemplateEnvironment(None)
    return _SAFE_TEMPLATE_ENV


def render_template_snapshot(template_str: str, permitted: dict) -> str:
    """Render a Jinja2 template against a detached snapshot of permitted state.

    Uses safe_template_env() with permission-restricted state helpers and pure time shims.
    Raises TemplateError, ValueError or jinja2 errors; callers map them to invalid_request.
    """
    # Local: not part of the recorded Home Assistant import floor.
    from homeassistant.helpers.template import MAX_TEMPLATE_OUTPUT

    filtered_states = FilteredStates(permitted)

    # Permission-restricted versions of the HA template state helpers.
    def _state_attr(entity_id: str, attr: str) -> Any:
        s = permitted.get(entity_id)
        return s.attributes.get(attr) if s is not None else None

    def _is_state(entity_id: str, value: str) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.state == value

    def _is_state_attr(entity_id: str, attr: str, value) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.attributes.get(attr) == value

    def _has_value(entity_id: str) -> bool:
        s = permitted.get(entity_id)
        return s is not None and s.state not in ("unknown", "unavailable")

    # Time helpers absent from the hass-less environment. These mirror HA's
    # DateTimeExtension implementations; they touch only dt_util, never entities.
    def _today_at(time_str: str = "") -> datetime:
        today = dt_util.start_of_local_day()
        if not time_str:
            return today
        parsed = dt_util.parse_time(time_str)
        if parsed is None:
            raise ValueError(f"could not convert str to datetime: '{time_str}'")
        return datetime.combine(today, parsed, today.tzinfo)

    def _localize(value: datetime) -> datetime:
        return value if value.tzinfo else dt_util.as_local(value)

    def _relative_time(value: Any) -> Any:
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() < value else dt_util.get_age(value)

    def _time_since(value: Any, precision: int = 1) -> Any:
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() < value else dt_util.get_age(value, precision)

    def _time_until(value: Any, precision: int = 1) -> Any:
        if not isinstance(value, datetime):
            return value
        value = _localize(value)
        return value if dt_util.now() > value else dt_util.get_time_remaining(value, precision)

    variables = {
        "states": filtered_states,
        "state_attr": _state_attr,
        "is_state": _is_state,
        "is_state_attr": _is_state_attr,
        "has_value": _has_value,
        "now": dt_util.now,
        "utcnow": dt_util.utcnow,
        "today_at": _today_at,
        "relative_time": _relative_time,
        "time_since": _time_since,
        "time_until": _time_until,
        # Defense in depth: these names do not exist in the hass-less environment,
        # but the enumeration helpers must return empty values rather than raise,
        # so keep the stubs as render variables.
        **template_blocklist_vars(),
    }

    compiled = safe_template_env().from_string(template_str)
    chunks = []
    size = 0
    for chunk in compiled.generate(variables):
        size += len(chunk)
        if size > MAX_TEMPLATE_OUTPUT:
            raise TemplateOutputLimitError("Template output exceeded maximum size.")
        chunks.append(chunk)
    return "".join(chunks).strip()
