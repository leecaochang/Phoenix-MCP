---
{ "id": "window.attention", "title": "{{label:attention.title}}", "parent": "topic.windows", "order": 22, "keywords": ["unknown outcome", "interrupted", "blocked writes", "review"], "sectionKeywords": { "actions": ["check a previous action", "unknown", "calendar"], "review": ["mark reviewed", "show in audit log", "blocked", "write refused"], "problems": ["MESA could not start", "calendar history"], "window": ["show needs attention", "close"] }, "covers": ["frontend_src/components/AttentionPanel.tsx", "frontend_src/shell/Shell.tsx", "custom_components/phoenix_mcp/admin/changes.py", "custom_components/phoenix_mcp/agent_recovery.py"] }
---
Things Phoenix can't settle by itself. The window appears over the workspace while there's something to check, and goes away when the list is empty. It checks for new items every 15 seconds.

## Actions with an unknown outcome {#actions}

An action that reached Home Assistant, but Phoenix never learned whether it worked. This happens when a conversation is cut off, an external app disconnects during a change, or Home Assistant restarts mid-action. It also happens when Agent Chat makes a change Phoenix can't confirm. The item names the access key, the tool and when it started.

Until you review it, that access key can't make any more changes, though it can still read. A calendar change with an unknown outcome is listed the same way.

An app that disconnects while it waits for your approval isn't listed: the request stays in Approvals. While one of a key's changes is still running, its other changes are refused until that one finishes, and reads carry on.

## Reviewing an action {#review}

Check in Home Assistant whether the action happened. For an agent's action, **{{label:attention.showInAudit}}** opens the Audit Log at that request.

Then select **{{label:attention.continue}}**, tick **{{label:attention.checked}}** and confirm. Phoenix doesn't repeat or undo the action. The key can make changes again, and the audit log records your review.

## Problems that need a fix {#problems}

- **{{label:attention.mesaTitle}}**: MESA failed to start, so protected changes are paused. Check the Home Assistant log. If it says the profile storage is corrupt, [follow its steps](guide:topic.mesaRules#storage). Then reload Phoenix.
- **{{label:attention.calendar_storageTitle}}**: calendar changes are paused until Phoenix can read its saved calendar history again.
- **{{label:attention.audit_storageTitle}}**: Phoenix can't write the audit log to disk, so a restart would lose recent entries. Look for a disk or storage error in the Home Assistant log. The item goes away after the next save works.

These go away by themselves once the problem is fixed.

## Closing the window {#window}

Closing the window hides it in this workspace until an item it didn't show arrives. **{{label:workspace.showAttention}}** in Workspace Options brings it back, and so does resetting the workspace or all workspaces. On a phone, close it from its title bar.
