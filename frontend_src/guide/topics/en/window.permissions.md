---
{ "id": "window.permissions", "title": "{{label:accessKeys.permissionsTree}}", "parent": "workspace.accessKeys", "order": 4, "keywords": ["entities", "devices", "domains", "grant", "scope"], "sectionKeywords": { "states": ["read", "write", "deny", "N R W D"], "select-by": ["area", "label", "integration", "room"], "hints": ["entity hint", "note"], "clear-all": ["reset permissions"], "pass-through": ["scoped mode"] }, "covers": ["frontend_src/components/EntityTree.tsx", "frontend_src/components/PermissionSelector.tsx", "frontend_src/components/PassThroughNotice.tsx", "custom_components/phoenix_mcp/policy_engine.py", "frontend_src/components/MesaProfileLink.tsx", "frontend_src/utils/companion_reach.ts"] }
---
Decides which devices and entities the key can reach, and whether it can only read them or also control them. A new key reaches nothing until you grant something here.

## Setting access {#states}

Each row has four buttons:

- **N**: no setting of its own. The row follows the level above it.
- **R**: read only.
- **W**: read and control.
- **D**: deny. It overrides every grant above and below it.

Each change saves straight away. Select the active button again to go back to N. Drag across rows to set many at once.

With the keyboard, a row's four buttons are one Tab stop. Use the arrow keys to move between N, R, W, and D. Press Enter or Space to set the one you're on. Moving alone changes nothing.

The word after each name shows the result: {{label:perms.stateWrite}}, {{label:perms.stateRead}}, {{label:perms.stateDeny}}, {{label:perms.stateNoAccess}}, or {{label:perms.stateNotFound}}.

A Home Companion key reaches only household devices, whatever you set. Other kinds of device show {{label:perms.stateNoAccess}}, and sensors, weather, calendars, and the sun show {{label:perms.stateRead}} even with W. See [{{label:personas.home_companion.label}}](guide:window.persona#home-companion).

Related: [How a permission is decided](guide:topic.permissionResolution)

## The tree {#tree}

Domains are at the top, with their devices and entities below. Entities without a device are grouped under **{{label:perms.deviceless}}**. A device made of parts shows them nested beneath it.

A grant on a domain or device also covers entities added to it later. The {{label:perms.dynamicBadge}} badge marks such a grant. A "!" marks domains where W reaches further than it looks, such as homeassistant, backup, automation, script, and scene.

A row marked {{label:perms.missingBadge}} is a grant on an entity that isn't in Home Assistant's list of enabled entities any more. {{label:perms.unmatchedDevicesTitle}}, at the end, lists grants on devices that no longer exist. Set them to N to remove them.

The tree updates itself when entities, devices, areas, or labels change.

## Finding things {#search}

Enter text in the filter box to find a domain, device, or entity by name or ID. The match ignores case and accents, and also spaces, dots, underscores, and hyphens.

## {{label:selectBy.menu}} {#select-by}

Narrows the tree to one area, label, or integration. It changes only what you see and never grants anything. Select **{{label:selectBy.reset}}** to show everything again.

## Entity hints {#hints}

A hint tells agents something about one entity, such as which lamp it is. **{{label:perms.addHint}}** appears on entities set to R, W, or D.

Choose whether the hint is for this key only or for all keys. Enter up to 200 characters, then select **{{label:common.save}}**. Saving empty text removes the hint. A key's own hint takes priority over one for all keys.

Hints are advice and never grant access. Agents and their model providers receive them, so never put secrets in a hint. Setting the entity back to N deletes this key's own hint for it.

## MESA profiles {#mesa-link}

The **+** or **MESA** button on a row opens the MESA profile for that domain, device, or entity.

Related: [MESA](guide:workspace.mesa)

## {{label:workspace.clearAllPermissions}} {#clear-all}

Removes every grant from this key, after you confirm. It's on the toolbar. It also deletes this key's own hints. Hints for all keys stay.

## Pass-through keys {#pass-through}

A pass-through key doesn't use the tree. **{{label:accessKeys.switchToScoped}}** turns the tree back on for it, straight away.

Related: [What pass-through opens](guide:topic.passThrough)
