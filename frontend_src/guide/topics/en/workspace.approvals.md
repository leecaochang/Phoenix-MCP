---
{ "id": "workspace.approvals", "title": "Approvals Workspace", "order": 20, "keywords": ["confirm", "pending", "queue", "review", "approve", "reject"] }
---
When a capability is set to Confirm, the change an agent asks for waits here for your decision. Changes from MESA Confirm and configuration recovery wait here too.

- [{{label:shell.tabApprovals}}](guide:window.approvals): the requests, pending or decided. The toolbar's Approvals button shows how many are waiting.
- [{{label:workspace.review}}](guide:window.review): what a request would change, with **{{label:approvals.approve}}** and **{{label:approvals.reject}}**.
- [{{label:workspace.requestDetails}}](guide:window.requestDetails): a request's key, capability, arguments and result.

A request that nobody decides expires after an hour.

Related: [How an approval works](guide:topic.approvalLifecycle), [Changes](guide:workspace.changes)
