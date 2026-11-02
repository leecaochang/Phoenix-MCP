---
{ "id": "window.help", "title": "{{label:help.title}}", "parent": "topic.windows", "order": 20, "keywords": ["help bar", "tooltip", "hint"], "sectionKeywords": { "showing": ["show help bar", "hide", "documentation"] }, "covers": ["frontend_src/shell/help.tsx", "frontend_src/shell/Shell.tsx", "frontend_src/shell/storage.ts"] }
---
A short line about the control under the pointer, or the one in focus when the pointer is elsewhere. It sits along the bottom of every workspace and doesn't scroll.

Pointing at the Help window itself keeps its text, so you can read it. For more than a line, open the Guide from the Documentation menu.

## Showing and hiding Help {#showing}

**{{label:help.showHelpBar}}** in the Documentation menu shows or hides Help in every workspace. It's on by default.

Closing Help in one workspace hides it there only. Show it again from **{{label:help.showHelpBar}}**, and it applies to every workspace again.

Help isn't available on a phone.
