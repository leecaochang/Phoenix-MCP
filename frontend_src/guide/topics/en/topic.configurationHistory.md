---
{ "id": "topic.configurationHistory", "title": "What Changes Records", "parent": "workspace.changes", "order": 10, "keywords": ["history", "versions", "retention", "undo", "recovery"], "sectionKeywords": { "restore": ["undo", "revert", "recreate"], "not-recorded": ["missing", "dashboard resources", "calendar"], "recovery": ["safety copy", "restore_configuration"] }, "covers": ["custom_components/phoenix_mcp/version_store.py", "custom_components/phoenix_mcp/tool_common.py", "custom_components/phoenix_mcp/tools/restore.py", "custom_components/phoenix_mcp/config_recovery.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/config_reconciliation.py", "custom_components/phoenix_mcp/recovery_storage.py", "custom_components/phoenix_mcp/template_recovery.py", "custom_components/phoenix_mcp/template_snapshots.py"] }
---
After an agent's write succeeds, Phoenix records the change, with the item before and after it. Phoenix records changes you approve in {{label:shell.tabApprovals}} the same way. Requests you reject record nothing.

## What is recorded {#recorded}

- Automations, scripts, and scenes
- Helpers, including zones, tags, and people, and the settings of integration-based helpers
- Dashboard layouts and the Energy dashboard
- Blueprints
- Entity and device registry edits, and integration names and options
- Areas, floors, labels, and categories
- Restores from this panel, as rollbacks

## What is not {#not-recorded}

Changes made outside Phoenix, in Home Assistant itself, aren't recorded. Neither are actions on devices, calendar events, to-do items, the dashboard list, or dashboard resources.

A removed to-do item can't be brought back. Before you approve a removal, check the items.

Some records exist only as evidence and can't be restored, such as removing an integration or a device.

## How long it's kept {#retention}

Phoenix keeps the 20 newest changes to each item, with no time limit. History survives restarts. Deleting core data in [{{label:settings.dataCard}}](guide:window.settingsSafety#wipe-core) clears it.

Records hold the configuration exactly as it was, so a restore can put it back. Only administrators can see them.

## What a restore can put back {#restore}

- Automations, scripts, scenes, helpers, and blueprints: edited back, or recreated under their original ID if they were deleted.
- Dashboards: the layout is put back, but a deleted dashboard isn't recreated.
- Entities and devices: their names, areas, labels, and similar registry details. A deleted entity can't be recreated.
- Areas, floors, labels, and categories: only while the same item still exists.
- The Energy dashboard: its whole configuration.

Restoring a helper usually replaces it whole, so fields added since are removed. A configuration that used !secret, !include, or similar tags can't be restored.

## Safety copies {#recovery}

Before some risky writes, Phoenix also keeps a safety copy, separate from {{label:shell.tabChanges}}. If a write might not have finished, Phoenix asks in {{label:shell.tabApprovals}} whether to put the copy back. Safety copies survive deleting core data.

Restoring a dashboard to a copy from before it existed deletes the dashboard with its views. Phoenix first keeps a safety copy of the views. The review tells you that the restore deletes the dashboard.

Phoenix won't keep a copy that holds a secret, so it refuses the change instead. For a dashboard, the agent's message names the place in the layout. Ordinary card settings such as `max_tokens` or a `session` label are fine. The layout can nest up to 100 levels.

Phoenix checks unfinished writes every 30 seconds and at startup. It keeps up to 256 safety copies, each up to 1 MiB. Resolved copies are kept for 30 days, and at most 20 per item.
