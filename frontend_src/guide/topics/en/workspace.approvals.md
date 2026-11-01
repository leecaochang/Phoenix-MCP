---
{ "id": "workspace.approvals", "title": "Approvals", "order": 20 }
---
When a capability is set to confirm, the agent's change waits here for your decision. Nothing runs until you decide.

- **{{label:shell.tabApprovals}}** lists the requests. Use **{{label:approvals.show}}** to see **{{label:approvals.tabPending}}**, **{{label:approvals.tabHistory}}** or one outcome. Choose a request to see it in **{{label:workspace.review}}**.
- **{{label:workspace.review}}** shows exactly what would change. **{{label:approvals.approve}}** runs the request. **{{label:approvals.reject}}** refuses it, and you can tell the agent why before you confirm.
- **{{label:workspace.requestDetails}}**, on the toolbar once you choose a request, shows its access key, capability, inputs and recorded result, under Review.
- The list updates itself, so there's nothing to refresh. Restarting Home Assistant cancels any requests still waiting, and marks them in the list, since nothing is waiting for their results any more.
- **{{label:approvals.selectMultiple}}** approves several requests at once, in the order shown. Use it for requests you already know.

Configuration that agents change is recorded in [Changes](guide:workspace.changes), where you can put back an earlier version.
