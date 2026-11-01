---
{ "id": "topic.logRecords", "title": "What the Logs Record", "parent": "workspace.logs", "order": 10, "keywords": ["audit", "history", "retention", "privacy", "redaction"], "sectionKeywords": { "audit-entries": ["tool call", "admin", "approval", "MESA"], "not-audited": ["missing", "401", "unauthorized"], "outcomes": ["allowed", "denied", "rate limited", "pending approval", "invalid request", "not found", "not implemented"], "audit-keeping": ["retention", "storage", "disk", "wipe"], "conversation-sources": ["agent chat", "voice", "AI Task", "Assist", "MCP client", "Home Companion"], "conversation-contents": ["tool result", "provider", "model", "secrets", "codes"], "conversation-keeping": ["retention", "storage", "limits"], "linked": ["request ID", "approval ID"] }, "covers": ["custom_components/phoenix_mcp/audit.py", "custom_components/phoenix_mcp/conversation_log.py", "custom_components/phoenix_mcp/mesa_audit.py", "custom_components/phoenix_mcp/const.py"] }
---
Phoenix keeps two logs. The audit log is a short record of every action and its outcome. The Conversation Log, when it's on, keeps what agents said and did in full.

## What makes an audit entry {#audit-entries}

- **Agent actions**: each tool call from an MCP client, Agent Chat, the voice agent, Assist, AI Task or Home Companion. The entry holds the tool's name and arguments, never its result.
- **Other MCP requests**: listing tools, starting and ending a session, and requests a key wasn't allowed to make.
- **MESA blocks**: an extra entry for each action MESA refused.
- **Approvals**: each request queued for approval, and what happened to it: approved, carried out, rejected, cancelled or failed.
- **Your changes in the panel**: access keys, permissions, settings, MESA profiles, restores, wipes and other administrator actions. These show the administrator instead of an access key.

## What isn't recorded {#not-audited}

Requests without a valid access key, requests while Phoenix is starting or stopped by the kill switch, and chat or voice turns that call no tool. Most panel reads aren't recorded either.

Turn on the [Conversation Log](guide:window.settingsLogs#conversation-log) to see the conversations themselves.

## Outcomes {#outcomes}

- **{{label:audit.outcomeAllowed}}**: carried out.
- **{{label:audit.outcomeDenied}}**: refused by permissions or MESA, or an approval that was rejected, expired or cancelled.
- **{{label:audit.outcomePendingApproval}}**: waiting for your approval.
- **{{label:audit.outcomeRateLimited}}**: the key sent too many requests.
- **{{label:audit.outcomeInvalidRequest}}**, **{{label:audit.outcomeNotFound}}** and **{{label:audit.outcomeNotImplemented}}**: the request was malformed, named something that doesn't exist, or asked for something Phoenix doesn't do.

Which outcomes are recorded is up to the [audit log settings](guide:window.settingsLogs#audit-log).

## How long audit entries are kept {#audit-keeping}

The audit log keeps the newest 10,000 entries by default, and saves them to disk every 15 minutes and when Home Assistant stops. Change both in [{{label:workspace.logsNotifications}}](guide:window.settingsLogs#log-max-entries).

Nothing in the panel clears the audit log on its own. A Phoenix core data wipe does. Removing Phoenix keeps it.

## What the Conversation Log records {#conversation-sources}

- **Agent Chat, the voice agent and AI Task**: each turn, with your words, the reply, and every tool call.
- **MCP clients, Assist and Home Companion**: each tool call and its result. Phoenix doesn't see what the person typed, so there's no prompt.

Work done inside a turn, such as an approved action carried out later, joins that turn. Agent Chat turns in one chat are grouped together.

## What a conversation holds {#conversation-contents}

Each tool call and its full result, changes with their before and after, approvals, errors, and the route a request took. Entries from Agent Chat, voice and AI Task also name the AI provider and model.

Passwords, tokens, keys and alarm or lock codes are hidden before anything is saved. Images and files are noted, not stored. The instructions Phoenix gives the AI aren't recorded, and token usage goes to the routing statistics instead.

## How long conversations are kept {#conversation-keeping}

The newest 100 conversations, for up to 7 days and 25 MB, by default. Change the limits in [{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log). An entry that's too large keeps its outcome but drops some detail.

Clear the log with **{{label:conversations.clear}}** in the Conversation Log window. A core data wipe clears it too.

## Finding the same request in both logs {#linked}

A tool call's request ID is the same in both logs, and so is an approval's ID. In the audit log, filter by **{{label:audit.rowRequestId}}** or **{{label:audit.rowResource}}**; in the Conversation Log, search for it.
