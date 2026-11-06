"""MESA profile suggestions: scan for under-protected entities, admin-confirmed.

blast_radius flags automations, scripts and scenes without authored coverage that touch a
risky domain or fan out widely; the profile goes on the orchestrator, as its actions are never
re-gated. naked_risky flags uncovered risky-domain entities: lock and alarm_control_panel
suggest "prohibited", the rest "confirm".

Computed admin-side and cached on MesaRuntime, never auto-applied. Blast radii use HA's public
callbacks and are a lower bound, since templated targets are invisible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er_mod

from .const import (
    BLOCKED_DOMAINS, MESA_SUGGEST_CONSOLIDATE_THRESHOLD, MESA_SUGGEST_COVER_DEVICE_CLASSES,
    MESA_SUGGEST_FANOUT_THRESHOLD, MESA_SUGGEST_MAX_EVIDENCE_IDS, MESA_SUGGEST_RISKY_DOMAINS,
    MESA_SUGGEST_SIGNAL_BLAST_RADIUS, MESA_SUGGEST_SIGNAL_NAKED_RISKY,
)
from .catalog_templates import MESA_SUGGESTION_PHRASES, MESA_SUGGESTION_TEMPLATES
from .mesa_core import ControlMode, DeploymentDefaults

if TYPE_CHECKING:
    from .mesa import MesaRuntime

_ORCHESTRATOR_DOMAINS = ("automation", "script", "scene")


@dataclass
class MesaSuggestion:
    """One suggested profile. key is the stable dismissal identity."""

    key: str
    signal: str
    scope: str  # "entity" | "domain"
    subject_id: str
    suggested_mode: str
    # English, and the form written into control_reason on Apply: it is the
    # record. reason_key/reason_params let the panel render the same sentence in
    # the operator's language without the stored text ever changing.
    reason: str
    reason_key: str = ""
    reason_params: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)


def _reason(key: str, **params: Any) -> tuple[str, str, dict[str, Any]]:
    """Return the English sentence plus the key and params that reproduce it elsewhere.

    Params ending in _key name sub-phrases the panel resolves; they are never interpolated.
    """
    text = MESA_SUGGESTION_TEMPLATES[key].format(
        **{k: v for k, v in params.items() if not k.endswith("_key")}
    )
    return text, key, params


def _reason_fields(key: str, **params: Any) -> dict[str, Any]:
    """The same thing as _reason, shaped for MesaSuggestion(**...)."""
    reason, reason_key, reason_params = _reason(key, **params)
    return {"reason": reason, "reason_key": reason_key, "reason_params": reason_params}


def _automation_refs(hass: HomeAssistant, entity_id: str) -> list[str] | None:
    """Return the entities referenced by an automation, or None when unavailable.

    Any failure, such as a moved API, skips the signal and never breaks the scan.
    """
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.automation import entities_in_automation
        return entities_in_automation(hass, entity_id)
    except Exception:  # degrade to signal-skipped
        return None


def _script_refs(hass: HomeAssistant, entity_id: str) -> list[str] | None:
    """Entities referenced by a script, or None when unavailable."""
    try:
        # Local: optional component, imported only when this path runs.
        from homeassistant.components.script import entities_in_script
        return entities_in_script(hass, entity_id)
    except Exception:  # degrade to signal-skipped
        return None


def _scene_members(hass: HomeAssistant, entity_id: str) -> list[str] | None:
    """A scene's member entities from its state attribute, or None."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    members = state.attributes.get("entity_id")
    if isinstance(members, (list, tuple)):
        return [m for m in members if isinstance(m, str)]
    return None


def _refs_for(domain: str, hass: HomeAssistant, entity_id: str) -> list[str] | None:
    """Dispatch to the per-domain ref helper at call time (monkeypatch-friendly)."""
    if domain == "automation":
        return _automation_refs(hass, entity_id)
    if domain == "script":
        return _script_refs(hass, entity_id)
    return _scene_members(hass, entity_id)


def _cover_device_class(hass: HomeAssistant, entity_id: str) -> str | None:
    state = hass.states.get(entity_id)
    if state is not None:
        dc = state.attributes.get("device_class")
        if dc:
            return str(dc)
    entry = er_mod.async_get(hass).async_get(entity_id)
    if entry is not None:
        return entry.device_class or entry.original_device_class
    return None


def _is_risky_ref(hass: HomeAssistant, entity_id: str) -> bool:
    """Whether a referenced entity counts as risky for the blast-radius signal."""
    domain = entity_id.split(".", 1)[0]
    if domain in BLOCKED_DOMAINS or domain not in MESA_SUGGEST_RISKY_DOMAINS:
        return False
    if domain == "cover":
        return _cover_device_class(hass, entity_id) in MESA_SUGGEST_COVER_DEVICE_CLASSES
    return True


def _default_covered(defaults: DeploymentDefaults | None, domain: str) -> bool:
    """Whether deployment defaults already restrict unprofiled entities of a domain.

    When they do (the domain resolves to a non-autonomous mode before the built-in
    baseline is consulted), suggesting a profile is redundant noise.
    """
    if defaults is None:
        return False
    try:
        return defaults.control_mode_for(domain) is not ControlMode.AUTONOMOUS
    except Exception:  # malformed override value: fail open to suggesting
        return False


