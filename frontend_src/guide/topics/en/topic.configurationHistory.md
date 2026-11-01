---
{ "id": "topic.configurationHistory", "title": "What Changes Records", "parent": "workspace.changes", "order": 10, "keywords": ["history", "versions", "retention", "undo", "recovery"], "sectionKeywords": { "restore": ["undo", "revert", "recreate"], "not-recorded": ["missing", "dashboard resources", "calendar"], "recovery": ["safety copy", "restore_configuration"] }, "covers": ["custom_components/phoenix_mcp/version_store.py", "custom_components/phoenix_mcp/tool_common.py", "custom_components/phoenix_mcp/mcp_view.py", "custom_components/phoenix_mcp/config_recovery.py", "custom_components/phoenix_mcp/const.py"] }
---
Phoenix records a change after an agent's write has succeeded, with the item before and after it. Changes approved in Approvals are recorded the same way; requests you reject record nothing.

## What is recorded {#recorded}

- Automations, scripts and scenes.
- Helpers, including zones, tags and people, and the settings of integration-based helpers.
- Dashboard layouts and the Energy dashboard.
- Blueprints.
- Entity and device registry edits, and integration names and options.
- Areas, floors, labels and categories.
- Restores from this panel, as rollbacks.

## What is not {#not-recorded}

Changes made outside Phoenix, in Home Assistant itself, aren't recorded. Neither are actions on devices, calendar events, to-do items, the dashboard list or dashboard resources.

Some records exist only as evidence and can't be restored, such as removing an integration or a device.

## How long it's kept {#retention}

Phoenix keeps the 20 newest changes to each item, with no time limit. History survives restarts. Deleting core data in [Data Management](guide:window.settingsSafety#wipe-core) clears it.

Records hold the configuration exactly as it was, so a restore can put it back. Only administrators can see them.

## What a restore can put back {#restore}

- Automations, scripts, scenes, helpers and blueprints: edited back, or recreated under their original ID if they were deleted.
- Dashboards: the layout is put back, but a deleted dashboard isn't recreated.
- Entities and devices: their names, areas, labels and similar registry details. A deleted entity can't be recreated.
- Areas, floors, labels and categories: only while the same item still exists.
- The Energy dashboard: its whole configuration.

Restoring a helper usually replaces it whole, so fields added since are removed. A configuration that used !secret, !include or similar tags can't be restored.

## Safety copies {#recovery}

Before some risky writes, Phoenix also keeps a safety copy, separate from Changes. If a write may not have finished, Phoenix asks in Approvals whether to put the copy back. Safety copies survive deleting core data.
