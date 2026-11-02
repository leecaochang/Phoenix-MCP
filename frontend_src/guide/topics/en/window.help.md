---
{ "id": "window.help", "title": "{{label:help.title}}", "parent": "topic.windows", "order": 20, "keywords": ["quick help", "help bar", "tooltip", "hint"], "sectionKeywords": { "showing": ["help menu", "show", "hide", "documentation"] }, "covers": ["frontend_src/shell/help.tsx", "frontend_src/shell/Shell.tsx", "frontend_src/shell/storage.ts"] }
---
A short line about the control under the pointer, or the one in focus when the pointer is elsewhere. It sits along the bottom of every workspace and doesn't scroll.

Pointing at the Quick Help window itself keeps its text, so you can read it. For more than a line, open the Guide from the **{{label:shell.help}}** menu.

## Showing and hiding Quick Help {#showing}

**{{label:help.title}}** in the **{{label:shell.help}}** menu shows or hides it in every workspace. It's on by default.

Closing Quick Help in one workspace hides it there only. Choose it again in the **{{label:shell.help}}** menu, and it applies to every workspace again.

Quick Help isn't available on a phone.
