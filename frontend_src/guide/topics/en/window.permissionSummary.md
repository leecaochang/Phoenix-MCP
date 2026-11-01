---
{ "id": "window.permissionSummary", "title": "{{label:accessKeys.permissionSummary}}", "parent": "workspace.accessKeys", "order": 5, "keywords": ["overview", "rules", "grants"], "covers": ["frontend_src/components/PermissionSummary.tsx", "frontend_src/views/AccessKeyDetail.tsx"] }
---
Lists every rule set in this key's [{{label:accessKeys.permissionsTree}}](guide:window.permissions): each domain, device or entity set to R, W or D. Rows left at N aren't listed, and neither is what they inherit.

## The table {#summary-table}

Shows each rule's type, name, ID and state. The state reads Deny when a D on the device or domain overrides the rule. Use the column header's menu to sort, move, resize or hide columns.

Select a row to show it in the {{label:accessKeys.permissionsTree}}. The MESA button on an entity row opens its MESA profile.

## An empty summary {#empty}

An empty summary means nothing is granted in the tree. Tools that don't use the tree, such as reading configuration or managing automations, still work if the key's capabilities allow them.

On a pass-through key, the summary says the key skips the tree instead.
