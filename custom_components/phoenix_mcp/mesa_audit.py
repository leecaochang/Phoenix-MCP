"""Tail mesa-core's audit events into Phoenix MCP's own audit log.

mesa-core emits one event dict per decision on the ``mesa_core.audit`` logger
(payload on the record's ``mesa_audit_event`` attribute). Phoenix MCP records its
OWN outcome for a call, so without this the Audit tab never shows which entity
MESA stopped or under which rule.

Two deliberate limits keep the tab worth reading. 1. DENIALS ONLY, and only those
PHOENIX honored: allowed traffic would flood the ring buffer, and
``control_mode:confirm_no_channel`` (``_NOT_A_DENIAL_RULES``) is labelled
"blocked" because the enforcer runs with interactive=False, but Phoenix always
reinterprets it host-side (advisory warns, enforced raises its own approval), so
it is never a Phoenix denial. 2. THE LOGGER'S LEVEL IS UNCHANGED (mesa-core
defaults it to INFO): an operator raising it can suppress rows, so an empty trail
does not prove no denial occurred. See docs/mesa.html.

Rows are attributed to the request that provoked them through a ContextVar set by
the actuation gate, MESA-tool adapter and camera privacy check; with no context
set (a preview) an event is dropped.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator

from .mesa_core.audit import audit_logger
from .mesa_core import CallerContext, EnforcementResult, MesaAuditEvent, emit_audit_event

if TYPE_CHECKING:
    from .data import PhoenixData
    from .token_store import TokenRecord

_LOGGER = logging.getLogger(__name__)

# Decisions worth an operator's attention. mesa-core's vocabulary: enforcement
# uses "blocked", privacy uses "denied", leases use "granted"/"denied" for a
# request and an expiry reason for an ending.
_RECORDED_DECISIONS = frozenset({"blocked", "denied"})

# NOT a denial, however mesa-core labels it. The enforcer runs with
# interactive=False, so a confirm entity comes back "blocked" with this rule
# before any challenge is issued (a MESA confirm entity routes through Phoenix's
# own approval gate, never mesa-core's confirmation manager), and Phoenix ALWAYS
# reinterprets it host-side: advisory lets it through with a warning, enforced
# turns it into a pending approval with an approval record of its own. Recording
# it would log a "denied" row for an action that actually ran, when the Audit tab
# is where an operator finds out what MESA stopped.
_NOT_A_DENIAL_RULES = frozenset({"control_mode:confirm_no_channel"})

# Prefix on the audit row's method, so one "mesa" filter in the panel finds
# every kind while each kind stays distinguishable.
_METHOD_PREFIX = "mesa:"


@dataclass(frozen=True)
class MesaAuditContext:
    """The request a mesa-core event should be attributed to."""

    token_id: str
    token_name: str
    request_id: str
    client_ip: str
    pass_through: bool
    preset: str | None


_context: ContextVar[MesaAuditContext | None] = ContextVar("phoenix_mesa_audit", default=None)


@contextlib.contextmanager
def request_context(
    token: TokenRecord, request_id: str, client_ip: str | None
) -> Iterator[None]:
    """Attribute any mesa-core event raised in this block to one request."""
    # Resolved once here rather than per event: a call can raise one event per
    # targeted entity, and the preset lookup is a scan of the token's presets.
    preset: str | None = None
    active = getattr(token, "active_preset_id", None)
    if active:
        preset = next((p.name for p in getattr(token, "presets", []) if p.id == active), None)
    reset = _context.set(MesaAuditContext(
        token_id=token.id,
        token_name=token.name,
        request_id=request_id,
        client_ip=client_ip or "",
        pass_through=bool(getattr(token, "pass_through", False)),
        preset=preset,
    ))
    try:
        yield
    finally:
        _context.reset(reset)


def _resource(event: dict[str, Any]) -> str:
    """What the decision was about: the entity, else the action."""
    return str(event.get("entity_id") or event.get("action") or "-")


def record_read_only_denial(
    entity_id: str, service: str, caller: CallerContext, result: EnforcementResult
) -> None:
    """Record Phoenix's stronger read-only boundary only for actual requests.

    Uses the public audit event API; mesa-core's own denials are not emitted again.
    """
    if _context.get() is None:
        return
    emit_audit_event(MesaAuditEvent(
        event_type="enforcement_decision",
        action=service,
        decision="blocked",
        entity_id=entity_id,
        caller_id=caller.caller_id,
        roles=caller.effective_roles(),
        profile_version=result.effective_profile.metadata.profile_version,
        rule_applied="control_mode:read_only",
        details={"enforcement_source": "phoenix_read_only_boundary"},
    ))


class MesaAuditBridge(logging.Handler):
    """Records mesa-core denials, including privacy and lease refusals, in Phoenix's audit log.

    A logging handler runs inside the caller's ``log()`` call, so it must never
    raise: an exception here would surface inside mesa-core's decision path and
    turn an audit-plumbing bug into a failed service call.
    """

    def __init__(self, data: PhoenixData) -> None:
        super().__init__(level=logging.NOTSET)
        self._data = data

    def emit(self, record: logging.LogRecord) -> None:
        try:
            event = getattr(record, "mesa_audit_event", None)
            if not isinstance(event, dict):
                return
            if str(event.get("decision")) not in _RECORDED_DECISIONS:
                return
            if str(event.get("rule_applied")) in _NOT_A_DENIAL_RULES:
                return
            ctx = _context.get()
            if ctx is None:
                # No request to attribute it to: a preview, or a lease expiring
                # on a worker thread long after its caller went away. An
                # unattributed row is noise in a per-token log.
                return
            self._data.audit.record(
                request_id=ctx.request_id,
                token_id=ctx.token_id,
                token_name=ctx.token_name,
                method=_METHOD_PREFIX + str(event.get("event_type") or "event"),
                resource=_resource(event),
                outcome="denied",
                client_ip=ctx.client_ip,
                settings=self._data.store.get_settings(),
                pass_through=ctx.pass_through,
                # The rule, the roles and the service live here; record() runs
                # it through redact_structure and caps the size.
                payload=event,
                preset=ctx.preset,
            )
        except Exception:  # noqa: BLE001 - see the class docstring
            self.handleError(record)


def attach_mesa_audit_bridge(data: PhoenixData) -> MesaAuditBridge:
    """Start recording mesa-core denials. Returns the handler, for teardown."""
    bridge = MesaAuditBridge(data)
    audit_logger.addHandler(bridge)
    return bridge


def detach_mesa_audit_bridge(bridge: MesaAuditBridge | None) -> None:
    """Stop recording. Safe to call twice, so a config-entry reload cannot stack
    handlers and multiply every future row."""
    if bridge is None:
        return
    audit_logger.removeHandler(bridge)
    bridge.close()
