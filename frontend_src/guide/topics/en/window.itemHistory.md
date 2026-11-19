---
{ "id": "window.itemHistory", "title": "{{label:workspace.itemHistory}}", "parent": "workspace.changes", "order": 3, "keywords": ["versions", "timeline", "earlier"], "covers": ["frontend_src/views/ChangesView.tsx"] }
---
Every recorded change to the chosen item, newest first, with the chosen one marked. Select a change to compare it. **{{label:workspace.previousRecord}}** and **{{label:workspace.nextRecord}}** then move through this list.

When the item has more than one change, it opens by itself under the Changes list. If you close it, it stays closed. Reopen it from **{{label:workspace.options}}**.

Phoenix keeps the 20 newest changes to each item. When an entity is renamed, its changes move to its new ID, so its history stays in one list.
