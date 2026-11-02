---
{ "id": "window.auditLog", "title": "{{label:shell.tabAudit}}", "parent": "workspace.logs", "order": 1, "keywords": ["audit", "requests", "history"], "sectionKeywords": { "list": ["columns", "sort", "load more", "MESA advisory", "source"], "filters": ["outcome", "access key", "time", "method", "resource", "request ID", "search"], "updates": ["pause", "refresh", "live"], "attention": ["needs attention", "show in audit log"] }, "covers": ["frontend_src/views/AuditView.tsx", "frontend_src/components/AuditEntry.tsx", "frontend_src/utils/audit_source.ts", "frontend_src/ui/table/ConfigTable.tsx", "custom_components/phoenix_mcp/admin_view.py"] }
---
Each action agents and administrators took through Phoenix, newest first.

Related: [What the Logs Record](guide:topic.logRecords)

## The list {#list}

Choose an entry to read it in [{{label:workspace.logDetails}}](guide:window.logDetails). Up and Down Arrow move through the list.

- **{{label:audit.rowAccess}}** says whether the key used its permissions or [pass-through](guide:topic.passThrough).
- **{{label:audit.rowMesa}}** shows {{label:mesa.modeAdvisory}} when MESA warned the agent but let the action run.
- **{{label:audit.rowIp}}** is the caller's IP address, or the Phoenix feature that made the call, such as Agent Chat.
- **{{label:audit.colAccessKey}}** shows the key's current name, even if it was renamed since.

The log loads 100 entries at a time; select **{{label:common.loadMore}}** for more. Sorting by anything other than newest first sorts only the entries loaded so far.

Sort, move and hide columns from the column header's menu, and drag a header's edge to resize one.

## {{label:logs.filters}} {#filters}

Narrow the list by **{{label:audit.rowOutcome}}**, **{{label:audit.colAccessKey}}** and time, and by text in **{{label:audit.rowMethod}}**, **{{label:audit.rowResource}}** and **{{label:audit.rowIp}}**. **{{label:audit.rowRequestId}}** finds one exact request, such as one from the Conversation Log. A time window such as the past 5 minutes moves on as the log updates.

Hiding the filters stops them applying until you show them again. When both logs are open, one **{{label:logs.filters}}** button shows the filters of both.

## Live updates {#updates}

New entries appear every 5 seconds while the window is shown and unfolded. **{{label:logs.pauseUpdates}}** holds the list still while you read it. While paused, **{{label:common.refresh}}** loads the newest entries once.

## From Needs Attention {#attention}

**{{label:attention.showInAudit}}** in the Needs Attention window opens this log filtered to that one request.
