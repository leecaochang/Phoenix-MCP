---
{ "id": "workspace.approvals", "title": "Approvals" }
---
Approvals holds the changes agents asked to make through capabilities set to confirm. Nothing runs until you decide.

- **{{label:shell.tabApprovals}}** lists the requests. **{{label:approvals.show}}** chooses **{{label:approvals.tabPending}}**, **{{label:approvals.tabHistory}}** or one outcome. Choose a request to see it in **{{label:workspace.review}}**.
- **{{label:workspace.review}}** shows exactly what would change. **{{label:approvals.approve}}** runs the request. **{{label:approvals.reject}}** refuses it, and you can tell the agent why before you confirm.
- **{{label:workspace.requestDetails}}**, from the toolbar, shows the request's access key, capability, inputs and recorded result.
- **{{label:approvals.selectMultiple}}** approves several requests you already know, in the order shown.

Configuration that agents change is recorded in [Changes](guide:workspace.changes), where an earlier version can be put back.
