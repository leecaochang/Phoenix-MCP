"""Inheritance resolution: defaults, then domain, integration, area, device, entity (Spec 5.6, 5.8).

The InheritanceResolver gathers the declared layers for an entity, merges them
through the ConflictResolver (Rules A-E), and fills undeclared kernel fields
from deployment defaults or the built-in domain safety baseline (Rule E:
defaults apply only when no profile at any level declares the field).
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from custom_components.phoenix_mcp.mesa_core.conflict import ConflictResolver, FieldExplanation, Layer, _is_trusted_for
from custom_components.phoenix_mcp.mesa_core.exceptions import HostCallbackError
from custom_components.phoenix_mcp.mesa_core.profile import (
    CONTROL_MODE_RANK,
    ControlMode,
    PrivacyLevel,
    ProfileMetadata,
    SemanticProfile,
    baseline_control_mode,
    baseline_triggers_automations,
)

if TYPE_CHECKING:
    from custom_components.phoenix_mcp.mesa_core.store import DeploymentDefaults, ProfileStore


@dataclass
class ProfileExplanation:
    """Full inheritance resolution path for an entity (Spec 9.5)."""

    entity_id: str
    effective_profile: SemanticProfile
    explanation: list[FieldExplanation] = field(default_factory=list)
    conflicts_detected: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self, show_conflicts: bool = True) -> dict[str, Any]:
        entries = []
        for entry in self.explanation:
            d = entry.to_dict()
            if not show_conflicts:
                d.pop("competing_values", None)
            entries.append(d)
        return {
            "entity_id": self.entity_id,
            "effective_profile": self.effective_profile.to_dict(),
            "explanation": entries,
            "conflicts_detected": self.conflicts_detected,
            "warnings": list(self.warnings),
        }


class InheritanceResolver:
    """Resolves effective profiles for entities.

    Host callbacks supply the HA registry knowledge mesa-core does not have:
    ``get_entity_area`` maps an entity ID to its area ID (None when unassigned),
    ``get_entity_domain`` maps an entity ID to the HA domain whose domain-level
    profile applies (defaults to the entity ID prefix), and
    ``get_entity_integration`` maps an entity ID to the integration that created
    it, for the integration-level sidecar profile (Spec 5.6). When
    ``get_entity_integration`` is absent, the integration level falls back to the
    entity's HA domain, which resolves domain-defining integrations (``light``,
    ``lock``, ...) and leaves device and hub integration profiles inert.
    ``get_entity_device`` maps an entity ID to the HA device registry ID of the
    physical device that owns it (None when the entity belongs to no device);
    when absent there is no fallback and device-scope profiles are inert
    (Spec 5.6).
    """

    def __init__(
        self,
        store: ProfileStore,
        get_entity_area: Callable[[str], str | None] | None = None,
        get_entity_domain: Callable[[str], str] | None = None,
        get_entity_integration: Callable[[str], str | None] | None = None,
        get_entity_device: Callable[[str], str | None] | None = None,
    ) -> None:
        self.store = store
        if get_entity_area is not None:
            store.get_entity_area = get_entity_area
        if get_entity_domain is not None:
            store.get_entity_domain = get_entity_domain
        if get_entity_integration is not None:
            store.get_entity_integration = get_entity_integration
        if get_entity_device is not None:
            store.get_entity_device = get_entity_device
        self._conflicts = ConflictResolver()

    @property
    def get_entity_domain(self) -> Callable[[str], str]:
        return self.store.get_entity_domain or (lambda eid: eid.split(".", 1)[0])

    def lookup(self, kind: str, entity_id: str) -> str | None:
        callback = getattr(self, f"get_entity_{kind}")
        if callback is None:
            return None
        try:
            value = callback(entity_id)
        except Exception as err:
            raise HostCallbackError(f"get_entity_{kind} callback failed") from err
        if inspect.isawaitable(value):
            if inspect.iscoroutine(value):
                value.close()
            raise HostCallbackError(f"get_entity_{kind} must be synchronous")
        if value is None and kind != "domain":
            return None
        if not isinstance(value, str) or not value.strip():
            raise HostCallbackError(f"get_entity_{kind} must return a non-empty identifier")
        return value

    @property
    def get_entity_area(self) -> Callable[[str], str | None] | None:
        return self.store.get_entity_area

    @property
    def get_entity_integration(self) -> Callable[[str], str | None] | None:
        return self.store.get_entity_integration

    @property
    def get_entity_device(self) -> Callable[[str], str | None] | None:
        return self.store.get_entity_device

    def _gather_layers(
        self, entity_id: str, entity_profile: SemanticProfile | None = None
    ) -> list[Layer]:
        layers: list[Layer] = []
        # A caller that has already loaded the entity profile (e.g. query())
        # passes it in to avoid a redundant store read; None means fetch it.
        if entity_profile is None:
            entity_profile = self.store.get(entity_id)
        if entity_profile is not None:
            layers.append(Layer("entity", entity_profile))
        # Device level (Spec 5.6): host maps entity to owning device; no
        # fallback exists, so without the callback device profiles are inert.
        if self.get_entity_device is not None:
            device_id = self.lookup("device", entity_id)
            if device_id is not None:
                device_profile = self.store.get_device_profile(device_id)
                if device_profile is not None:
                    layers.append(Layer("device", device_profile))
        if self.get_entity_area is not None:
            area_id = self.lookup("area", entity_id)
            if area_id is not None:
                area_profile = self.store.get_area_profile(area_id)
                if area_profile is not None:
                    layers.append(Layer("area", area_profile))
        # Integration level: the sidecar of the integration that created this
        # entity (Spec 5.6). The host maps entity -> integration; absent that
        # mapping we fall back to the entity's HA domain, which resolves
        # domain-defining integrations only.
        if self.get_entity_integration is not None:
            integration = self.lookup("integration", entity_id)
        else:
            integration = self.lookup("domain", entity_id)
        if integration is not None:
            integration_profile = self.store.get_integration_profile(integration)
            if integration_profile is not None:
                layers.append(Layer("integration", integration_profile))
        domain = self.lookup("domain", entity_id)
        assert domain is not None
        domain_profile = self.store.get_domain_profile(domain)
        if domain_profile is not None:
            layers.append(Layer("domain", domain_profile))
        return layers

    def has_profile(self, entity_id: str) -> bool:
        """Whether any profile is declared for this entity at any inheritance level."""
        return bool(self._gather_layers(entity_id))

    @staticmethod
    def _default_control_mode(
        domain: str, layers: list[Layer], defaults: DeploymentDefaults | None
    ) -> tuple[ControlMode, str, str]:
        """Rule E default for an undeclared control_mode.

        Unprofiled entities take the deployment/domain baseline. Profiles with no
        trusted control declaration preserve the stricter of that floor and
        confirm. This keeps locks prohibited when unrelated metadata is present.
        """
        mode = defaults.control_mode_for(domain) if defaults else baseline_control_mode(domain)
        if layers and CONTROL_MODE_RANK[mode] < CONTROL_MODE_RANK[ControlMode.CONFIRM]:
            mode = ControlMode.CONFIRM
        return (
            mode,
            "deployment_default" if defaults else "built_in_baseline",
            "user" if defaults else "unknown",
        )

    def explain(
        self, entity_id: str, *, entity_profile: SemanticProfile | None = None
    ) -> ProfileExplanation:
        layers = self._gather_layers(entity_id, entity_profile)
        effective, resolution = self._conflicts.resolve(entity_id, layers)
        domain = self.lookup("domain", entity_id)
        assert domain is not None
        defaults = self.store.get_deployment_defaults()

        # Unconfirmed inferred declarations may tighten, never loosen, the
        # applicable baseline. Trusted declarations explicitly replace it.
        control_path = "operational_boundaries.control_mode"
        trusted_control = any(
            (layer.profile.declared(control_path) and _is_trusted_for(layer.profile, control_path))
            or (
                layer.level == "integration"
                and "control_mode"
                in layer.profile.raw.get("semantic_profile", {}).get("capability_semantics", {})
                and _is_trusted_for(layer.profile, "capability_semantics.control_mode")
            )
            for layer in layers
        )
        floor, floor_level, floor_origin = self._default_control_mode(domain, layers, defaults)
        if (
            not trusted_control
            and CONTROL_MODE_RANK[floor]
            > CONTROL_MODE_RANK[effective.operational_boundaries.control_mode]
        ):
            effective.operational_boundaries.control_mode = floor
            resolution.explanations = [
                entry for entry in resolution.explanations if entry.field_path != control_path
            ]
            resolution.explanations.append(
                FieldExplanation(
                    field_path=control_path,
                    effective_value=floor.value,
                    provided_by_level=floor_level,
                    provided_by_origin=floor_origin,
                )
            )

        declared_paths = {e.field_path for e in resolution.explanations}

        # Rule E default filling for the kernel policy fields.
        if "operational_boundaries.control_mode" not in declared_paths:
            mode, level, origin = self._default_control_mode(domain, layers, defaults)
            effective.operational_boundaries.control_mode = mode
            resolution.explanations.append(
                FieldExplanation(
                    field_path="operational_boundaries.control_mode",
                    effective_value=mode.value,
                    provided_by_level=level,
                    provided_by_origin=origin,
                )
            )

        if "operational_boundaries.triggers_automations" not in declared_paths:
            if not layers and defaults is not None:
                # deployment_defaults are scoped to unprofiled entities (Spec 5.8).
                triggers = defaults.triggers_for(domain)
                level = "deployment_default"
                origin = "user"
            else:
                # A profiled entity with no triggers declaration takes the
                # baseline: helpers default to likely regardless (Spec 5.4 Rule 9),
                # so a deployment override cannot drop an inferred helper to none.
                triggers = baseline_triggers_automations(domain)
                level = "built_in_baseline"
                origin = "unknown"
            effective.operational_boundaries.triggers_automations = triggers
            resolution.explanations.append(
                FieldExplanation(
                    field_path="operational_boundaries.triggers_automations",
                    effective_value=triggers.value,
                    provided_by_level=level,
                    provided_by_origin=origin,
                )
            )

        if "privacy_classification.level" not in declared_paths:
            # Person entities MUST be treated as sensitive by default (Spec 17).
            privacy = PrivacyLevel.SENSITIVE if domain == "person" else PrivacyLevel.NORMAL
            effective.privacy_classification.level = privacy
            resolution.explanations.append(
                FieldExplanation(
                    field_path="privacy_classification.level",
                    effective_value=privacy.value,
                    provided_by_level="built_in_baseline",
                    provided_by_origin="unknown",
                )
            )

        if not layers:
            effective.metadata = ProfileMetadata()

        # The effective profile declares every field resolution settled,
        # including the Rule E defaults filled above.
        effective.declared_paths = {e.field_path for e in resolution.explanations}

        return ProfileExplanation(
            entity_id=entity_id,
            effective_profile=effective,
            explanation=resolution.explanations,
            conflicts_detected=resolution.conflicts_detected,
            warnings=resolution.warnings,
        )

    def resolve(
        self, entity_id: str, *, entity_profile: SemanticProfile | None = None
    ) -> SemanticProfile:
        """Return the fully resolved effective profile for an entity.

        ``entity_profile`` is an optional already-loaded entity profile, passed
        to avoid a redundant store read; None means load it from the store.
        """
        return self.explain(entity_id, entity_profile=entity_profile).effective_profile
