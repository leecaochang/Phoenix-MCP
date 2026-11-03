---
{ "id": "window.changes", "title": "{{label:shell.tabChanges}}", "parent": "workspace.changes", "order": 1, "keywords": ["history", "list", "versions"], "sectionKeywords": { "filters": ["type", "action", "search"] }, "covers": ["frontend_src/views/ChangesView.tsx", "frontend_src/shell/changes.tsx", "frontend_src/utils/dashboard_names.ts"] }
---
Every change Phoenix recorded, newest first. Choose one to compare it in [{{label:workspace.comparison}}](guide:window.comparison). The list updates itself as agents make changes.

## {{label:logs.filters}} {#filters}

**{{label:logs.filters}}** on the toolbar shows the filters: a type, an action (create, edit, delete or rollback), and a search box. Search looks through the changes loaded so far, by item, type, action, who made it and summary.

Changing a filter or the search clears the chosen change. Closing the filters stops them from applying; they come back as they were when you reopen them. **{{label:logs.clearFilters}}** resets all three.

## The list {#list}

Each row shows when the change was made, the item, the action, its type, and who made it: the access key, or **{{label:changes.byAdmin}}** for a restore from this panel.

Sort, move and hide columns from the column header's menu, and drag a header's edge to resize one. The count under the list includes changes not loaded yet.

## {{label:common.loadMore}} {#load-more}

Loads the next 100 changes.
