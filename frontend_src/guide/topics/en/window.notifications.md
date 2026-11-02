---
{ "id": "window.notifications", "title": "{{label:notifications.title}}", "parent": "topic.windows", "order": 21, "keywords": ["messages", "history"], "sectionKeywords": { "toasts": ["toast", "popup", "dismiss", "confirmation"], "history": ["clear notification", "show notification history"] }, "covers": ["frontend_src/ui/notify.tsx", "frontend_src/shell/notifications.tsx"] }
---
The messages Phoenix showed you during this visit to the panel.

## Messages {#toasts}

When something you did in the panel finishes, such as saving a MESA profile, a short message appears at the bottom of the screen for a few seconds. Point at it or move focus into it to keep it there. **{{label:notifications.dismiss}}** hides it.

Problems aren't shown this way. They appear in the window where they happened.

## {{label:notifications.title}} window {#history}

Lists the last 30 messages, newest first, with the time each appeared, and the date for one from an earlier day. They're kept until you reload the panel. **{{label:notifications.clear}}** removes one.

The window starts closed. Open it from **{{label:notifications.showHistory}}** in Workspace Options. It isn't available on a phone, where messages still appear.
