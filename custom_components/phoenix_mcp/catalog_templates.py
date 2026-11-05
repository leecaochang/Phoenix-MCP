"""English source text of the generated operator catalog sections.

scripts/gen_diff_catalog.py writes these templates into the English catalog, and
the locale catalogs carry their translations: edit a template here, then regenerate.
"""

from collections.abc import Mapping
from types import MappingProxyType

from .const import MESA_SUGGEST_RISKY_DOMAINS

# The suggestion reason an operator reads, and which is written verbatim into a
# profile's control_reason when they Apply. English stays the stored form (like
# a diff summary, it is the record); the panel renders the key in its own
# language. Keys are mirrored into translations/en.json by
# scripts/gen_diff_catalog.py, so never hand-edit that section.
MESA_SUGGESTION_TEMPLATES: Mapping[str, str] = MappingProxyType({
    "blast_radius.risky.one": (
        "This {noun} touches {count} risky entity ({shown}) and has no MESA profile of "
        "its own; an agent allowed to trigger it controls those entities indirectly, "
        "bypassing their own profiles."
    ),
    "blast_radius.risky.other": (
        "This {noun} touches {count} risky entities ({shown}) and has no MESA profile of "
        "its own; an agent allowed to trigger it controls those entities indirectly, "
        "bypassing their own profiles."
    ),
    "blast_radius.fanout": (
        "This {noun} has no MESA profile of its own and references many entities ({count}); "
        "one trigger has a wide blast radius."
    ),
    "naked_risky.domain": (
        "{domain} entities with no MESA profile at any level: {count}. This domain "
        "{concern}. A domain-level profile covers them all.{baseline_note}"
    ),
    "naked_risky.entity": (
        "No MESA profile covers this entity at any level, and it {concern}.{baseline_note}"
    ),
})

# The domain nouns and the trailing baseline note the templates interpolate. Each
# domain's concern clause is interpolated INTO a reason template, so it needs its
# own catalog entry: translating the sentence but not the clause spliced into it
# produces a half-translated line. Derived from MESA_SUGGEST_RISKY_DOMAINS so the
# two cannot drift.
MESA_SUGGESTION_PHRASES: Mapping[str, str] = MappingProxyType({
    "noun.automation": "automation",
    "noun.script": "script",
    "noun.scene": "scene",
    "shown.more": "{shown} + {extra} more",
    "baseline_note": (
        " Prohibited matches the built-in baseline for this domain, so the profile makes "
        "that intent explicit. To allow agent control behind admin approval instead, "
        "choose Confirm in Profile Editor before saving; approval applies while MESA is enforced."
    ),
    **{f"concern.{domain}": concern for domain, (_mode, concern) in MESA_SUGGEST_RISKY_DOMAINS.items()},
})

