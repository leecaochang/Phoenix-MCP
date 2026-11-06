---
{ "id": "window.comparison", "title": "{{label:workspace.comparison}}", "parent": "workspace.changes", "order": 2, "keywords": ["diff", "before", "after"], "sectionKeywords": { "restore": ["undo", "rollback", "revert"], "views": ["code editor", "side by side", "stacked", "preview"] }, "covers": ["frontend_src/views/ChangesView.tsx", "frontend_src/shell/changes.tsx", "frontend_src/components/DiffView.tsx", "custom_components/phoenix_mcp/admin/changes.py", "frontend_src/components/YamlView.tsx", "frontend_src/components/DashboardPreview.tsx"] }
---
The chosen change, as the item looked before and after it. The line under the title says what the change was, its type, who made it and when, followed by a short summary.

## Stepping through changes {#step}

**{{label:workspace.previousRecord}}** and **{{label:workspace.nextRecord}}** move through the list you chose the change from: the Changes list, or Item History.

## The views {#views}

Before and after are compared line by line, removed lines in red and added lines in green. Switch between **{{label:changes.lineViewShort}}** and **{{label:changes.codeEditorShort}}**, and between **{{label:changes.stackedShort}}** and **{{label:changes.sideBySideShort}}**.

A dashboard change can also be shown as a **{{label:common.preview}}** that draws the dashboard, before or after. These choices are kept in this browser and shared with Approvals.

## {{label:changes.restore}} {#restore}

Puts the item back as it was before this change (**{{label:changes.restoreBefore}}**) or right after it (**{{label:changes.restoreAfter}}**). A restore runs with your authority as an administrator, so it needs no approval.

A greyed-out choice can't work here, and the reason shows under it. For example, a side that doesn't exist, the After of the item's newest change, an entity that was deleted, or a version holding YAML tags such as !secret. A restore can still be refused after you confirm if it no longer fits the item as it is now.

Related: [What a restore can put back](guide:topic.configurationHistory#restore)

### Confirming {#confirm}

Choosing a side asks you to confirm in place. Select **{{label:changes.confirmRestoreButton}}**, or **{{label:changes.cancelRestore}}** to back out.

If the item has a newer change by the time you confirm, Phoenix restores nothing and tells you, so check the newer change first. The server checks this too, so a change that lands at the last moment is caught as well.

### After a restore {#after-restore}

The restore is recorded as a new change, a rollback, and becomes the chosen change. To undo it, restore that rollback's Before.
