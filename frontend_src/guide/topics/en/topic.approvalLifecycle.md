---
{ "id": "topic.approvalLifecycle", "title": "How an Approval Works", "parent": "workspace.approvals", "order": 10, "keywords": ["confirm", "lifecycle", "expire", "pending_approval", "wait_for_approval"], "sectionKeywords": { "agent": ["inline wait", "wait_for_approval", "voice", "assist"], "expiry": ["expire", "expired", "timeout", "one hour"], "history": ["retention", "kept"] }, "covers": ["custom_components/phoenix_mcp/approvals.py", "custom_components/phoenix_mcp/tool_common.py", "custom_components/phoenix_mcp/admin/approvals.py", "custom_components/phoenix_mcp/const.py", "custom_components/phoenix_mcp/approval_targets.py", "custom_components/phoenix_mcp/catalog_templates.py"] }
---
What happens to a request from the moment an agent asks until it's decided, and what the agent is told along the way.

## What waits for you {#held}

A capability set to {{label:caps.modeConfirm}} holds the changes it covers. Reads covered by the same capability run straight away.

MESA's {{label:mesa.modeConfirm}} holds an action only while MESA is {{label:settings.mesaModeEnforced}}. One approval covers both, so you never approve the same action twice. Configuration recovery also asks in {{label:shell.tabApprovals}} before it puts something back.

Related: [Deny, Allow, and Confirm](guide:window.capabilities#modes)

## What the agent is told {#agent}

- External apps hear straight away that the request is waiting, and can ask for the outcome later. With an [inline approval wait](guide:window.keySettings#inline-wait), the app waits up to that long for your decision first.
- Agent Chat waits for your decision for up to 15 minutes. If no decision comes by then, or the chat ends first, the request is cancelled.
- The Voice Agent, Assist, and AI Task don't wait: they report that the change is waiting for approval and carry on. Approving later still runs it.
- Every agent also hears what the review page shows: the action, why it needs approval, and which kinds of detail the page lists. It never hears the details themselves, such as the names of automations that use the target.

## How long a request waits {#expiry}

A pending request expires after one hour. Once it has, {{label:workspace.review}} offers only **{{label:approvals.reject}}**. Rejecting it records it as expired.

Each access key can have up to 100 pending requests at once. One Home Assistant user can have up to 10 of them through the Voice Agent, Assist, or AI Task. Asking again for a request that's already waiting doesn't add another.

## What happens when you approve {#approve}

Phoenix checks the request again before running it:

- An expired request closes as expired.
- If the access key was revoked or has expired, the request is cancelled.
- If the key was rotated since an app's request, or the app sign-in that asked has ended, the request is cancelled.
- If its capability is now {{label:caps.modeDeny}}, the request fails without running.
- If the kill switch is on, the request is cancelled, unless it's a configuration recovery review.

Once those pass, Phoenix records your decision before it runs anything. Each action also checks its own targets, permissions, and MESA rules again. If the devices it targets have changed since the request, it's refused, and the agent must ask again.

If what you reviewed has changed since, Phoenix refuses the change the same way. This applies to edits of an automation, script, scene, blueprint, or dashboard, and to removing a blueprint. Someone might have edited it in Home Assistant, or another approval might have moved the cards. Nothing is overwritten, and the agent must ask again.

A request can reach more than it names, such as a raw command that works on a whole device. It lists the rest as `also_reaches`, and those entities count as targets.

## How it can end {#outcomes}

- Approved: it ran, and its result is saved.
- Failed: it was refused at the last check, or the action returned an error. It isn't offered again.
- Interrupted: it stopped while running, or Phoenix couldn't confirm the result, so it might have partly applied. Check the result yourself.

If Phoenix can't record your decision, or can't find the action to run, nothing runs and the request stays pending to approve again.

## Rejecting {#reject}

The agent receives your reason. If you gave none, Phoenix tells it to ask you. A rejected configuration recovery keeps the current configuration.

## What ends a request early {#cancelled}

Restarting Home Assistant cancels every pending request. Reloading Phoenix doesn't, and an approved action that's already running carries on. Revoking the access key, the key expiring, and deleting core data cancel its requests too.

Rotating the key cancels the requests apps made with it. Requests from the Voice Agent, Assist, and AI Task stay, since nobody used the key's value to make them.

Removing an app's sign-in, or turning off OAuth sign-in for the key, cancels that app's requests. This includes one the app is still waiting on. A sign-in that ends because the app signed out or a token was used twice does the same. Requests sent with the access key itself stay.

Approving while the kill switch is on cancels the request instead, except for a configuration recovery review.

If you reload or close the page, an approved action that's running carries on.

## Notifications {#notify}

With **{{label:settings.notifyApprovalLabel}}** on, each request raises a Home Assistant notification that links to it. The notification goes away once the request is decided.

Related: [Home Assistant notifications](guide:window.settingsLogs#notify-approval)

## History {#history}

Decided requests are kept for seven days, up to the newest 500. Their arguments, change, and result are cleared about an hour after the decision. Interrupted requests stay in the list so you can check what happened. Phoenix clears their arguments, change, and result after seven days too.
