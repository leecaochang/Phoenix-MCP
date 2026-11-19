---
{ "id": "window.requestDetails", "title": "{{label:workspace.requestDetails}}", "parent": "workspace.approvals", "order": 3, "keywords": ["arguments", "raw", "result", "error"], "covers": ["frontend_src/views/ApprovalsView.tsx", "custom_components/phoenix_mcp/approvals.py"] }
---
The technical record of the request selected in {{label:workspace.review}}. It opens with the workspace, under {{label:workspace.review}}. If you close it, reopen it from **{{label:workspace.options}}**.

Each part folds. It stays folded or open for the next request you select.

## {{label:approvals.colRequest}} {#request}

The access key, tool, capability, and status of the request, and when it was made and expires or was decided. If an app signed in with OAuth sent it, the app shows too. A decided request also shows the reason it ended.

## {{label:approvals.detailTabArgs}} {#raw-args}

The arguments the agent sent. Phoenix hides values that look like secrets, and its own internal arguments. Folded by default.

## {{label:approvals.detailTabResult}} {#result}

For a decided request, what the action returned, including any error.

## After an hour {#pruned}

An hour after a request is decided, Phoenix clears its arguments, change, and result. A request whose action was interrupted keeps them for seven days. The request stays in history with its outcome, but {{label:workspace.review}} and these sections have nothing left to show.
