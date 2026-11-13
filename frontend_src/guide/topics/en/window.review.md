---
{ "id": "window.review", "title": "{{label:workspace.review}}", "parent": "workspace.approvals", "order": 2, "keywords": ["diff", "compare", "decide"], "sectionKeywords": { "reject": ["reason", "deny request"], "change": ["before", "after", "preview", "code editor", "side by side", "used by", "relationships", "delete"] }, "covers": ["frontend_src/views/ApprovalsView.tsx", "frontend_src/components/ApprovalChange.tsx", "frontend_src/components/ActionPreview.tsx", "frontend_src/components/DiffView.tsx", "custom_components/phoenix_mcp/admin/approvals.py", "frontend_src/components/DashboardPreview.tsx", "frontend_src/utils/approval_reason.ts", "frontend_src/utils/approval_reason_draft.ts", "frontend_src/utils/approval_summary.ts", "frontend_src/utils/approval_text.ts", "frontend_src/utils.ts"] }
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
- Dashboards, cards and the Energy dashboard: **{{label:common.diff}}** or **{{label:common.preview}}**, which draws the result. Each request opens on **{{label:common.diff}}**. The preview runs the cards with your access, so choose it only for a change you trust.
- Actions on devices: the action, its targets, and what MESA allows, holds or blocks.
- Deletes, renames, helpers, integrations and other system actions: why the request needs your approval, what happens, what uses the item and which Phoenix settings name it. Unfold **{{label:approvals.previewAllDetails}}** at the bottom to see everything.
- Removing to-do items: each item with its status, and a warning that removal is permanent.

Templates in a dashboard change were already tried in a separate process, and a change whose template failed there never reaches you.

The other view choices are kept in this browser, and Changes uses them too.

## Stepping through requests {#step}

**{{label:workspace.previousRecord}}** and **{{label:workspace.nextRecord}}** move through the list without leaving Review.
