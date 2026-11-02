---
{ "id": "window.review", "title": "{{label:workspace.review}}", "parent": "workspace.approvals", "order": 2, "keywords": ["diff", "compare", "decide"], "sectionKeywords": { "reject": ["reason", "deny request"], "change": ["before", "after", "preview", "code editor", "side by side"] }, "covers": ["frontend_src/views/ApprovalsView.tsx", "frontend_src/components/ApprovalChange.tsx", "frontend_src/components/DiffView.tsx", "custom_components/phoenix_mcp/admin_view.py", "frontend_src/components/DashboardPreview.tsx", "frontend_src/utils/approval_reason.ts", "frontend_src/utils/approval_reason_draft.ts", "frontend_src/utils/approval_summary.ts", "frontend_src/utils/approval_text.ts", "frontend_src/utils.ts"] }
---
Shows exactly what the chosen request would change, and lets you decide. A decided request stays here, with its outcome.

## Time left {#context}

The line under the title shows the request's status, its access key, and for a pending request the time left before it expires. An expired request can't be approved, so only **{{label:approvals.reject}}** is offered, and it closes the request as expired.

## {{label:approvals.approve}} {#approve}

Runs the request, straight away and without asking again. Phoenix first checks it's still allowed: the key must still be active, its capability not set to Deny, and the kill switch off. Otherwise the request closes without running.

If the action fails, the request ends as failed and isn't offered again. Ask the agent to try again if you still want the change.

Related: [What happens when you approve](guide:topic.approvalLifecycle#approve)

## {{label:approvals.reject}} {#reject}

Refuses the request. **{{label:approvals.reject}}** opens a box for an optional reason, which the agent receives so it can adjust its next proposal. Press Enter or select **{{label:approvals.confirmReject}}** to reject, or **{{label:approvals.cancelReject}}** to back out.

The reason you're typing is shared with Agent Chat's Reject button.

## The change {#change}

How the change is shown depends on what the request does:

- Configuration and YAML: **{{label:common.before}}** and **{{label:common.after}}** side by side. Switch between **{{label:changes.lineViewShort}}** and **{{label:changes.codeEditorShort}}**, or stack the two sides.
- Dashboards, cards and the Energy dashboard: **{{label:common.diff}}** or **{{label:common.preview}}**, which draws the result.
- Actions on devices: the action, its targets, and what MESA allows, holds or blocks.

These view choices are kept in this browser, and Changes uses them too.

## Stepping through requests {#step}

**{{label:workspace.previousRecord}}** and **{{label:workspace.nextRecord}}** move through the list without leaving Review.
