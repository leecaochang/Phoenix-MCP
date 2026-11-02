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

Each change saves straight away. Click the active button again to go back to N, or drag across rows to set many at once.

The word after each name shows the result: WRITE, READ, DENY, NO ACCESS or NOT FOUND.

Related: [How a permission is decided](guide:topic.permissionResolution)

## The tree {#tree}

Domains are at the top, with their devices and entities below. Entities without a device are grouped under **{{label:perms.deviceless}}**, and a device made of parts shows them nested beneath it.

A grant on a domain or device also covers entities added to it later; the **{{label:perms.dynamicBadge}}** badge says so. A "!" marks domains where W reaches further than it looks, such as homeassistant, backup, automation, script and scene.

A row marked Missing is a grant on an entity that isn't in Home Assistant's list of enabled entities any more. Unmatched Device Permissions, at the end, lists grants on devices that no longer exist; set them to N to remove them.

The tree updates itself when entities, devices, areas or labels change.

## Finding things {#search}

Type in the filter box to find a domain, device or entity by name or ID. The match ignores case and accents, and also spaces, dots, underscores and hyphens.

## {{label:selectBy.menu}} {#select-by}

Narrows the tree to one area, label or integration. It only changes what you see; it never grants anything. Choose **Reset** to show everything again.

## Entity hints {#hints}

A hint tells agents something about one entity, such as which lamp it is. **{{label:perms.addHint}}** appears on entities set to R, W or D.

Choose whether the hint is for this key only or for all keys, type up to 200 characters, and select **Save**. Saving empty text removes the hint. A key's own hint takes priority over one for all keys.

Hints are advice and never grant access. Agents and their model providers receive them, so never put secrets in a hint. Setting the entity back to N deletes this key's own hint for it.

## MESA profiles {#mesa-link}

The **+** or **MESA** button on a row opens the MESA profile for that domain, device or entity.

Related: [MESA](guide:workspace.mesa)

## {{label:workspace.clearAllPermissions}} {#clear-all}

**{{label:workspace.clearAllPermissions}}** on the toolbar removes every grant from this key, after you confirm. It also deletes this key's own hints; hints for all keys stay.

## Pass-through keys {#pass-through}

A pass-through key doesn't use the tree. **{{label:accessKeys.switchToScoped}}** turns the tree back on for it, straight away.

Related: [What pass-through opens](guide:topic.passThrough)
