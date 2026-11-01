---
{ "id": "window.itemHistory", "title": "{{label:workspace.itemHistory}}", "parent": "workspace.changes", "order": 3, "keywords": ["versions", "timeline", "earlier"], "covers": ["frontend_src/views/ChangesView.tsx"] }
---
Every recorded change to the chosen item, newest first, with the chosen one marked. Choose a change to compare it, and **{{label:workspace.previousRecord}}** and **{{label:workspace.nextRecord}}** then move through this list.

It opens by itself, under the Changes list, when the item has more than one change. If you close it, it stays closed; reopen it from **{{label:workspace.options}}**.

Phoenix keeps the 20 newest changes to each item.
