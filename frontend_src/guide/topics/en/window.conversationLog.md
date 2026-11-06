---
{ "id": "window.conversationLog", "title": "{{label:conversations.title}}", "parent": "workspace.logs", "order": 2, "keywords": ["conversations", "chat history", "transcript"], "sectionKeywords": { "recording": ["record conversations", "turn on", "off"], "list": ["prompt", "turns", "tool calls", "changes", "load more"], "filters": ["search", "source", "outcome", "date"], "updates": ["pause", "refresh"], "clear": ["delete history", "erase"] }, "covers": ["frontend_src/views/ConversationsView.tsx", "frontend_src/ui/table/ConfigTable.tsx", "custom_components/phoenix_mcp/conversation_log.py", "custom_components/phoenix_mcp/admin/logs.py"] }
---
What agents said and did, newest first. Agent Chat turns from one chat show as one conversation.

Related: [What the Logs Record](guide:topic.logRecords#conversation-sources)

## Turning recording on {#recording}

The log is empty until you turn on **{{label:conversations.enable}}** in [{{label:workspace.logsNotifications}}](guide:window.settingsLogs#conversation-log). Select **{{label:logs.logSettings}}** on the toolbar to open it here. Recording is off by default.

## The list {#list}

Each row shows the first thing asked, the outcome, where it came from, and how many turns, tool calls and changes it holds. Choose a row to read it in [{{label:workspace.logDetails}}](guide:window.logDetails).

A row that shows a tool's name instead of a prompt is a tool call from an MCP client, Assist or Home Companion. They don't pass on what the person said.

The list shows 25 conversations; select **{{label:common.loadMore}}** for more. Sorting by anything other than newest first sorts only the rows loaded so far.

## {{label:logs.filters}} {#filters}

Narrow the list by source, outcome and date. The search finds any recorded text: what was said, tool names and arguments, and results. A match anywhere in a conversation shows the whole conversation.

Hiding the filters stops them applying until you show them again.

## Live updates {#updates}

The list updates every 5 seconds while it's shown, and so does an open conversation in Log Details. **{{label:logs.pauseUpdates}}** holds it still; while paused, **{{label:common.refresh}}** loads the newest once.

## {{label:conversations.clear}} {#clear}

Deletes every recorded conversation, whatever the filters show. Confirm in place to go ahead. This can't be undone, and the audit log records that you did it.
