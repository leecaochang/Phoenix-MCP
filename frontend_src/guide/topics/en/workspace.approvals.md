---
{ "id": "workspace.approvals", "title": "Approvals Workspace", "order": 20, "keywords": ["confirm", "pending", "queue", "review", "approve", "reject"] }
---
When a capability is set to {{label:caps.modeConfirm}}, the change an agent asks for waits in this workspace for your decision. Changes that MESA holds for confirmation, and configuration recovery reviews, wait in this workspace too.

- [{{label:shell.tabApprovals}}](guide:window.approvals): the requests, pending or decided. The toolbar's **{{label:shell.tabApprovals}}** button shows how many are waiting.
- [{{label:workspace.review}}](guide:window.review): what a request would change, with **{{label:approvals.approve}}** and **{{label:approvals.reject}}**.
- [{{label:workspace.requestDetails}}](guide:window.requestDetails): a request's key, capability, arguments, and result.

A request that nobody decides expires after an hour.

Related: [How an approval works](guide:topic.approvalLifecycle), [Changes](guide:workspace.changes)
