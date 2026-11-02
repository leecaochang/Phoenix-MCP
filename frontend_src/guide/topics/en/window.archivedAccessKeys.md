---
{ "id": "window.archivedAccessKeys", "title": "{{label:accessKeys.archivedTitle}}", "parent": "window.accessKeys", "order": 1, "keywords": ["revoked", "expired", "old keys"], "covers": ["frontend_src/views/AccessKeyList.tsx", "frontend_src/components/ArchivedAccessKeyTable.tsx"] }
---
Keys you revoked and keys that expired. **{{label:accessKeys.showArchived}}** on the toolbar opens this window over the workspace. Selecting a key in the list closes it again.

An archived key keeps its name, dates and who created it. Its capabilities, permissions and settings are gone, and it can never be used again.

## {{label:accessKeys.deletePermanently}} {#delete-permanently}

Removes a key from this list. Select it, then **{{label:accessKeys.confirmDelete}}**. Click anywhere else to cancel.
