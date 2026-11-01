---
{ "id": "window.attention", "title": "{{label:attention.title}}", "parent": "topic.windows", "order": 22, "keywords": ["unknown outcome", "interrupted", "blocked writes", "review"], "sectionKeywords": { "actions": ["check a previous action", "unknown", "calendar"], "review": ["mark reviewed", "show in audit log", "blocked", "write refused"], "problems": ["MESA could not start", "calendar history"], "window": ["show needs attention", "close"] }, "covers": ["frontend_src/components/AttentionPanel.tsx", "frontend_src/shell/Shell.tsx", "custom_components/phoenix_mcp/admin_view.py", "custom_components/phoenix_mcp/agent_recovery.py"] }
---
Things Phoenix can't settle by itself. The window appears over the workspace while there's something to check, and goes away when the list is empty. It checks for new items every 15 seconds.

## Actions with an unknown outcome {#actions}

An action that reached Home Assistant, but Phoenix never learned whether it worked. This happens when a conversation is cut off or Home Assistant restarts mid-action. The item names the access key, the tool and when it started.

Until you review it, that access key can't make any more changes. A calendar change with an unknown outcome is listed the same way.

## Reviewing an action {#review}

Check in Home Assistant whether the action happened. For an agent's action, **{{label:attention.showInAudit}}** opens the Audit Log at that request.

Then select **{{label:attention.continue}}**, tick **{{label:attention.checked}}** and confirm. Phoenix doesn't repeat or undo the action. The key can make changes again, and the audit log records your review.

## Problems that need a fix {#problems}

- **{{label:attention.mesaTitle}}**: MESA failed to start, so protected changes are paused. Check the Home Assistant log, then reload Phoenix.
- **{{label:attention.calendar_storageTitle}}**: calendar changes are paused until Phoenix can read its saved calendar history again.

These go away by themselves once the problem is fixed.

## Closing the window {#window}

Closing the window hides it in this workspace until more items arrive. **{{label:workspace.showAttention}}** in Workspace Options brings it back.
