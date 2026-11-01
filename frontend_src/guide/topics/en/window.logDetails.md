---
{ "id": "window.logDetails", "title": "{{label:workspace.logDetails}}", "parent": "workspace.logs", "order": 3, "keywords": ["entry", "record", "payload", "transcript"], "sectionKeywords": { "step": ["previous", "next"], "audit-entry": ["payload", "request ID", "arguments"], "conversation": ["turns", "copy", "raw data", "technical details", "tool result"] }, "covers": ["frontend_src/shell/logDetails.tsx", "frontend_src/components/AuditEntry.tsx", "frontend_src/components/ConversationRecord.tsx"] }
---
The record you chose in the [{{label:shell.tabAudit}}](guide:window.auditLog) or the [{{label:conversations.title}}](guide:window.conversationLog), in full.

## Stepping through records {#step}

**{{label:workspace.previousRecord}}** and **{{label:workspace.nextRecord}}** move through the log in the order it showed when you chose the record. Entries that arrive later aren't included until you choose a record again.

## An audit entry {#audit-entry}

Every field of the entry, including its **{{label:audit.rowRequestId}}**. A tool call also shows its **{{label:audit.rowPayload}}**: the tool's name and arguments, with passwords, tokens and keys hidden. Long payloads are cut short.

If the access key was renamed since, its name then shows in brackets after the current one.

## A conversation {#conversation}

The heading sums up the conversation: where it came from, when, and how many turns, tool calls and changes it holds. Its turns follow, oldest first, 25 at a time.

Each turn shows what was asked, each tool call with its result, changes with their before and after, and the reply. Long text is folded. **{{label:conversations.copy}}** copies a block of text. **{{label:conversations.details}}** and **{{label:conversations.technical}}** show what was recorded exactly.
