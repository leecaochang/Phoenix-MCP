---
{ "id": "topic.approvalLifecycle", "title": "How an Approval Works", "parent": "workspace.approvals", "order": 10, "keywords": ["confirm", "lifecycle", "expire", "pending_approval", "wait_for_approval"], "sectionKeywords": { "agent": ["inline wait", "wait_for_approval", "voice", "assist"], "expiry": ["expire", "expired", "timeout", "one hour"], "history": ["retention", "kept"] }, "covers": ["custom_components/phoenix_mcp/approvals.py", "custom_components/phoenix_mcp/tool_common.py", "custom_components/phoenix_mcp/admin_view.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/approval_targets.py", "custom_components/phoenix_mcp/catalog_templates.py"] }
---
What happens to a request from the moment an agent asks until it's decided, and what the agent is told along the way.

## What waits for you {#held}

A capability set to Confirm holds the changes it covers. Reads covered by the same capability run straight away.

MESA's Confirm holds an action only while MESA is Enforced. One approval covers both, so you never approve the same action twice. Configuration recovery also asks here before it puts something back.

Related: [Deny, Allow and Confirm](guide:window.capabilities#modes)

## What the agent is told {#agent}

- External apps hear at once that the request is waiting, and can ask for the outcome later. With an [inline approval wait](guide:window.keySettings#inline-wait), the app waits up to that long for your decision first.
- Agent Chat waits for your decision for up to 15 minutes. If no decision comes by then, or the chat ends first, the request is cancelled.
- The Voice Agent, Assist and AI Task don't wait: they report that the change is waiting for approval and carry on. Approving later still runs it.

## How long a request waits {#expiry}

A pending request expires after one hour. Once it has, Review offers only Reject, and rejecting it records it as expired. Each access key can have up to 100 pending requests at once.

## What happens when you approve {#approve}

Phoenix checks the request again before running it:

- An expired request closes as expired.
- If the access key was revoked or has expired, the request is cancelled.
- If its capability is now Deny, the request fails without running.
- If the kill switch is on, the request is cancelled, unless it's a configuration recovery review.

Once those pass, Phoenix records your decision before it runs anything. Each action also checks its own targets, permissions and MESA rules again. If the devices it targets have changed since the request, it's refused, and the agent must ask again.

## How it can end {#outcomes}

- Approved: it ran, and its result is saved.
- Failed: it was refused at the last check, or the action returned an error. It isn't offered again.
- Interrupted: it stopped while running and may have partly applied. Check the result yourself.

Only a failure before the action starts leaves the request pending, to approve again.

## Rejecting {#reject}

The agent receives your reason, or is told to ask you if you gave none. A rejected configuration recovery keeps the current configuration.

## What ends a request early {#cancelled}

Restarting Home Assistant cancels every pending request. Reloading Phoenix doesn't, and an approved action that's already running carries on. Revoking the access key, the key expiring and deleting core data cancel its requests too. Approving while the kill switch is on cancels the request instead, except for a configuration recovery review.

## Notifications {#notify}

With **{{label:settings.notifyApprovalLabel}}** on, each request raises a Home Assistant notification that links to it, and the notification goes away once the request is decided.

Related: [Home Assistant notifications](guide:window.settingsLogs#notify-approval)

## History {#history}

Decided requests are kept for seven days, up to the newest 500. Their arguments, change and result are cleared about an hour after the decision. Interrupted requests stay in the list, so you can check what happened, but their arguments, change and result are cleared after seven days too.