# Approval diff summaries, as format templates keyed by a stable slug.
#
# The summary an admin reads before approving is the confirm gate's whole safety
# property, so it has to be translatable. It is also PERSISTED on the approval
# record (it doubles as the audit trail), which means translating it server-side
# would freeze the language at creation time and make History mixed after a
# language switch. So the record carries the English `summary` plus
# `summary_key` and `summary_params` for the panel to render in the operator's
# own language, falling back to the stored English for any record without a key.
#
# The template lives HERE and nowhere else. Python formats it to produce the
# stored English, and scripts/gen_diff_catalog.py generates the panel's
# translations/en.json `diff.*` entries FROM this dict, so the two cannot drift.
# A contract test pins that generation and checks every builder's emitted key
# and params against it.
#
# Placeholders are `{name}` only: that is what both str.format and the panel's
# interpolation understand, and what HA's own translation loader validates a
# translated string against. A variant suffix (.mesa, .section, .device) is a
# SEPARATE key rather than a runtime concatenation, because a translator has to
# be able to move the clause.
DIFF_SUMMARY_TEMPLATES: Mapping[str, str] = MappingProxyType({
    "create_calendar_event": "Create calendar event",
    "update_calendar_event": "Update calendar event",
    "delete_calendar_event": "Delete calendar event",
    "configuration_recovery.restore": "Restore saved configuration for {resource}",
    "configuration_recovery.acknowledge": "Check configuration after an interrupted change to {resource}",
    "restore.template_helper": "Restore Template helper configuration",
    "restore.blueprint": "Restore blueprint source '{label}'",
    "restore.dashboard_layout": "Restore the layout of dashboard '{label}'",
    "restore.dashboard_registry": "Restore the registry entry of dashboard '{label}'",
    "restore.dashboard_resource": "Restore dashboard resource {url}",
    "restore.energy": "Restore Energy dashboard preferences",
    "edit_energy_config.fallback": "Change the Energy dashboard configuration",
    # Blueprints. Create never reports consumers (a new blueprint has none).
    "blueprint.create": "Create {domain} blueprint '{rel}'",
    "blueprint.edit": "Replace {domain} blueprint '{rel}'",
    "blueprint.delete": "Delete {domain} blueprint '{rel}'",
    "blueprint.edit.consumers": "Replace {domain} blueprint '{rel}' (to be reloaded: {count})",
    "blueprint.delete.consumers": "Delete {domain} blueprint '{rel}' (still in use: {count})",
    # Entity registry.
    "set_entity": "Update registry metadata ({fields}) for {entity_id}",
    "delete_entity": "Delete the registry entry for {entity_id}",
    # Location registry.
    "location.create": "Create location {name}",
    "location.update": "Update location {name}",
    "location.delete": "Delete location {name}",
    "label.create": "Create label {name}",
    "label.update": "Update label {name}",
    "label.delete": "Delete label {name}",
    "category.create": "Create category {name}",
    "category.update": "Update category {name}",
    "category.delete": "Delete category {name}",
    # Device registry.
    "set_device": "Update device registry metadata ({fields}) for {device_id}",
    "remove_device": "Remove {device_id} from config entry {config_entry_id}",
    # Zigbee. The bare forms are the error paths, where no device resolved.
    "zigbee_permit": "Open the Zigbee network for joining",
    "zigbee_permit.close": "Close the Zigbee join window",
    "zigbee_permit.open": "Open the Zigbee network for joining for {duration} seconds",
    "zigbee_reconfigure": "Reconfigure a Zigbee device",
    "zigbee_reconfigure.device": "Reconfigure (re-interview) Zigbee device {label}",
    "zigbee_remove": "Remove a Zigbee device",
    "zigbee_remove.device": "Remove Zigbee device {label} from the network",
    "zigbee_options": "Change Zigbee2MQTT device options",
    "zigbee_options.device": "Change Zigbee2MQTT options ({keys}) for {label}",
    "zigbee_property": "Change a Zigbee2MQTT exposed property",
    "zigbee_property.device": "Change Zigbee2MQTT property {property} for {label}",
    "zigbee_binding": "Change a Zigbee device binding",
    "zigbee_binding.devices.bind": "Bind Zigbee device {source} to {target}",
    "zigbee_binding.devices.unbind": "Unbind Zigbee device {source} from {target}",
    "zigbee_reporting": "Configure Zigbee attribute reporting",
    "zigbee_reporting.device": "Configure {cluster}.{attribute} reporting for {label}",
    "zigbee_group": "Change a Zigbee group",
    "zigbee_group.named.create": "Create Zigbee group {name}",
    "zigbee_group.named.add_members": "Add devices to Zigbee group {name}",
    "zigbee_group.named.remove_members": "Remove devices from Zigbee group {name}",
    "zigbee_group.named.delete": "Remove Zigbee group {name}",
    "zigbee_topology": "Scan Zigbee topology",
    "zigbee_topology.backend": "Scan {backend} neighbor tables for the scoped Zigbee map",
    # System.
    "restart_ha": "Restart Home Assistant",
    "send_notification": "Send a notification to {target}",
    "config_entry.options": "Change {keys} on helper '{label}'",
    "integration.enable": "Enable integration {label}",
    "integration.disable": "Disable integration {label}",
    "integration.update": "Update integration {label}",
    "integration.reconfigure": "Reconfigure integration {label}",
    "integration.log_level": "Change integration log level for {label}",
    "integration.reload": "Reload integration {label}",
    "integration.remove": "Remove integration {label}",
    "create_backup": "Create backup",
    "create_backup.named": 'Create backup "{name}"',
    # ESPHome.
    "install_esphome_firmware": "Flash ESPHome firmware to {label}",
    "install_esphome_firmware.jump": "Flash ESPHome firmware to {label} (ESPHome version change: {jump})",
    # Dashboards. The card ops spell out the location rather than passing a
    # pre-joined "view N, section M" fragment, so word order stays translatable.
    "set_dashboard_config": "Set dashboard layout '{label}'",
    "dashboard.create": "Create dashboard '{label}'",
    "dashboard.edit": "Edit dashboard '{label}'",
    "dashboard.delete": "Delete dashboard '{label}'",
    "dashboard_resource.create": "Add dashboard resource {url}",
    "dashboard_resource.update": "Change dashboard resource {url}",
    "dashboard_resource.delete": "Remove dashboard resource {url}",
    "dashboard_card.add": "Add '{card_type}' to dashboard '{label}' (view {view_index})",
    "dashboard_card.add.section": "Add '{card_type}' to dashboard '{label}' (view {view_index}, section {section_index})",
    "dashboard_card.edit": "Replace card {card_index} on dashboard '{label}' (view {view_index})",
    "dashboard_card.edit.section": "Replace card {card_index} on dashboard '{label}' (view {view_index}, section {section_index})",
    "dashboard_card.delete": "Delete card {card_index} from dashboard '{label}' (view {view_index})",
    "dashboard_card.delete.section": "Delete card {card_index} from dashboard '{label}' (view {view_index}, section {section_index})",
    # Energy dashboard. Each names the human-visible thing, never the JSON path:
    # an operator reviewing these knows their appliances, not HA's prefs schema.
    "edit_energy_config.replace_statistic": "Energy: point '{label}' at {new_statistic} (was {old_statistic})",
    "edit_energy_config.add_device": "Energy: track '{label}' ({new_statistic}) as an individual device",
    "edit_energy_config.remove_device": "Energy: stop tracking '{label}' ({old_statistic})",
    "edit_energy_config.rename_device": "Energy: rename '{old_label}' to '{label}'",
    "edit_energy_config.remove_source": "Energy: remove the {source_type} source ({old_statistic})",
    "edit_energy_config.set_source.update": "Energy: update the {source_type} source ({fields})",
    "edit_energy_config.set_source.create": "Energy: add a {source_type} source ({fields})",
    "patch_dashboard.set": "Set {path} on dashboard '{label}'",
    "patch_dashboard.append": "Append to {path} on dashboard '{label}'",
    "patch_dashboard.remove": "Remove {path} from dashboard '{label}'",
    "patch_dashboard.multi": "Apply {count} changes to dashboard '{label}'",
    # Service calls and the native physical-gate tools.
    "call_service": "Call {domain}/{service}",
    "call_service.mesa": "Call {domain}/{service} (includes MESA confirmation)",
    "hass_turn.on": "Physical device control: turn on {names}",
    "hass_turn.off": "Physical device control: turn off {names}",
    "hass_turn.on.mesa": "Physical device control: turn on {names} (includes MESA confirmation)",
    "hass_turn.off.mesa": "Physical device control: turn off {names} (includes MESA confirmation)",
    "hass_set_position": "Set cover/valve position",
    "hass_set_position.mesa": "Set cover/valve position (includes MESA confirmation)",
    "hass_stop_moving": "Stop moving cover",
    "hass_stop_moving.mesa": "Stop moving cover (includes MESA confirmation)",
    "mesa_service": "MESA confirmation for {domain}.{service}",
    # Authoring.
    "create_automation": "Create automation '{alias}'",
    "edit_automation": "Edit automation '{automation_id}'",
    "delete_automation": "Delete automation '{automation_id}'",
    "create_script": "Create script '{script_id}'",
    "edit_script": "Edit script '{script_id}'",
    "delete_script": "Delete script '{script_id}'",
    "create_scene": "Create scene '{name}'",
    "edit_scene": "Edit scene '{scene_id}'",
    "delete_scene": "Delete scene '{scene_id}'",
    "create_helper": "Create {helper_type} helper '{name}'",
    "edit_helper": "Edit {helper_type} helper '{helper_id}'",
    "delete_helper": "Delete {helper_type} helper '{helper_id}'",
})

