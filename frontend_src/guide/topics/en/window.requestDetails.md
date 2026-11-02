---
{ "id": "window.requestDetails", "title": "{{label:workspace.requestDetails}}", "parent": "workspace.approvals", "order": 3, "keywords": ["arguments", "raw", "result", "error"], "covers": ["frontend_src/views/ApprovalsView.tsx", "custom_components/phoenix_mcp/approvals.py"] }
---
The technical record of the request chosen in Review. It opens with the workspace, under Review; if you close it, reopen it from **{{label:workspace.options}}**.

Each part folds, and stays folded or open for the next request you choose.

## {{label:approvals.colRequest}} {#request}

The access key, tool, capability, when the request was made and expires or was decided, and its status. A decided request also shows the reason it ended.

## {{label:approvals.detailTabArgs}} {#raw-args}

The arguments the agent sent. Values that look like secrets are hidden, and so are Phoenix's own internal arguments. Folded by default.

## {{label:approvals.detailTabResult}} {#result}

What the action returned, for a decided request, including any error.

## After an hour {#pruned}

An hour after a request is decided, Phoenix clears its arguments, change and result. The request stays in history with its outcome, but Review and these sections have nothing left to show.
