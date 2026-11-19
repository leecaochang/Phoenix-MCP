---
{ "id": "window.help", "title": "{{label:help.title}}", "parent": "topic.windows", "order": 20, "keywords": ["quick help", "help bar", "tooltip", "hint"], "sectionKeywords": { "showing": ["help menu", "show", "hide", "documentation"] }, "covers": ["frontend_src/shell/help.tsx", "frontend_src/shell/Shell.tsx", "frontend_src/shell/storage.ts"] }
---
A short line about the control under the pointer, or the one in focus when the pointer is elsewhere. It sits along the bottom of every workspace and doesn't scroll.

In an open dropdown list, it explains the choice under the pointer. Where the choice's name is all there is to know, such as a model or an access key, it says what the list is for.

Pointing at the {{label:help.title}} window itself keeps its text, so you can read it. For more than a line, open the Guide from the **{{label:shell.help}}** menu.

## Showing and hiding Quick Help {#showing}

Turn it on or off for every workspace with **{{label:help.title}}** in the **{{label:shell.help}}** menu. It's on by default.

Closing Quick Help in one workspace hides it there only. Select it again in the **{{label:shell.help}}** menu to show it in every workspace again.

Quick Help isn't available on a phone.
