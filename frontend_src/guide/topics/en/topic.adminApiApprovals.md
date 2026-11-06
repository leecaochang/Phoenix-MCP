---
{ "id": "topic.adminApiApprovals", "title": "Approval and Change Endpoints", "parent": "topic.adminApi", "order": 20, "keywords": ["approvals API", "approve", "reject", "cancel", "versions", "rollback", "undo", "recovery", "Needs Attention", "card catalog"], "sectionKeywords": { "approvals": ["pending", "approval_id", "PendingApproval"], "list-approvals": ["in_progress", "status filter", "access_key_id"], "approve": ["approvalExecutionInterrupted", "approvalProcessing", "killSwitchEngaged", "approvalCapabilityDenied", "approvalAccessKeyInactive", "execution_failed", "refused_before_write"], "batch-approve": ["approval_ids", "batch", "applied", "remaining", "approve all"], "reject": ["reason", "closure_reason"], "cancel": ["DELETE", "admin_cancelled"], "claims": ["phoenix_mcp_approval_claimed", "phoenix_mcp_approval_resolved", "claim", "event"], "history": ["versions", "resource_type", "resource_id", "truncated"], "read-version": ["restore_refusals", "yaml_tags", "audit_only", "template_incompatible", "entity_missing"], "restore-version": ["expected_latest_id", "stale_version", "restore_unverified", "rollback", "side"], "attention": ["attention", "unknown outcome", "checked_outcome", "calendar"], "recovery": ["config-recovery", "safety copy", "snapshot", "phase"], "recovery-action": ["inspect", "reviewed", "delete", "recoveryBlocked"], "recovery-restore": ["isError", "recoveryManualOnly", "zigbee"], "card-catalog": ["custom cards", "harvest", "resource_fingerprint", "stub_config"] }, "covers": ["custom_components/phoenix_mcp/admin/approvals.py", "custom_components/phoenix_mcp/approvals.py", "custom_components/phoenix_mcp/config_recovery.py"] }
---
These endpoints handle what agents ask you to approve, and the changes they make: approvals, configuration history, recovery records, Needs Attention and the dashboard card catalog. Signing in, the error body and request bodies work as described in [Admin API](guide:topic.adminApi).