def _suggest_blast_radius(
    hass: HomeAssistant, runtime: MesaRuntime, defaults: DeploymentDefaults | None
) -> list[MesaSuggestion]:
    suggestions: list[MesaSuggestion] = []
    for domain in _ORCHESTRATOR_DOMAINS:
        if _default_covered(defaults, domain):
            continue
        for subject in sorted(hass.states.async_entity_ids(domain)):
            if runtime.resolver.has_profile(subject):
                continue
            refs = _refs_for(domain, hass, subject)
            if not refs:  # None = source unavailable, [] = nothing referenced
                continue
            unique_refs = sorted(set(refs))
            risky = [r for r in unique_refs if _is_risky_ref(hass, r)]
            over_fanout = len(unique_refs) >= MESA_SUGGEST_FANOUT_THRESHOLD
            if not risky and not over_fanout:
                continue
            noun = MESA_SUGGESTION_PHRASES[f"noun.{domain}"]
            if risky:
                shown = ", ".join(risky[:3])
                if len(risky) > 3:
                    shown = MESA_SUGGESTION_PHRASES["shown.more"].format(
                        shown=shown, extra=len(risky) - 3
                    )
                reason, reason_key, reason_params = _reason(
                    "blast_radius.risky.one" if len(risky) == 1 else "blast_radius.risky.other",
                    noun=noun, count=len(risky), shown=shown,
                )
                reason_params["noun_key"] = f"noun.{domain}"
            else:
                reason, reason_key, reason_params = _reason(
                    "blast_radius.fanout", noun=noun, count=len(unique_refs),
                )
                reason_params["noun_key"] = f"noun.{domain}"
            suggestions.append(MesaSuggestion(
                key=f"{MESA_SUGGEST_SIGNAL_BLAST_RADIUS}:entity:{subject}",
                signal=MESA_SUGGEST_SIGNAL_BLAST_RADIUS,
                scope="entity",
                subject_id=subject,
                suggested_mode="confirm",
                reason=reason,
                reason_key=reason_key,
                reason_params=reason_params,
                evidence={
                    "referenced_count": len(unique_refs),
                    "risky_referenced": risky[:MESA_SUGGEST_MAX_EVIDENCE_IDS],
                    "over_fanout": over_fanout,
                },
            ))
    return suggestions


def _suggest_naked_risky(
    hass: HomeAssistant, runtime: MesaRuntime, defaults: DeploymentDefaults | None
) -> list[MesaSuggestion]:
    suggestions: list[MesaSuggestion] = []
    for domain, (mode, concern) in MESA_SUGGEST_RISKY_DOMAINS.items():
        if _default_covered(defaults, domain):
            continue
        entity_ids = sorted(hass.states.async_entity_ids(domain))
        if domain == "cover":
            entity_ids = [
                e for e in entity_ids
                if _cover_device_class(hass, e) in MESA_SUGGEST_COVER_DEVICE_CLASSES
            ]
        uncovered = [e for e in entity_ids if not runtime.resolver.has_profile(e)]
        if not uncovered:
            continue
        baseline_note = (
            MESA_SUGGESTION_PHRASES["baseline_note"] if mode == "prohibited" else ""
        )
        # Many naked entities in one domain: one domain-level suggestion instead
        # of a wall of rows. cover is exempt (a domain profile would catch blinds).
        if len(uncovered) >= MESA_SUGGEST_CONSOLIDATE_THRESHOLD and domain != "cover":
            suggestions.append(MesaSuggestion(
                key=f"{MESA_SUGGEST_SIGNAL_NAKED_RISKY}:domain:{domain}",
                signal=MESA_SUGGEST_SIGNAL_NAKED_RISKY,
                scope="domain",
                subject_id=domain,
                suggested_mode=mode,
                **_reason_fields(
                    "naked_risky.domain", count=len(uncovered), domain=domain,
                    concern=concern, baseline_note=baseline_note,
                    concern_key=f"concern.{domain}",
                    baseline_key="baseline_note" if baseline_note else "",
                ),
                evidence={
                    "domain": domain,
                    "uncovered_count": len(uncovered),
                    "examples": uncovered[:MESA_SUGGEST_MAX_EVIDENCE_IDS],
                    "concern": concern,
                },
            ))
            continue
        for entity_id in uncovered:
            suggestions.append(MesaSuggestion(
                key=f"{MESA_SUGGEST_SIGNAL_NAKED_RISKY}:entity:{entity_id}",
                signal=MESA_SUGGEST_SIGNAL_NAKED_RISKY,
                scope="entity",
                subject_id=entity_id,
                suggested_mode=mode,
                **_reason_fields(
                    "naked_risky.entity", concern=concern, baseline_note=baseline_note,
                    concern_key=f"concern.{domain}",
                    baseline_key="baseline_note" if baseline_note else "",
                ),
                evidence={"domain": domain, "concern": concern},
            ))
    return suggestions


def _subject_exists(hass: HomeAssistant, scope: str, subject_id: str) -> bool:
    if scope == "domain":
        return bool(hass.states.async_entity_ids(subject_id))
    return (
        hass.states.get(subject_id) is not None
        or er_mod.async_get(hass).async_get(subject_id) is not None
    )


def refresh_suggestions(hass: HomeAssistant, runtime: MesaRuntime) -> None:
    """Recompute profile suggestions and cache them on the runtime.

    Synchronous and admin-initiated, never on an agent path; computed whatever the mesa_mode.
    Dismissed keys are filtered, and those whose subject is gone are pruned in memory.
    """
    defaults = runtime.store.get_deployment_defaults()
    computed = _suggest_blast_radius(hass, runtime, defaults) + _suggest_naked_risky(
        hass, runtime, defaults
    )
    computed.sort(key=lambda s: (s.signal, s.scope != "domain", s.subject_id))
    runtime.suggestions = [s for s in computed if s.key not in runtime.dismissed_suggestions]

    pruned = set()
    for key in runtime.dismissed_suggestions:
        parts = key.split(":", 2)
        if len(parts) != 3:
            continue
        _signal, scope, subject_id = parts
        if _subject_exists(hass, scope, subject_id):
            pruned.add(key)
    runtime.dismissed_suggestions = pruned
