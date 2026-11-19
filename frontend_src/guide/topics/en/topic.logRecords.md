---
{ "id": "topic.logRecords", "title": "What the Logs Record", "parent": "workspace.logs", "order": 10, "keywords": ["audit", "history", "retention", "privacy", "redaction"], "sectionKeywords": { "audit-entries": ["tool call", "admin", "approval", "MESA"], "not-audited": ["missing", "401", "unauthorized"], "outcomes": ["allowed", "denied", "rate limited", "pending approval", "invalid request", "not found", "not implemented", "cancelled", "outcome unknown"], "audit-keeping": ["retention", "storage", "disk", "wipe"], "conversation-sources": ["agent chat", "voice", "AI Task", "Assist", "MCP client", "Home Companion"], "conversation-contents": ["tool result", "provider", "model", "secrets", "codes"], "conversation-keeping": ["retention", "storage", "limits"], "linked": ["request ID", "approval ID"] }, "covers": ["custom_components/phoenix_mcp/audit.py", "custom_components/phoenix_mcp/conversation_log.py", "custom_components/phoenix_mcp/mesa_audit.py", "custom_components/phoenix_mcp/const.py"] }
---
Phoenix keeps two logs. The audit log is a short record of actions and their outcomes. The Conversation Log, when it's on, keeps what agents said and did in full.

## What makes an audit entry {#audit-entries}

- **Agent actions**: each tool call from an MCP client, Agent Chat, the Voice Agent, Assist, AI Task, or Home Companion. The entry holds the tool's name and arguments, never its result.
- **Phoenix's AI models**: an action from Agent Chat, the Voice Agent, AI Task, Home Companion, or the decision provider also names the provider and model that asked. They show in [{{label:workspace.logDetails}}](guide:window.logDetails#audit-entry).
- **Other MCP requests**: listing tools, starting and ending a session, and requests a key wasn't allowed to make. If an app disconnects during a change, an entry named `request/cancelled` records it.
- **Evaluate actions**: each `ai_task/evaluate` call and refusal, under the Decisions access key. The entry holds a refusal's reason, never the questions or data.
- **MESA blocks**: an extra entry for each entity MESA refused. These need the `mesa_core.audit` logger at its default level, INFO. If you raise its level in Home Assistant's logger settings, these entries stop, but MESA still blocks.
- **OAuth sign-ins**: each token an app is given or refused. Any entry for a signed-in app's request also names the app, in [{{label:workspace.logDetails}}](guide:window.logDetails#audit-entry).
- **Approvals**: each request queued for approval, and what happened to it: approved, carried out, rejected, cancelled, or failed. A cancellation names its reason, such as a rotated key or a removed sign-in.
- **Your changes in the panel**: access keys, permissions, settings, MESA profiles, restores, wipes, and other administrator actions. These show the administrator instead of an access key. A settings change names what changed, even one that turns logging off.

## What isn't recorded {#not-audited}

Phoenix doesn't record:

- Requests without a valid access key
- Requests while Phoenix is starting or stopped by the kill switch
- Chat or voice turns that call no tool
- Most panel reads

Turn on the [Conversation Log](guide:window.settingsLogs#conversation-log) to see the conversations themselves.

## Outcomes {#outcomes}

- {{label:audit.outcomeAllowed}}: carried out.
- {{label:audit.outcomeDenied}}: refused by permissions or MESA, or an approval that was rejected, expired, or cancelled.
- {{label:audit.outcomePendingApproval}}: waiting for your approval in [{{label:shell.tabApprovals}}](guide:workspace.approvals).
- {{label:audit.outcomeRateLimited}}: the key sent too many requests. If that's expected, raise its limit in [{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting).
- {{label:audit.outcomeInvalidRequest}}, {{label:audit.outcomeNotFound}}, and {{label:audit.outcomeNotImplemented}}: the request was malformed, named something that doesn't exist, or asked for something Phoenix doesn't do. A change Phoenix couldn't confirm is also {{label:audit.outcomeInvalidRequest}}, even though it might have landed. Check it in Home Assistant.
- {{label:audit.outcomeCancelled}} and {{label:audit.outcomeUnknown}}: the app left, or lost its permission, before the request finished. {{label:audit.outcomeCancelled}} means nothing reached your home. {{label:audit.outcomeUnknown}} means the change might have run, so check it in Home Assistant.

Which outcomes are recorded is up to the [audit log settings](guide:window.settingsLogs#audit-log).

## How long audit entries are kept {#audit-keeping}

The audit log keeps the newest 10,000 entries by default. It saves them to disk every 15 minutes and when Home Assistant stops. Change both in [{{label:workspace.logsNotifications}}](guide:window.settingsLogs#log-max-entries).

Approval decisions and results, and your changes in the panel, are kept apart, up to the same number. Requests from agents never push them out. A request waiting for approval, and its expiry, stay with the agent's other requests.

A key's malformed, unsupported, and rate-limited requests count toward the limit, but only its first 30 in a minute get their own entry. The rest of that minute share one entry, which counts them by outcome. Permission refusals, missing names, and writes that might have run always get their own entry.

Nothing in the panel clears the audit log on its own. A Phoenix core data wipe does. Removing Phoenix keeps it.

## What the Conversation Log records {#conversation-sources}

- **Agent Chat, the Voice Agent, and AI Task**: each turn, with your words, the reply, and every tool call.
- **MCP clients, Assist, and Home Companion**: each tool call and its result. Phoenix doesn't see what the person typed, so there's no prompt.

Work done inside a turn, such as an approved action carried out later, joins that turn. Agent Chat turns in one chat are grouped together.

## What a conversation holds {#conversation-contents}

Each tool call and its full result, changes with their before and after, approvals, errors, and the route a request took. Entries from Agent Chat, the Voice Agent, and AI Task also name the AI provider and model.

Phoenix hides passwords, tokens, keys, and alarm or lock codes before it saves anything. Images and files are noted, not stored. Phoenix doesn't record the instructions it gives the AI. Token usage goes to the routing statistics instead.

## How long conversations are kept {#conversation-keeping}

The newest 100 conversations, for up to 7 days and 25 MB, by default. Change the limits in [{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log). An entry that's too large keeps its outcome but drops some detail.

Clear the log with **{{label:conversations.clear}}** in the Conversation Log window. A core data wipe clears it too.

## Finding the same request in both logs {#linked}

A tool call's request ID is the same in both logs, and so is an approval's ID. In the audit log, filter by **{{label:audit.rowRequestId}}** or **{{label:audit.rowResource}}**. In the Conversation Log, search for it.

An approval that was carried out or cancelled is logged under the request ID that asked for it. Approval entries also hold the approval's ID, even when entity names are hidden. Entries for approvals that Phoenix closes when it starts are the exception.
