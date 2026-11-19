---
{ "id": "window.approvals", "title": "{{label:shell.tabApprovals}}", "parent": "workspace.approvals", "order": 1, "keywords": ["requests", "queue", "pending"], "sectionKeywords": { "select-multiple": ["batch", "approve all", "bulk"], "show": ["history", "filter", "approved", "rejected", "expired"] }, "covers": ["frontend_src/views/ApprovalsView.tsx", "frontend_src/shell/approvals.tsx"] }
---
The requests agents made. Select one to see it in [{{label:workspace.review}}](guide:window.review). The list updates itself.

## {{label:approvals.show}} {#show}

Which requests to list: **{{label:approvals.tabPending}}** (default), **{{label:approvals.tabHistory}}** for everything decided, **{{label:approvals.scopeAll}}**, or a single outcome. Your choice is kept in this browser.

If new requests arrive while you're looking at history, opening the workspace again switches back to {{label:approvals.tabPending}}.

## Search {#search}

Searches every request that **{{label:approvals.show}}** covers, by access key, OAuth app, tool, title, or reason.

## The list {#list}

Each row shows the request and its status. More columns, such as the access key, tool, and times, are hidden. Show them from the column header's menu. Newest requests are first.

Use the arrow keys to move through the list. {{label:workspace.review}} shows the request you move to. A request someone is approving right now shows as {{label:approvals.processing}}.

## {{label:approvals.selectMultiple}} {#select-multiple}

Approves several pending requests at once. Select **{{label:approvals.selectMultiple}}** on the toolbar, tick the requests, then select the Approve button that counts them. It's available while at least two pending requests are listed.

Phoenix approves them one at a time, newest first, and stops at the first one that doesn't succeed. The rest stay pending and ticked. A batch holds at most 50 requests, so selecting all ticks the first 50. Expired requests can't be ticked. **{{label:approvals.cancelSelection}}** leaves without approving.

Use it only for requests you already know well. Each one still runs exactly as {{label:workspace.review}} shows it.

## {{label:common.loadMore}} {#load-more}

Loads the next 50 requests, or the next 10 on a phone.
