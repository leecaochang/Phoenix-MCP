---
{ "id": "workspace.approvals", "title": "Approvals" }
---
Approvals holds the changes agents asked to make through capabilities set to confirm. Nothing runs until you decide.

- **{{label:shell.tabApprovals}}** lists the requests. **{{label:approvals.show}}** chooses **{{label:approvals.tabPending}}**, **{{label:approvals.tabHistory}}** or one outcome. Choose a request to see it in **{{label:workspace.review}}**.
- **{{label:workspace.review}}** shows exactly what would change. **{{label:approvals.approve}}** runs the request. **{{label:approvals.reject}}** refuses it, and you can tell the agent why before you confirm.
- **{{label:workspace.requestDetails}}**, from the toolbar once a request is chosen, shows its access key, capability, inputs and recorded result, as the bottom half of Review's column.
- The list keeps itself up to date, so there is nothing to refresh. Restarting Home Assistant cancels every request still queued and says so in the list: nothing was waiting for the result any more.
- **{{label:approvals.selectMultiple}}** approves several requests you already know, in the order shown.

Configuration that agents change is recorded in [Changes](guide:workspace.changes), where an earlier version can be put back.