# Friendly approval-modal copy. Unlike diff summaries, these sentences are
# presentation only: the persisted English diff remains the audit record. Every
# known diff key gets a title and consequence so Summary never has to expose a
# technical path or rendered YAML. The generated catalog is the frontend API.
_APPROVAL_SUMMARY_TITLE_OVERRIDES = MappingProxyType({
    "dashboard_card.add": "Add a card to dashboard '{label}'",
    "dashboard_card.add.section": "Add a card to dashboard '{label}'",
    "dashboard_card.edit": "Update a card on dashboard '{label}'",
    "dashboard_card.edit.section": "Update a card on dashboard '{label}'",
    "dashboard_card.delete": "Remove a card from dashboard '{label}'",
    "dashboard_card.delete.section": "Remove a card from dashboard '{label}'",
    "patch_dashboard.set": "Update dashboard '{label}'",
    "patch_dashboard.append": "Update dashboard '{label}'",
    "patch_dashboard.remove": "Update dashboard '{label}'",
    "patch_dashboard.multi": "Update dashboard '{label}'",
})

_APPROVAL_SUMMARY_BODIES = MappingProxyType({
    "configuration_recovery.restore": "A previous change could not be verified. Approve restores the saved configuration shown below. Reject keeps the current configuration. The original command will not be repeated.",
    "configuration_recovery.acknowledge": "A previous change could not be verified and cannot be safely restored here. Check this resource in Home Assistant, then Approve to accept its current state and unblock changes. Reject leaves changes blocked. The original command will not be repeated.",
    "restart_ha": "Home Assistant will be briefly unavailable while it restarts.",
    "restore.template_helper": "This will put back the Template helper configuration from the selected change. Compare it with the current configuration below.",
    "restore.blueprint": "This will put back the blueprint source from the selected change. Compare it with the current source below.",
    "restore.dashboard_layout": "This will put back the dashboard layout from the selected change. Compare it with the current layout below.",
    "restore.dashboard_registry": "This will put back the dashboard's registry settings from the selected change. Compare them with the current settings below.",
    "restore.dashboard_resource": "This will put back the dashboard resource from the selected change. Every browser loads it when it opens a dashboard.",
    "restore.energy": "This will put back the Energy dashboard preferences from the selected change. Compare them with the current preferences below.",
    "send_notification": "This will send the message shown below to {target}.",
    "zigbee_reconfigure": "This will interview the Zigbee device again. It may be briefly unresponsive. Battery devices must be awake.",
    "zigbee_reconfigure.device": "This will interview {label} again. It may be briefly unresponsive. Battery devices must be awake.",
    "zigbee_remove": "This will remove the Zigbee device from the network. The change cannot be undone from this approval.",
    "zigbee_remove.device": "This will remove {label} from the Zigbee network. The change cannot be undone from this approval.",
    "zigbee_options": "This will change Zigbee2MQTT device options. Review the proposed values below.",
    "zigbee_options.device": "This will change Zigbee2MQTT device options for {label}. Review the proposed values below.",
    "zigbee_property": "This will directly change a Zigbee2MQTT device property. Review the proposed value below.",
    "zigbee_property.device": "This will directly change property {property} for {label}. Review the proposed value below.",
    "zigbee_binding": "This will change direct control between two Zigbee devices. Review both devices and the selected clusters below.",
    "zigbee_binding.devices.bind": "This will let {source} control {target} directly over Zigbee. Review the exact relationship below.",
    "zigbee_binding.devices.unbind": "This will stop {source} controlling {target} directly over Zigbee. Review the exact relationship below.",
    "zigbee_reporting": "This will change how frequently a Zigbee device reports an attribute. Review the intervals below.",
    "zigbee_reporting.device": "This will change {cluster}.{attribute} reporting for {label}. Review the intervals below.",
    "zigbee_group": "This will change which devices are controlled together as a Zigbee group. Review the membership below.",
    "zigbee_group.named.create": "This will create Zigbee group {name}. Review its members below.",
    "zigbee_group.named.add_members": "This will add devices to Zigbee group {name}. Review the exact membership change below.",
    "zigbee_group.named.remove_members": "This will remove devices from Zigbee group {name}. Review the exact membership change below.",
    "zigbee_group.named.delete": "This will remove Zigbee group {name}. Its devices stay paired but are no longer controlled together.",
    "zigbee_topology": "This actively queries Zigbee routers. The mesh may be less responsive while the scan runs.",
    "zigbee_topology.backend": "This actively queries Zigbee routers on {backend}. The mesh may be less responsive while the scan runs.",
    "integration.reload": "This will temporarily unload the {label} integration and set it up again.",
    "integration.reconfigure": (
        "Phoenix will submit agent-provided values to {label}'s Home Assistant reconfigure flow. "
        "The integration has not validated them yet. Browser or OAuth and asynchronous progress "
        "steps are unsupported, and Phoenix cannot automatically roll this change back."
    ),
    "integration.remove": "This will remove the {label} integration and its configuration from Home Assistant.",
    "delete_entity": "This will delete the registry entry for {entity_id}. The entity may be recreated later by its integration.",
    "remove_device": "This will remove {device_id} from its integration. The device may need to be added again.",
    "install_esphome_firmware": "This will compile and flash firmware to {label}. Do not disconnect the device during the update.",
    "install_esphome_firmware.jump": "This will change the ESPHome version, compile, and flash firmware to {label}. Do not disconnect the device during the update.",
    "blueprint.delete": "This will permanently delete the {domain} blueprint {rel}.",
    "blueprint.delete.consumers": "This will permanently delete {rel}. Automations and scripts that still use it: {count}.",
    "dashboard.delete": "This will permanently delete the dashboard {label}.",
    "dashboard_resource.create": "Every browser that opens a dashboard will load {url}. A JavaScript file runs with that person's Home Assistant access, so approve only a file you trust.",
    "dashboard_resource.update": "This changes a file every browser loads when it opens a dashboard. Compare the current and proposed file below.",
    "dashboard_resource.delete": "Dashboards will stop loading {url}. Cards that come from it will show an error until it is added back.",
    "dashboard_card.delete": "This will permanently delete the selected card from {label}.",
    "dashboard_card.delete.section": "This will permanently delete the selected card from {label}.",
    "delete_automation": "This will permanently delete automation {automation_id}.",
    "delete_script": "This will permanently delete script {script_id}.",
    "delete_scene": "This will permanently delete scene {scene_id}.",
    "delete_helper": "This will permanently delete helper {helper_id}.",
})