Related: [Errors](guide:topic.adminApi#errors), [Request bodies](guide:topic.adminApi#bodies), [How an Approval Works](guide:topic.approvalLifecycle), [What Changes Records](guide:topic.configurationHistory)

## Approvals {#approvals}

```
GET    /api/phoenix-mcp/admin/approvals
GET    /api/phoenix-mcp/admin/approvals/{approval_id}
DELETE /api/phoenix-mcp/admin/approvals/{approval_id}
POST   /api/phoenix-mcp/admin/approvals/{approval_id}/approve
POST   /api/phoenix-mcp/admin/approvals/batch/approve
POST   /api/phoenix-mcp/admin/approvals/{approval_id}/reject
```

Use these to review, approve, reject or cancel the actions agents are waiting on. The endpoints for one approval answer with this approval record:

| Field | Notes |
|---|---|
| `id` | The approval ID. |
| `access_key_id`, `access_key_name` | The access key that asked. |
| `tool_name`, `cap_name` | The tool held for approval and the capability it needs. |
| `args` | The tool's arguments. Secrets, device codes and every key starting with `_` show as `<redacted>`. |
| `diff` | The proposed change. |
| `status` | `pending`, `approved`, `rejected`, `failed`, `expired` or `cancelled`. |
| `created_at`, `expires_at`, `resolved_at` | When it was asked, when it expires and when it ended. |
| `approved_by_user_id`, `approved_at` | Who approved it, and when. |
| `closure_reason` | Why it ended without running, or why its run failed. Values below. |
| `result` | The tool's result after an approve. |
| `request_id`, `client_ip` | The agent request that asked. |
| `household_request`, `ha_user_id` | The household request flag, and the Home Assistant user behind the request. |
| `execution_started_at` | When an approve started running the action. |

`closure_reason` is your own reason for a reject, or one of these values:

```text
access_key_inactive        capability_denied
kill_switch                admin_cancelled
access_key_revoked         access_key_expired
phoenix_mcp_data_wiped     execution_interrupted
execution_failed           agent_chat_ended
agent_chat_wait_timed_out  integration_removed
recovery_review_replaced   phoenix_mcp_restarted
```

Related: [Approvals](guide:window.approvals), [How it can end](guide:topic.approvalLifecycle#outcomes)

### List approvals {#list-approvals}

Lists approvals, newest first.

| Field | Type | Notes |
|---|---|---|
| `status` | query, string | Only approvals with exactly this status. An unknown value returns an empty list. |
| `access_key_id` | query, string | Only approvals from this access key. |
| `limit` | query, integer | Default 50, at most 500. A larger value counts as 500. |
| `offset` | query, integer | Default 0. |

The response is `{ approvals, total, limit, offset }`. Each item in `approvals` is the record plus `in_progress`, which is `true` while an approve is running it. Treat such a row as not actionable, even when its status is still `pending`.

Errors: the paging errors in [Request bodies](guide:topic.adminApi#bodies).

### Read one approval {#read-approval}

Returns the approval record, without `in_progress`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.approvalNotFound` | No approval has this ID. |

### Approve {#approve}

Approves the approval and runs the held action as the agent asked it. The endpoint takes no body and ignores any you send.

Phoenix checks the approval in the order of the error table below. Before running anything, it saves an execution marker to disk. If a run reaches the action and never reports back, that marker stays, and every later approve, reject or cancel answers `409`.

Approvals that MESA held for confirmation, and recovery reviews, skip the capability check. Recovery reviews skip the kill switch check too.

The response is `200` with the updated record:

- `status: "approved"` when the action ran.
- `status: "failed"` with `closure_reason: "execution_failed"` when the tool returned an error. This is still a `200`.
- `status: "failed"` with `closure_reason: "execution_interrupted"` when the tool sent the change but couldn't confirm it landed. Check the result before you retry. This is still a `200`.
- `result` is `{ tool_result, outcome }`. It adds `refused_before_write: true` when the tool refused before writing anything.
- An approval that already ended comes back unchanged.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 409 | `conflict` | `adminError.approvalWipeInProgress` | Phoenix data is being deleted. |
| 404 | `not_found` | `adminError.approvalNotFound` | No approval has this ID, or it disappeared while running. |
| 409 | `conflict` | `adminError.approvalProcessing` | Another approve is running it now. |
| 409 | `conflict` | `adminError.approvalExecutionInterrupted` | An earlier approve reached the action and never reported back. Check the result before doing anything else. |
| 409 | `conflict` | `adminError.approvalExpired` | It was past `expires_at`, so this call closed it as `expired`. Nothing ran. |
| 409 | `conflict` | `adminError.approvalAlreadyResolved` | Another request finished it first. |
| 404 | `not_found` | `adminError.approvalAccessKeyInactive` | The access key is no longer active. Phoenix closes the approval as `cancelled`, reason `access_key_inactive`. |
| 403 | `forbidden` | `adminError.approvalCapabilityDenied` | The capability is now set to Deny for this key. Phoenix closes the approval as `failed`, reason `capability_denied`. |
| 503 | `service_unavailable` | `adminError.killSwitchEngaged` | The kill switch is on. Phoenix closes the approval as `cancelled`, reason `kill_switch`. |
| 503 | `service_unavailable` | `adminError.approvalRecordFailed` | Phoenix couldn't save the execution marker. Nothing ran and the approval is still pending. |
| 400 | `invalid_request` | `adminError.approvalExecutorMissing` | Phoenix has no way to run this tool. Nothing ran. |
| 500 | `internal_error` | `adminError.approvalExecutionUncertain` | The run raised an error. Phoenix closes the approval as `failed`, reason `execution_interrupted`, and won't offer it again. |

Three of those errors sound temporary but close the approval for good: `approvalAccessKeyInactive`, `approvalCapabilityDenied` and `killSwitchEngaged`. Fixing the condition afterwards doesn't bring it back.

### Approve several {#batch-approve}

Approves several approvals in order, one at a time, through the same steps as a single approve. The batch stops at the first approval that doesn't come back as `200` with `status: "approved"`.

| Field | Type | Notes |
|---|---|---|
| `approval_ids` | array of strings | Required. 1 to 50 IDs, each a non-empty string, with no repeats. |

The response is always `200`:

```json
{
  "applied": [{ "approval_id": "...", "tool_name": "..." }],
  "failed": {
    "approval_id": "...",
    "tool_name": "...",
    "status": 409,
    "error": "conflict",
    "message": "...",
    "message_key": "adminError.approvalProcessing"
  },
  "remaining": ["..."]
}
```

- `applied` lists the approvals that ran. An approval that was already approved counts as applied.
- `failed` is `null` when every approval ran. Its `status` is that item's HTTP status. Its `error` is the item's error code, else its `closure_reason`, else `"failed"`.
- `failed` carries `message_key` and `message_params` when the item's error had them.
- `remaining` lists the IDs the batch didn't try.

The batch adds one audit log entry, not one per approval.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.approvalIdsNonEmpty` | `approval_ids` is missing, empty, or holds something other than non-empty strings. |
| 400 | `invalid_request` | `adminError.approvalIdsUnique` | An ID repeats. |
| 400 | `invalid_request` | `adminError.approvalBatchMaximum` | More than 50 IDs. `message_params.max` is 50. |

### Reject {#reject}

Rejects a pending approval, so the action never runs.

| Field | Type | Notes |
|---|---|---|
| `reason` | string | Optional. Stored in `closure_reason`. |

The response is the updated record with `status: "rejected"`. An approval that already ended comes back unchanged with `200`. A pending approval past `expires_at` closes as `expired` instead.

Rejecting a recovery review also records that you checked it. If the recovery record refuses that, you get `409` `conflict` with one of the keys in [Act on a record](guide:#recovery-action).

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.approvalReasonString` | `reason` isn't a string. |
| 404 | `not_found` | `adminError.approvalNotFound` | No approval has this ID. |
| 409 | `conflict` | `adminError.approvalProcessing` | An approve is running it now. |
| 409 | `conflict` | `adminError.approvalExecutionInterrupted` | An earlier approve reached the action and never reported back. Check the result first. |

### Cancel {#cancel}

Cancels a pending approval. It works like reject, with `status: "cancelled"` and `closure_reason: "admin_cancelled"`. Send no body.

The response, and the handling of ended and overdue approvals, match [Reject](guide:#reject). The errors are the same except `approvalReasonString`.

### Claims and events {#claims}

Phoenix fires two events on Home Assistant's event bus, so a script can follow approvals without polling:

- `phoenix_mcp_approval_claimed` fires with `claimed: true` when an approve takes the approval, before the action runs. Data: `approval_id`, `claimed`, `timestamp`.
- It fires again with `claimed: false` if a failure before the run leaves the approval pending.
- `phoenix_mcp_approval_resolved` fires whenever an approval reaches a final status. Data: `approval_id`, `access_key_id`, `access_key_name`, `tool_name`, `status`, `closure_reason`, `approved_by_user_id`, `timestamp`.

A page that loads while a run is under way sees `in_progress: true` on the row in [List approvals](guide:#list-approvals).

Related: [Events for automations](guide:topic.running#events)

## Configuration history {#history}

```
GET    /api/phoenix-mcp/admin/versions
GET    /api/phoenix-mcp/admin/versions/{version_id}
POST   /api/phoenix-mcp/admin/versions/{version_id}/restore
```

Use these to read the changes agents and restores made, and to put one side of a change back.

Phoenix records versions of these resource types:

```text
automation  script  scene      helper     dashboard  entity  device
blueprint   energy  config_entry  area    floor      label   category
```

Each version has an action: `create`, `edit`, `delete` or `rollback`.

Phoenix keeps the newest 20 versions of each resource. Raw text over 100,000 bytes is stored as `{ "content": null, "truncated": true, "bytes": <size> }`, and you can't restore it.

Related: [Changes](guide:window.changes), [What Changes Records](guide:topic.configurationHistory)

### List versions {#list-versions}

Lists recent changes across every resource, or every version of one resource.

| Field | Type | Notes |
|---|---|---|
| `resource_type` | query, string | With `resource_id`, lists every stored version of that resource, newest first, without paging. |
| `resource_id` | query, string | Send with `resource_type`, or leave both out for the recent feed. |
| `limit` | query, integer | Recent feed only. Default 50, at most 500. A larger value counts as 500. |
| `offset` | query, integer | Recent feed only. Default 0. |

The response is `{ resource_type, resource_id, versions, total }`. Each item in `versions` is a summary:

| Field | Notes |
|---|---|
| `id` | The version ID. |
| `resource_type`, `resource_id`, `alias` | What changed, and its name. |
| `action` | `create`, `edit`, `delete` or `rollback`. |
| `access_key_id`, `access_key_name` | The access key that made the change. |
| `approved_by_user_id` | The administrator who approved it, or who ran a restore. |
| `timestamp` | When it changed. |
| `summary`, `summary_key`, `summary_params` | A short description, and its translation key and values. |
| `has_before`, `has_after` | Whether the version holds a before or after side. |

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.versionResourcePairRequired` | Only one of `resource_type` and `resource_id` was sent. |

The paging errors in [Request bodies](guide:topic.adminApi#bodies) also apply.

### Read one version {#read-version}

Returns one version in full: the summary fields without `has_before` and `has_after`, plus `before`, `after` and `request_id`.

It adds `restore_refusals: { before, after }`. Each side is `null`, or a reason Phoenix would refuse to restore that side: `yaml_tags`, `audit_only`, `template_incompatible` or `entity_missing`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.versionNotFound` | No version has this ID. |

### Restore a version {#restore-version}

Applies one side of a version to the live configuration. An empty body is fine.

| Field | Type | Notes |
|---|---|---|
| `side` | string | Optional. `"before"` or `"after"`. Default `"after"`, or `"before"` when the version has no after side. |
| `expected_latest_id` | string or null | Optional. When set, the restore applies nothing unless this is the ID of the resource's newest version. |

Send `expected_latest_id` to make sure no other change landed since you last read the history. If one did, you get `stale_version`; read the history again and decide.

The restore runs with your authority as administrator. Phoenix records it as a `rollback` version, with you in `approved_by_user_id`. The audit log entry keeps the access key that made the original change, as `originating_access_key_id`.

The response is `200` with `{ "restored": true, version_id, resource_type, resource_id }`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.versionNotFound` | No version has this ID. |
| 400 | `invalid_request` | `adminError.restoreSideInvalid` | `side` is something other than `"before"` or `"after"`. |
| 400 | `invalid_request` | `adminError.stringOrNull` | `expected_latest_id` isn't a string or null. `message_params.field` is `expected_latest_id`. |
| 409 | `stale_version` | `adminError.restoreStale` | `expected_latest_id` isn't the newest version any more. Nothing was applied. |
| 500 | `internal_error` | `adminError.restoreFailed` | The restore raised an error. |
| 409 | `restore_unverified` | `adminError.restoreUnverified` | Phoenix sent the restore, but reading the resource back doesn't match. It may be partly applied, so check before retrying. |
| 400 | `invalid_request` | `adminError.restoreNoConfigSide` | That side holds no configuration to restore. |
| 400 | `invalid_request` | `adminError.restoreYamlTags` | The version holds YAML tags such as `!secret` or `!include`. Re-create it by hand. |
| 400 | `invalid_request` | a recovery key | A recovery record refuses the restore. The keys are listed in [Act on a record](guide:#recovery-action). |
| 400 | `invalid_request` | `adminError.restoreToolReported` | The restore tool refused for another reason. `message_params.detail` holds its own English text. |
| 400 | `invalid_request` | `adminError.restoreNotApplicable` | The restore couldn't be applied to the current configuration. |

## Needs Attention {#attention}

```
GET    /api/phoenix-mcp/admin/attention
POST   /api/phoenix-mcp/admin/attention/{kind}/{record_id}
```

Use these to find agent and calendar actions whose outcome Phoenix doesn't know, and to mark them checked once you have looked.

Related: [Needs Attention](guide:window.attention)

### List items {#attention-items}

The response is `{ items }`. Each item is one of these:

- An agent action whose outcome is unknown: `{ id, "kind": "agent", tool, access_key_name, started_at, dispatch_result }`.
- A calendar write whose outcome is unknown: `{ id, "kind": "calendar", tool, access_key_name, started_at, entity_id, "dispatch_result": "unknown" }`. Its `tool` is `<operation>_calendar_event`.
- `{ "id": "calendar_storage", "kind": "calendar_storage" }` when Phoenix can't read its calendar records.
- `{ "id": "audit_storage", "kind": "audit_storage" }` when Phoenix's last audit log save failed.
- `{ "id": "mesa", "kind": "mesa" }` when MESA failed to start.

When a request wrote to a calendar, its calendar item replaces the agent item for the same request.

### Mark an action checked {#attention-review}

Records that you checked the outcome of one agent or calendar action. Use the item's `kind` and `id` in the path. Phoenix saves your review only; it doesn't run the action again or undo it.

| Field | Type | Notes |
|---|---|---|
| `checked_outcome` | boolean | Required. Must be exactly `true`. |

Phoenix reads your user from Home Assistant again, and you must still be an active administrator. The response is `{ "reviewed": true }`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.attentionCheckRequired` | `checked_outcome` isn't `true`. |
| 403 | `forbidden` | `adminError.adminRequired` | You're no longer an active administrator. |
| 404 | `not_found` | `adminError.attentionNotReviewable` | `kind` is something other than `agent` or `calendar`. |
| 503 | `service_unavailable` | `adminError.attentionSaveFailed` | Phoenix couldn't save the review. The action still needs review. |
| 409 | `conflict` | `adminError.attentionReviewConflict` | The action is still running, or someone already reviewed it. Reload the list. |

## Configuration recovery {#recovery}

```
GET    /api/phoenix-mcp/admin/config-recovery
POST   /api/phoenix-mcp/admin/config-recovery/{recovery_id}
```

Use these to read the safety copies Phoenix takes before configuration writes, and to check, clear or restore them.

Related: [Safety copies](guide:topic.configurationHistory#recovery)

### List recovery records {#recovery-records}

Returns `{ records }`, newest first, without paging. Each record has these fields:

| Field | Notes |
|---|---|
| `id` | The recovery ID. |
| `resource`, `operation` | What the write changed, and how. |
| `access_key_id` | The access key that made the write. |
| `created_at` | When Phoenix took the copy. |
| `phase` | `prepared`, `dispatching`, `partial`, `unknown`, `verified`, `not_applied` or `reviewed`. |
| `before`, `source`, `expected` | The stored copies, and what the write was meant to produce. |
| `observations` | What Phoenix saw after the phase changed. |
| `reviewed_by` | Who reviewed it, once someone has. |
| `active` | `true` while the write is still running. |

Phoenix always keeps active records and records in `unknown`. It keeps the rest for 30 days, at most 20 per resource and 256 in all.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 503 | `service_unavailable` | `adminError.recoveryStorageUnavailable` | Phoenix couldn't load its recovery records. |

### Act on a record {#recovery-action}

Inspects, reviews, deletes or restores one record, chosen by `action`.

| Field | Type | Notes |
|---|---|---|
| `action` | string | Required. `inspect`, `reviewed`, `delete` or `restore`. |
| `side` | string | For `restore` only. `"before"` or `"source"`. |

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.recoveryNotFound` | No record has this ID. |
| 400 | `invalid_request` | `adminError.recoveryUnknownAction` | `action` isn't one of the four. |
| 400 | `invalid_request` | `adminError.recoverySideRequired` | `side` is missing, or that side has no stored copy. |
| 400 | `invalid_request` | `adminError.recoveryManualOnly` | Phoenix can't restore this kind of copy. Restore it by hand. |
| 409 | `conflict` | a recovery key | The record refuses the action. The key names why. |

The recovery keys are `adminError.recoveryStorageUnavailable`, `adminError.recoveryOperationActive`, `adminError.recoveryCommandRunning`, `adminError.recoveryResolveInApprovals`, `adminError.recoveryFull`, `adminError.recoveryReviewBeforeRemoving`, `adminError.recoveryReviewBeforeRestoring`, `adminError.recoveryChangedAfterReview`, `adminError.recoveryAuthorityChanged`, `adminError.recoveryHelperUnreadable`, `adminError.recoverySnapshotUnsafe` and `adminError.recoverySnapshotUnsafeAt`. Any other refusal uses `adminError.recoveryBlocked`. `adminError.recoverySnapshotUnsafeAt` names the refused place in `message_params.location`.

### Inspect {#recovery-inspect}

Reads the current state of what the record covers. It never proves which request caused that state. The response depends on the kind of copy:

- A restart receipt: `{ runtime_replaced, "execution_attribution": "unproven" }`.
- A storage helper: `{ observed, helper_present, "execution_attribution": "unproven" }`.
- A Template helper: `{ observed, entry_present, "execution_attribution": "unproven" }`.
- Anything else: `{ "status": "manual_verification_required" }`.

### Review and delete {#recovery-review}

`reviewed` marks the record reviewed and answers `{ "status": "reviewed" }`. `delete` removes it and answers `{ "status": "delete" }`. Delete needs the phase `verified`, `not_applied` or `reviewed`.

Both refuse while the write is still running, or while a Home Assistant command Phoenix sent hasn't finished.

### Restore {#recovery-restore}

Writes the chosen side back. The record must be inactive, in phase `verified`, `not_applied` or `reviewed`, with a stored copy on that side.

- Phoenix restores storage helpers, Template helpers, Energy preferences, blueprint sources, and dashboard registry, layout and resource copies.
- Copies of configuration file text are always refused, because Phoenix doesn't write file text back.
- Every other kind, including restart receipts and Zigbee copies, answers `400` with `adminError.recoveryManualOnly`.

A restore answers `200` with `{ result, outcome }`, where `result` is the restore tool's result. A refused or failed restore also answers `200`, with `result.isError: true` and the tool's own text. Check `result`, not the status code.

## Dashboard card catalog {#card-catalog}

```
GET    /api/phoenix-mcp/admin/card_catalog
POST   /api/phoenix-mcp/admin/card_catalog
```

The card catalog lists the custom dashboard cards a browser found. The panel sends the reports; you rarely need to.

### Read the catalog {#read-catalog}

The response is `{ harvested, entries, harvested_at, resource_count, failed_imports, resource_fingerprint }`. `harvested` stays `false` until a browser has reported. That isn't the same as having no custom cards.

Each item in `entries` has `type`, `name`, `description`, `documentation_url`, `preview`, `available`, `has_visual_editor`, `stub_config` and `source`. `source` is `picker` or `element`.

### Report a harvest {#report-catalog}

Replaces the whole catalog with what a browser found.

| Field | Type | Notes |
|---|---|---|
| `entries` | array | Required. At most 500 entries are kept, one per `type`. A `type` is at most 120 characters. A `stub_config` over 2000 bytes is dropped. |
| `resource_count` | number | Optional. How many dashboard resources the browser saw. |
| `failed_imports` | array of `{ url, error }` | Optional. At most 50 are kept. |

Phoenix cleans every field and skips bad entries instead of failing. It reads the dashboard resources itself and stores `resource_fingerprint`, a SHA-256 over their types and URLs. The fingerprint is `null` when Phoenix can't read them.

The response is `{ harvested, harvested_at, cards, available, failed_imports }`. The last three are counts.

The panel reports at most once every 10 minutes per browser and panel version. It skips the report when it found no resources and no cards, and reports again only when more cards appear.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.entriesListRequired` | `entries` is missing or isn't an array. |