def _approval_summary_body(key: str) -> str:
    """Return concise consequence copy for one stable diff-summary key."""
    if key in _APPROVAL_SUMMARY_BODIES:
        return _APPROVAL_SUMMARY_BODIES[key]
    if key.startswith("integration."):
        return "This will change the {label} integration in Home Assistant."
    if key.startswith(("dashboard", "patch_dashboard", "edit_energy_config")):
        return "This will update the Home Assistant dashboard configuration."
    if key in ("location.delete", "label.delete", "category.delete"):
        return "This will remove the selected item. The change may not be reversible."
    if key.startswith(("create_", "edit_", "set_", "patch_", "blueprint.", "location.", "label.", "category.")):
        return "This will apply the proposed configuration change."
    if key.startswith(("call_service", "mesa_service")):
        return "This will run the proposed Home Assistant service action."
    if key.startswith(("hass_turn", "hass_set_position", "hass_stop_moving")):
        return "This will control the selected physical device or devices."
    if key.startswith("zigbee_permit"):
        return "This will change whether new devices can join the Zigbee network."
    if key.startswith(("delete_", "remove_")):
        return "This will remove the selected item. The change may not be reversible."
    return "This will apply the proposed action."

APPROVAL_SUMMARY_TEMPLATES: Mapping[str, str] = MappingProxyType({
    part_key: template
    for key, diff_title in DIFF_SUMMARY_TEMPLATES.items()
    for part_key, template in (
        (f"{key}.title", _APPROVAL_SUMMARY_TITLE_OVERRIDES.get(key, diff_title)),
        (f"{key}.body", _approval_summary_body(key)),
    )
})

APPROVAL_SUMMARY_UI: Mapping[str, str] = MappingProxyType({
    "action.optionalReason": "Optional reason",
    "action.reasonPlaceholder": "Explain why this request was rejected",
    "fallback.file.title": "Update {file}",
    "fallback.integration.title": "Update integration {label}",
    "fallback.item.title": "Update {label}",
    "fallback.service.title": "Run {label}",
    "fallback.unknown.title": "Review proposed action",
    "targets.exact": "This approval covers the entities listed below. Changed targets require a new request.",
    "targets.futureNative": "Home Assistant resolves future targets when this configuration runs. Membership may change, and later execution is outside Phoenix permission and MESA checks.",
    "targets.dynamicAccessible": "Accessible devices are resolved again at execution and may have changed.",
    "named.editAutomation.title": "Edit automation '{label}'",
    "named.removeDevice.title": "Remove {device} from {integration}",
    "named.removeDevice.body": "This will ask the {integration} integration to remove {device}. You may need to add the device again, and this action cannot be undone from Phoenix MCP.",
    "named.removeDevice.unnamedTitle": "Remove device from {integration}",
    "named.removeDevice.unnamedBody": "This will ask the {integration} integration to remove the selected device. You may need to add the device again, and this action cannot be undone from Phoenix MCP.",
    "named.removeDevice.integrationFallback": "its current integration",
    "named.kind.automation": "automation",
    "named.kind.script": "script",
    "named.kind.scene": "scene",
    "named.kind.helper": "helper",
    "named.kind.input_boolean": "input boolean helper",
    "named.kind.input_number": "number helper",
    "named.kind.input_text": "text helper",
    "named.kind.input_select": "dropdown helper",
    "named.kind.input_datetime": "date and time helper",
    "named.kind.input_button": "button helper",
    "named.kind.counter": "counter helper",
    "named.kind.timer": "timer helper",
    "named.kind.schedule": "schedule helper",
    "named.kind.zone": "zone helper",
    "named.kind.tag": "tag helper",
    "named.kind.person": "person helper",
    "named.resource.createTitle": "Create {kind} '{label}'",
    "named.resource.editTitle": "Edit {kind} '{label}'",
    "named.resource.deleteTitle": "Delete {kind} '{label}'",
    "named.resource.deleteUnnamedTitle": "Delete {kind}",
    "named.resource.createBody": "This will add {kind} '{label}' to Home Assistant. Review its configuration below.",
    "named.resource.editBody": "This will update {kind} '{label}'.",
    "named.resource.deleteBody": "This will permanently delete {kind} '{label}'. This action cannot be undone.",
    "named.resource.deleteUnnamedBody": "This will permanently delete the selected {kind}. This action cannot be undone.",
    "history.approved.body": "The request completed successfully.",
    "history.rejected.body": "The request was rejected and was not run.",
    "history.rejectedReason.body": "The request was rejected and was not run. Reason: {reason}",
    "history.expired.body": "The request expired and was not run.",
    "history.cancelled.body": "The request was cancelled and was not run.",
    "history.cancelledReason.body": "The request was cancelled and was not run. Reason: {reason}",
    "history.error.generic": "The action could not be completed. Request Details shows the full error under Result.",
    "history.error.integrationChanged": "The integration changed after approval. Review the current request details and try again.",
    "history.error.integrationDisabled": "The integration is disabled and cannot be reloaded.",
    "history.error.integrationLoggerChanged": "The integration's logging configuration changed after approval. Review the current request details and try again.",
    "history.error.integrationNotFound": "The integration no longer exists.",
    "history.error.integrationReloadFailed": "Home Assistant could not reload the integration.",
    "history.error.integrationReloadUnsupported": "This integration does not currently support reloading.",
    "history.error.integrationStateNotReloadable": "The integration is currently in state {state} and cannot be reloaded.",
    "history.failed.body": "The request was approved but did not complete. Error: {error}",
    "history.interrupted.body": "Execution was interrupted and the change may have been applied. Verify the current state before requesting it again.",
    "history.resolvedAt": "Resolved {time}",
})

# Version-record summaries, the Changes tab's one-line "what changed" text.
#
# Same contract as DIFF_SUMMARY_TEMPLATES above and generated into the same
# catalog by the same script, but a separate surface: an approval diff describes
# a change being PROPOSED, a version record describes one already APPLIED, and
# only some resource types get one at all (an automation's alias already tells
# the story, so those stay None).
#
# The card-op location is assembled from the three loc* parts joined with the
# panel's list separator, rather than one key per combination of view/section/
# card: it is a coordinate list, not prose, so the order is fixed and only the
# nouns need translating.
VERSION_SUMMARY_TEMPLATES: Mapping[str, str] = MappingProxyType({
    "cards": "cards: {count}",
    "cards.was": "cards: {count} (was {before})",
    "size": "{size}",
    "size.was": "{size} (was {before})",
    "entity.removed": "registry entry removed",
    "entity.changed": "changed: {fields}",
    "device.removed": "device registry entry removed",
    "device.changed": "changed: {fields}",
    "device.zigbee_options": "changed Zigbee2MQTT options: {keys}",
    "device.zigbee_property": "changed Zigbee2MQTT property: {property}",
    "device.zigbee_binding.bind": "bound Zigbee control to {target}",
    "device.zigbee_binding.unbind": "removed the Zigbee binding to {target}",
    "device.zigbee_reporting": "configured Zigbee reporting: {cluster}.{attribute}",
    "device.zigbee_group.create": "created Zigbee group: {name}",
    "device.zigbee_group.add_members": "added to Zigbee group: {name}",
    "device.zigbee_group.remove_members": "removed from Zigbee group: {name}",
    "device.zigbee_group.delete": "removed Zigbee group: {name}",
    "config_entry.removed": "integration removed",
    "card.added": "added {subject} ({where})",
    "card.edited": "edited {subject} ({where})",
    "card.deleted": "deleted {subject} ({where})",
    "loc.view": "view {index}",
    "loc.section": "section {index}",
    "loc.card": "card {index}",
    "energy.sources": "sources: {count}, devices: {devices}",
    "config_entry.options": "reconfigured {subject}",
    "config_entry.reconfigure": "reconfigured {subject}",
    "patch.set": "set {subject}",
    "patch.append": "appended to {subject}",
    "patch.remove": "removed {subject}",
    "patch.multi": "changes: {count}",
})

# Persistent-notification text, the only Phoenix strings HA itself renders
# outside the panel.
#
# Server-rendered and broadcast to every admin, so there is no per-viewer
# language to key off: these resolve against hass.config.language, the
# instance's own setting. Same {name} placeholder rule as the summary templates,
# and generated into the catalog by the same script.
NOTIFICATION_TEMPLATES: Mapping[str, str] = MappingProxyType({
    "approval.title": "Phoenix MCP",
    # The link label is inside the markdown, so it has to be part of the string;
    # the URL is substituted and must not be translated.
    "approval.message": "Access key '{access_key}' requested approval.\n\n[Review in Phoenix MCP]({url})",
    "approval.recovery_message": "Configuration recovery needs review: a previous change could not be verified.\n\n[Review in Phoenix MCP]({url})",
    "rate_limit.title": "Phoenix MCP Alert",
    "rate_limit.message": "Phoenix MCP: access key '{access_key}' has hit its rate limit.",
})

# The sentences the voice agent speaks when it declines a turn. Unlike a
# notification (one stored string shown to every admin, so it follows the
# SERVER language), these are spoken back to whoever is talking, so they
# resolve against the CONVERSATION's own language.
#
# `unavailable` deliberately does not say WHY. It covers the kill switch, an
# admin-disabled agent, and shutdown with one sentence, and the kill switch is
# a security control: naming it would announce the gateway's state to anyone
# within earshot of the satellite.
VOICE_TEMPLATES: Mapping[str, str] = MappingProxyType({
    "local_cancelled": "Cancelled.",
    "local_clarification": "Which target do you mean? {choices}. Reply with its number or exact entity ID, or say cancel. This choice expires in two minutes.",
    "local_clarification_expired": "That choice is no longer current. Please repeat the full command.",
    "local_unauthorized": "This access key is no longer authorized.",
    "local_state_matched": "Home Assistant reports the requested state.",
    "local_state_unconfirmed": "Command sent. Home Assistant has not yet confirmed the requested state.",
    "local_pending": "This command needs approval in the Phoenix MCP panel.",
    "local_review": "Review command",
    # Link label after a typed Assist reply that queued approvals (not spoken).
    "approval_link": "Approve in the Phoenix MCP panel",
    "local_failed": "Phoenix MCP could not complete this command.",
    "local_state": "The state is {state}.",
    "local_state_unknown": "Unknown",
    "local_state_unavailable": "Unavailable",
    'local_summary_count': 'Entities exposed to Assist that I can access, with state {state}: {matched} of {total}. Unknown or unavailable: {unknown}.',
    'local_summary_total': 'Entities exposed to Assist that I can access: {total}. Unknown or unavailable: {unknown}.',
    'local_summary_breakdown': 'Entities exposed to Assist that I can access: {total}. {summary}.',

    "local_read_failed": "Phoenix MCP could not read the state.",
    "local_attribute_unavailable": "Home Assistant is not reporting a value for the requested property right now.",
    "unavailable": "The Phoenix MCP voice agent is currently unavailable.",
    "not_configured": "The Phoenix MCP voice agent is not fully configured.",
    "access_key_unavailable": "The Phoenix MCP voice agent's access key is no longer available.",
    "error": "Sorry, the Phoenix MCP voice agent hit an error.",
    # How a turn that did not finish cleanly is SPOKEN. The loop reports an
    # outcome code and each surface renders it from here, so a Chinese pipeline
    # does not hear the declines above translated and then an English sentence
    # the moment anything went wrong. {reason} is the provider's own error text
    # and stays in whatever language the provider produced: it is third-party
    # output and is not translated.
    "could_not_complete": "Sorry, I could not complete that.",
    "provider_unavailable": "Sorry, the language model could not answer right now.",
    "provider_slow": "Sorry, the language model is taking too long to answer. Try again in a moment.",
    "model_api_unsupported": "This model is not qualified for OpenCode Chat Completions. Choose a supported model.",
    "outcome_unknown": "This action may have run. Check Home Assistant and its approval history before trying it again. Open Needs Attention in Phoenix MCP.",
    "prior_action_unresolved": "This request was not executed because a previous action needs checking. Open Needs Attention in Phoenix MCP.",
    'companion_accepted_direct': 'Home Assistant accepted the command for {names}.',
    'companion_accepted_warm': 'Your command for {names} has been accepted by Home Assistant.',
    'companion_accepted_calm_guide': 'Home Assistant confirms it accepted the command for {names}.',
    'companion_accepted_lively': 'Command accepted for {names}.',
    'companion_accepted_technical': 'Command receipt: accepted. Targets: {names}.',
    'companion_verified_short': 'It reports the requested settings.',
    'companion_receipt_limit': 'A command receipt confirms acceptance; the state check shows what Home Assistant reports, not physical proof.',
    'companion_group_targets': '{kind} in {area} ({count})',
    'companion_action_lights': 'lights',
    'companion_action_fans': 'fans',
    'companion_action_devices': 'devices',
    'companion_verification_progress': 'Targets Home Assistant reports with the requested settings: {count} of {total}.',
    'companion_unavailable_targets': 'Unavailable ({count}): {names}. Check in Home Assistant; do not resend commands.',
    'companion_unverified_targets': 'Not verified ({count}): {names}. Check in Home Assistant; do not resend commands.',
    'companion_count_direct': 'Matching {kind} in your permitted scope: {count}.',
    'companion_count_warm': "Matching {kind} I can see within Phoenix's permitted scope: {count}.",
    'companion_count_calm_guide': 'Matching {kind} Phoenix can access: {count}. This count covers your permitted scope.',
    'companion_count_lively': 'Matching {kind} visible to Phoenix: {count}.',
    'companion_count_technical': "Matching {kind} within Phoenix's permitted scope, count: {count}.",
    'companion_kind_light': 'light entities',
    'companion_kind_fan': 'fan entities',
    'companion_kind_sensor': 'sensor entities',
    'companion_kind_other': 'entities',
    'companion_count_groups': 'This counts Home Assistant entities, which may include groups.',
    'companion_count_scope': 'Only matching entities this assistant can access are counted; this does not establish the number of physical devices.',
    'companion_power_on': 'Turned on {names}.',
    'companion_power_off': 'Turned off {names}.',
    "companion_readings": "Home Assistant reports: {readings}.",
    "companion_clarify": "Please try again with the complete request: what to do, which device or room, and any restrictions. Do not reply with only a device name.",
    "companion_candidates": "Matches include: {names}.",
    "companion_accepted": "Home Assistant accepted the device commands.",
    "companion_partial": "Some device commands were accepted, but other commands were refused or could not be confirmed.",
    "companion_targets": "Devices: {names}.",
    "companion_verified": "Home Assistant reports the accepted devices at their commanded settings.",
    "companion_unverified": "The final device states were not verified. Check them in Home Assistant; do not resend commands just because confirmation is missing.",
    "companion_refused": "The requested operation was refused or could not be confirmed. Check the device state and this assistant's permissions before deciding what to do next.",
    "companion_read_failed": "The lookup did not finish. No device command was attempted. Please try again with the complete request.",
    "companion_limit": "Phoenix reached an information limit before finishing. No device command was attempted. Keep the same requested scope when trying again; if this repeats, ask the administrator to review the request limits.",
    "companion_steps": "Phoenix reached its step limit before finishing. No device command was attempted. Please try again with the same complete request.",
    "companion_busy": "Another request is still running. This request did not start; wait for the earlier result before sending a complete new request.",
    "companion_no_match": "No accessible device matched the lookup. This does not establish whether the name, filters or permissions caused it. Check those details and try again with the complete request. No device command was attempted.",
    "output_limit": "Sorry, the model produced too much output, so I stopped.",
    "out_of_steps": "Sorry, I ran out of steps before finishing that.",
    "focus_declined": (
        "This conversation is in Home-focused mode, so I can only help with requests "
        "about your home and Home Assistant."
    ),
    # Appended after a real answer, so it is a follow-on sentence, not a reply.
    "answer_truncated": "I had to stop before finishing.",
})
