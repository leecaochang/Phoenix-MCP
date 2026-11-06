---
{ "id": "topic.adminApiKeys", "title": "Access Key Endpoints", "parent": "topic.adminApi", "order": 10, "keywords": ["access key API", "token API", "phx_", "create key", "permissions API", "capabilities API"], "sectionKeywords": { "access-keys": ["access key record", "key list"], "capability-fields": ["cap_", "deny", "allow", "confirm", "capability field"], "key-create": ["new key", "access_key", "raw value", "201"], "key-change": ["PATCH", "persona", "notes", "rename", "unknownFields"], "key-revoke": ["delete key", "revoke", "phoenix_mcp_access_key_revoked"], "key-rotate": ["rotate", "new value", "phoenix_mcp_access_key_rotated"], "key-stats": ["request_count", "denied_count", "rate_limit_hits"], "key-audit": ["audit rows", "outcome filter", "client IP"], "key-archived": ["archived keys", "expired keys", "revoked keys"], "presets": ["preset", "saved settings", "access_key_presets_enabled"], "permissions": ["permission tree", "nodes", "domains", "devices", "entities"], "permissions-replace": ["PUT", "replace tree", "clear all"], "permissions-node": ["single node", "set state"], "bulk-select": ["area", "label", "integration", "selector"], "integration-options": ["config entry", "integration list"], "entity-hints": ["hint", "global hint", "entity_hints"], "entities": ["entity tree", "scope", "resolve", "force_reload"], "resolve": ["resolution_path", "effective permission", "NO_ACCESS", "NOT_FOUND"], "scope": ["readable", "writable", "capability_flags"] }, "covers": ["custom_components/phoenix_mcp/admin/access_keys.py", "custom_components/phoenix_mcp/admin/permissions.py", "custom_components/phoenix_mcp/access_key_store.py"] }
---
These endpoints manage access keys: creating them, changing their settings and capabilities, and setting what each one may read and control. Every path starts with `/api/phoenix-mcp/admin` and needs an administrator.

Request bodies, the error envelope and the ID rules work as described on [Admin API](guide:topic.adminApi). The error tables below list each endpoint's own errors. Body errors from [Request bodies](guide:topic.adminApi#bodies) apply to every endpoint that takes a body.

Related: [Errors](guide:topic.adminApi#errors), [IDs in paths](guide:topic.adminApi#ids), [Permission states](guide:topic.adminApi#states)

## Access keys {#access-keys}

```
GET    /api/phoenix-mcp/admin/access-keys
POST   /api/phoenix-mcp/admin/access-keys
GET    /api/phoenix-mcp/admin/access-keys/{id}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}
DELETE /api/phoenix-mcp/admin/access-keys/{id}
POST   /api/phoenix-mcp/admin/access-keys/{id}/rotate
GET    /api/phoenix-mcp/admin/access-keys/{id}/stats
GET    /api/phoenix-mcp/admin/access-keys/{id}/connection
GET    /api/phoenix-mcp/admin/access-keys/{id}/audit
GET    /api/phoenix-mcp/admin/access-keys/archived
DELETE /api/phoenix-mcp/admin/access-keys/archived/{id}
```

Use these to create, change, rotate and revoke keys, and to read their use. `{id}` is the access key's ID.

Every endpoint that names a key answers `404` when the key doesn't exist:

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

### The access key record {#key-record}

Most endpoints answer with the key's record. It never contains the key itself.

| Field | Type | Notes |
|---|---|---|
| `id` | string | The key's ID. |
| `name` | string | The key's name. |
| `created_at` | string | ISO 8601 time. |
| `created_by` | string | Home Assistant user ID of the administrator who created it. |
| `expires_at` | string or null | ISO 8601 time, or `null` for no expiry. |
| `revoked` | boolean | |
| `last_used_at` | string or null | Last time an agent used the key. |
| `updated_at` | string | Last change to the key. |
| `pass_through` | boolean | See [Pass-through Mode](guide:topic.passThrough). |
| `use_assist_exposure` | boolean | Present only when `pass_through` is `true`. |
| `announce_all_tools` | boolean | |
| `confirm_inline_wait_seconds` | integer | `0` when off. |
| `rate_limit_requests` | integer | Requests per minute. `0` turns rate limiting off. |
| `rate_limit_burst` | integer | Requests per second. |
| `persona` | string | The persona label. |
| `notes` | string or null | Notes for the agent. |
| `permissions` | object | The permission tree, as in [Permissions](guide:#permissions). |
| `presets` | array | Saved presets, as in [Presets](guide:#presets). |
| `active_preset_id` | string or null | The preset the key matches. |
| `cap_*` | string | One field per capability: `deny`, `allow` or `confirm`. |

### Capability fields {#capability-fields}

Each key has these 32 fields. Each takes `deny`, `allow` or `confirm`, but only the fields marked Yes accept `confirm`.

| Field | Accepts `confirm` | Capability |
|---|---|---|
| `cap_config_read` | No | [{{label:caps.cap_config_read.label}}](guide:window.capabilities#config-read) |
| `cap_camera_read` | No | [{{label:caps.cap_camera_read.label}}](guide:window.capabilities#camera-read) |
| `cap_template_render` | No | [{{label:caps.cap_template_render.label}}](guide:window.capabilities#template-render) |
| `cap_log_read` | No | [{{label:caps.cap_log_read.label}}](guide:window.capabilities#log-read) |
| `cap_log_control` | Yes | [{{label:caps.cap_log_control.label}}](guide:window.capabilities#log-control) |
| `cap_search` | No | [{{label:caps.cap_search.label}}](guide:window.capabilities#search) |
| `cap_registry_read` | No | [{{label:caps.cap_registry_read.label}}](guide:window.capabilities#registry-read) |
| `cap_traces` | No | [{{label:caps.cap_traces.label}}](guide:window.capabilities#traces) |
| `cap_diagnostics` | No | [{{label:caps.cap_diagnostics.label}}](guide:window.capabilities#diagnostics) |
| `cap_broadcast` | No | [{{label:caps.cap_broadcast.label}}](guide:window.capabilities#broadcast) |
| `cap_notify` | Yes | [{{label:caps.cap_notify.label}}](guide:window.capabilities#notify) |
| `cap_service_response` | No | [{{label:caps.cap_service_response.label}}](guide:window.capabilities#service-response) |
| `cap_automation_write` | Yes | [{{label:caps.cap_automation_write.label}}](guide:window.capabilities#automation-write) |
| `cap_script_write` | Yes | [{{label:caps.cap_script_write.label}}](guide:window.capabilities#script-write) |
| `cap_blueprint_write` | Yes | [{{label:caps.cap_blueprint_write.label}}](guide:window.capabilities#blueprint-write) |
| `cap_scene_write` | Yes | [{{label:caps.cap_scene_write.label}}](guide:window.capabilities#scene-write) |
| `cap_helper_write` | Yes | [{{label:caps.cap_helper_write.label}}](guide:window.capabilities#helper-write) |
| `cap_calendar_write` | Yes | [{{label:caps.cap_calendar_write.label}}](guide:window.capabilities#calendar-write) |
| `cap_physical_control` | Yes | [{{label:caps.cap_physical_control.label}}](guide:window.capabilities#physical-control) |
| `cap_restart` | Yes | [{{label:caps.cap_restart.label}}](guide:window.capabilities#restart) |
| `cap_integration_write` | Yes | [{{label:caps.cap_integration_write.label}}](guide:window.capabilities#integration-write) |
| `cap_integration_reconfigure` | Yes | [{{label:caps.cap_integration_reconfigure.label}}](guide:window.capabilities#integration-reconfigure) |
| `cap_lovelace_write` | Yes | [{{label:caps.cap_lovelace_write.label}}](guide:window.capabilities#lovelace-write) |
| `cap_registry_write` | Yes | [{{label:caps.cap_registry_write.label}}](guide:window.capabilities#registry-write) |
| `cap_radio_write` | Yes | [{{label:caps.cap_radio_write.label}}](guide:window.capabilities#radio-write) |
| `cap_energy_write` | Yes | [{{label:caps.cap_energy_write.label}}](guide:window.capabilities#energy-write) |
| `cap_dashboard_resources` | Yes | [{{label:caps.cap_dashboard_resources.label}}](guide:window.capabilities#dashboard-resources) |
| `cap_backup` | Yes | [{{label:caps.cap_backup.label}}](guide:window.capabilities#backup) |
| `cap_filesystem` | No | [{{label:caps.cap_filesystem.label}}](guide:window.capabilities#filesystem) |
| `cap_yaml_edit` | Yes | [{{label:caps.cap_yaml_edit.label}}](guide:window.capabilities#yaml-edit) |
| `cap_esphome_yaml` | No | [{{label:caps.cap_esphome_yaml.label}}](guide:window.capabilities#esphome-yaml) |
| `cap_esphome_flash` | Yes | [{{label:caps.cap_esphome_flash.label}}](guide:window.capabilities#esphome-flash) |

Related: [Deny, Allow and Confirm](guide:window.capabilities#modes)

### List keys {#key-list}

`GET /access-keys` answers with a bare JSON array of active key records. Revoked and expired keys are in [the archive](guide:#key-archived).

### Create a key {#key-create}

`POST /access-keys` creates a key.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Required. 3 to 32 letters, digits, `-` or `_`. It must differ from every active key's name, ignoring case and treating `-` and `_` as the same. Names of archived keys are free to reuse. |
| `pass_through` | boolean | Default `false`. |
| `confirm_pass_through` | boolean | Must be `true` when `pass_through` is `true`. |
| `use_assist_exposure` | boolean | Default `false`. Kept only when `pass_through` is `true`. |
| `expires_at` | string | Optional. An ISO 8601 time in the future. Without a time zone, Phoenix reads it as Home Assistant's local time and stores it in UTC. Leave the field out for no expiry; `null` is refused. |
| `rate_limit_requests` | integer | 0 to 100000 (default 60). Set to 0 to turn rate limiting off. |
| `rate_limit_burst` | integer | 0 to 100000 (default 10). Stored as 0 when `rate_limit_requests` is 0. |

Create reads only these fields. A new key starts with persona `custom`, every capability `deny`, an empty permission tree, `confirm_inline_wait_seconds` 0 and `announce_all_tools` off. Set the rest with [a PATCH](guide:#key-change) afterwards.

The response is `201` with the new record plus `access_key`, the key itself: `phx_` followed by 64 characters. Phoenix shows it only in this response, so store it now.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nameRequired` | `name` is missing. |
| 400 | `invalid_request` | `adminError.accessKeyNameLengthPattern` | `name` breaks the naming rule. `message_params` has `min` and `max`. |
| 400 | `invalid_request` | `adminError.booleanRequired` | `pass_through` or `use_assist_exposure` isn't a boolean. `message_params` names the `field`. |
| 400 | `invalid_request` | `adminError.passThroughConfirmationRequired` | `pass_through` is `true` without `confirm_pass_through: true`. |
| 400 | `invalid_request` | `adminError.badExpiresAt` | `expires_at` isn't a usable time. |
| 400 | `invalid_request` | `adminError.expiresInPast` | `expires_at` isn't in the future. |
| 400 | `invalid_request` | `adminError.rateLimitsInteger` | A rate limit isn't a whole number. |
| 400 | `invalid_request` | `adminError.rateLimitsNonNegative` | A rate limit is negative. |
| 400 | `invalid_request` | `adminError.rateLimitsMaximum` | A rate limit is over `max` (100000). |
| 409 | `conflict` | `adminError.accessKeyNameTaken` | An active key already has this name. |

### Read a key {#key-read}

`GET /access-keys/{id}` answers with the key's record.

### Change a key {#key-change}

`PATCH /access-keys/{id}` changes any subset of these fields. The response is `200` with the updated record.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Same rule as on create, compared with every other active key. |
| `pass_through` | boolean | Turning it on for a key that has it off also needs `confirm_pass_through: true`. |
| `confirm_pass_through` | boolean | See `pass_through`. |
| `use_assist_exposure` | boolean | Allowed only when the key ends up with `pass_through` on. |
| `announce_all_tools` | boolean | |
| `confirm_inline_wait_seconds` | integer | 0 (off) or 30 to 180. |
| `rate_limit_requests` | integer | 0 to 100000. Setting it to 0 also sets `rate_limit_burst` to 0. |
| `rate_limit_burst` | integer | 0 to 100000. |
| `persona` | string | One of `home_companion`, `read_only`, `voice_assistant`, `automation_builder`, `power_user`, `home_admin`, `new_user`, `dashboard_designer`, `maintenance`, `esphome`, `custom`. |
| `notes` | string or null | At most 2000 characters, after line endings become LF and surrounding spaces are trimmed. Control, bidirectional override and tag characters are refused. `null` or `""` clears the notes. |
| `cap_*` | string | Any [capability field](guide:#capability-fields): `deny`, `allow` or `confirm`. |

PATCH refuses any field not in this table, and `expires_at` can't be changed at all.

`persona` sets the key's persona label. It doesn't change any capability, so send the capability values you want in the same request.

A key with persona `home_companion` always has `pass_through` and `announce_all_tools` off. Asking to turn either on is refused.

Renaming a key changes the display name of its sensors and device in Home Assistant. Their entity IDs stay the same.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.expiresImmutable` | The body contains `expires_at`. Phoenix checks this before it looks up the key. |
| 400 | `invalid_request` | `adminError.unknownFields` | The body has fields not in the table. `message_params` lists them as `fields`. |
| 400 | `invalid_request` | `adminError.nameRequired` | `name` is empty. |
| 400 | `invalid_request` | `adminError.accessKeyNameLengthPattern` | `name` breaks the naming rule. `message_params` gives `min` and `max`. |
| 400 | `invalid_request` | `adminError.accessKeyNameTaken` | Another active key already has this name. |
| 400 | `invalid_request` | `adminError.booleanRequired` | A boolean field has another type. `message_params` names the `field`. |
| 400 | `invalid_request` | `adminError.passThroughConfirmationRequired` | `pass_through` turns on without `confirm_pass_through: true`. |
| 400 | `invalid_request` | `adminError.assistExposurePassThroughOnly` | `use_assist_exposure` without pass-through. |
| 400 | `invalid_request` | `adminError.inlineWaitRange` | `confirm_inline_wait_seconds` is out of range. `message_params` gives `min` and `max`. |
| 400 | `invalid_request` | `adminError.integerRequired` | A rate limit isn't a whole number. `message_params` names the `field`. |
| 400 | `invalid_request` | `adminError.nonNegativeRequired` | A rate limit is negative. `message_params` names the `field`. |
| 400 | `invalid_request` | `adminError.maximumRequired` | A rate limit is over 100000. `message_params` gives `field` and `max`. |
| 400 | `invalid_request` | `adminError.unknownPersona` | `persona` isn't one of the listed values. |
| 400 | `invalid_request` | `adminError.companionSettingRefused` | A Home Companion key asks for `pass_through` or `announce_all_tools`. |
| 400 | `invalid_request` | `adminError.notesString` | `notes` isn't a string or `null`. |
| 400 | `invalid_request` | `adminError.notesTooLong` | `notes` is over `max` characters. |
| 400 | `invalid_request` | `adminError.notesInvalidCharacters` | `notes` contains a refused character. |
| 400 | `invalid_request` | `adminError.capabilityModeInvalid` | A `cap_*` value isn't `deny`, `allow` or `confirm`. `message_params` names the `capability`. |
| 400 | `invalid_request` | `adminError.capabilityConfirmUnsupported` | `confirm` on a capability that doesn't accept it. `message_params` names the `capability`. |
| 400 | `invalid_request` | `adminError.capabilityAllowUnsupported` | `allow` on `cap_dashboard_resources`, which always needs approval. `message_params` names the `capability`. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

### Revoke a key {#key-revoke}

`DELETE /access-keys/{id}` revokes the key straight away and moves it to the archive. The response is `204` with no body. Phoenix also:

- Removes the key from the Assist, Voice Agent and AI Task bindings. An Assist pipeline or AI Task entity that Phoenix created for the key goes too.
- Cancels the key's queued approvals and releases its MESA leases.
- Drops its rate-limit state and request counters, and removes its sensors.
- Fires the `phoenix_mcp_access_key_revoked` event with `access_key_id`, `access_key_name`, `revoked_by` and `timestamp`.

Related: [{{label:accessKeys.revoke}}](guide:window.accessKeys#revoke)

### Rotate a key {#key-rotate}

`POST /access-keys/{id}/rotate` gives the key a new value and takes no body. The old value stops working straight away. The key keeps its rate-limit state and counters.

The response is `200` with the record plus `access_key`, the new value. As on create, Phoenix shows it only once. Phoenix fires the `phoenix_mcp_access_key_rotated` event with `access_key_id`, `access_key_name`, `rotated_by` and `timestamp`.

Related: [{{label:accessKeys.rotate}}](guide:window.accessKeys#rotate)

### Key statistics {#key-stats}

`GET /access-keys/{id}/stats` answers with the key's request counters. The counters start at zero after each Home Assistant restart.

| Field | Type | Notes |
|---|---|---|
| `access_key_id` | string | |
| `access_key_name` | string | |
| `request_count` | integer | Requests since the restart. |
| `denied_count` | integer | Denied requests since the restart. |
| `rate_limit_hits` | integer | Requests refused by the rate limit since the restart. |
| `last_used_at` | string or null | |
| `status` | string | `active` or `expired`. |

### Connection check {#key-connection}

`GET /access-keys/{id}/connection` answers with `last_used_at` and `request_count`. Every request an agent sends with a valid key raises `request_count`, so poll this to see whether an app has connected.

### Key audit log {#key-audit}

`GET /access-keys/{id}/audit` answers with a bare array of the key's audit rows, newest first. Changes administrators make to the key are logged under the administrator, so they don't appear here.

| Query | Notes |
|---|---|
| `limit` | Up to 500 (default 100). |
| `offset` | Rows to skip. |
| `outcome` | One of `allowed`, `denied`, `not_found`, `rate_limited`, `not_implemented`, `invalid_request`, `pending_approval`. |
| `ip` | Matches rows whose client IP contains this text. |

Each row has `request_id`, `timestamp`, `access_key_id`, `access_key_name`, `method`, `resource`, `outcome`, `client_ip` and `pass_through`. When set, a row also has `payload`, `mesa_advisory`, `preset`, `stale_tools_advisory`, `ha_user_id` and `originating_access_key_id`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination` | `limit` or `offset` isn't a whole number. |
| 400 | `invalid_request` | `adminError.paginationNonNegative` | `limit` or `offset` is negative. |
| 400 | `invalid_request` | `adminError.outcomeFilterUnknown` | `outcome` isn't one of the listed values. `message_params` gives the `value`. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

### Archived keys {#key-archived}

`GET /access-keys/archived` answers with a bare array of revoked and expired keys. Each entry has `id`, `name`, `created_at`, `created_by`, `revoked_at`, `revoked`, `expires_at` and `last_used_at`. An expired key shows `revoked: false`.

`DELETE /access-keys/archived/{id}` deletes an archived record for good. The response is `204` with no body.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.archivedAccessKeyNotFound` | No archived record has this ID. |

## Presets {#presets}

```
POST   /api/phoenix-mcp/admin/access-keys/{id}/presets
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}
DELETE /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}
POST   /api/phoenix-mcp/admin/access-keys/{id}/presets/{pid}/apply
```

A preset saves a key's settings so you can switch back to them later. `{pid}` is the preset's ID. Every preset endpoint answers with the key's record.

Presets are off by default. Turn on the `access_key_presets_enabled` setting first. Until then, every preset endpoint answers `403`, before it looks up the key.

A preset holds the key's settings: `caps` (all 32 capabilities), `persona`, `permissions`, `pass_through`, `use_assist_exposure` and `announce_all_tools`. It also holds `confirm_inline_wait_seconds`, `rate_limit_requests` and `rate_limit_burst`, plus its own `id`, `name` and `created_at`. The key's name and notes aren't part of it.

Each key holds up to 8 presets. A preset name is 1 to 40 characters after trimming, without `<`, `>`, `"`, `'`, `&` or `;`. Names must differ within a key, ignoring case.

Every preset endpoint can also answer:

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 403 | `forbidden` | `adminError.presetsDisabled` | `access_key_presets_enabled` is off. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

### Save a preset {#preset-create}

`POST /access-keys/{id}/presets` saves the key's current settings as a new preset and makes it the active one. The response is `201`.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Required. The preset name rule above. |

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nameRequired` | `name` is missing. |
| 400 | `invalid_request` | `adminError.presetNameTooLong` | `name` is over `max` characters. |
| 400 | `invalid_request` | `adminError.presetNameInvalidCharacters` | `name` contains a refused character. |
| 400 | `invalid_request` | `adminError.presetNameTaken` | The key already has a preset with this name. |
| 400 | `invalid_request` | `adminError.presetLimitReached` | The key already has 8 presets. `message_params` has `max`. |
| 400 | `invalid_request` | `adminError.presetSaveRejected` | Phoenix refused the preset as sent. |

### Rename a preset {#preset-rename}

`PATCH /access-keys/{id}/presets/{pid}` renames a preset. The body and name errors are the same as for saving. The response is `200`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetRenameRejected` | Another preset of this key has the name. |
| 404 | `not_found` | `adminError.presetNotFound` | The key has no preset with this ID. |

### Delete a preset {#preset-delete}

`DELETE /access-keys/{id}/presets/{pid}` deletes a preset that isn't active. It takes no body and answers `200` with the record, not `204`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetDeleteRejected` | The preset is the active one. |
| 404 | `not_found` | `adminError.presetNotFound` | The key has no preset with this ID. |

### Apply a preset {#preset-apply}

`POST /access-keys/{id}/presets/{pid}/apply` makes the key match a preset and marks it active. The response is `200`.

| Field | Type | Notes |
|---|---|---|
| `confirm_pass_through` | boolean | Optional. Must be `true` when the preset turns pass-through on for a key that has it off. |

Before switching to a different preset, Phoenix saves the key's current settings into the active one. Applying the active preset again puts the key back to that preset's saved state.

Applying sets the persona, pass-through, Assist exposure, tool announcement, inline wait, rate limits, capabilities and permission tree. It also restarts the key's rate-limit count.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.presetPassThroughConfirmationRequired` | The preset turns pass-through on and the body lacks `confirm_pass_through: true`. |
| 404 | `not_found` | `adminError.presetNotFound` | The key has no preset with this ID. |

## Permissions {#permissions}

```
GET    /api/phoenix-mcp/admin/access-keys/{id}/permissions
PUT    /api/phoenix-mcp/admin/access-keys/{id}/permissions
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/domains/{node}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/devices/{node}
PATCH  /api/phoenix-mcp/admin/access-keys/{id}/permissions/entities/{node}
GET    /api/phoenix-mcp/admin/access-keys/{id}/permissions/integration-options
POST   /api/phoenix-mcp/admin/access-keys/{id}/permissions/bulk-select
```

These read and set the permission tree: which domains, devices and entities a key may read and control. Each node has a `state` and an optional `hint` for the agent.

```json
{
  "domains":  { "light": { "state": "YELLOW", "hint": null } },
  "devices":  { "<device_id>": { "state": "GREEN", "hint": "Desk lamp" } },
  "entities": { "light.desk": { "state": "RED", "hint": null } }
}
```

Node IDs follow [IDs in paths](guide:topic.adminApi#ids): domains under `domains`, device IDs under `devices` and entity IDs under `entities`. The states are in [Permission states](guide:topic.adminApi#states).

Related: [How a Permission Is Decided](guide:topic.permissionResolution), [{{label:accessKeys.permissionsTree}}](guide:window.permissions)

### Read the tree {#permissions-read}

`GET /access-keys/{id}/permissions` answers with the tree.

### Replace the tree {#permissions-replace}

`PUT /access-keys/{id}/permissions` replaces the whole tree with the body. The response is `200` with the new tree.

| Field | Type | Notes |
|---|---|---|
| `domains`, `devices`, `entities` | object | Each maps node IDs to node objects. Phoenix ignores other top-level fields. |
| `state` | string | In each node. Defaults to `GREY`. |
| `hint` | string or null | In each node. At most 200 characters, kept exactly as sent. |

To clear every permission, send `{"domains": {}, "devices": {}, "entities": {}}`.

Related: [{{label:workspace.clearAllPermissions}}](guide:window.permissions#clear-all)

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.permissionSectionObject` | A `section` isn't an object. |
| 400 | `invalid_request` | `adminError.permissionNodeObject` | A `node` isn't an object. |
| 400 | `invalid_request` | `adminError.nodeIdTooLong` | A node ID is over 255 characters. |
| 400 | `invalid_request` | `adminError.nodeIdInvalidChars` | A node ID contains a refused character. |
| 400 | `invalid_request` | `adminError.badDomain` | A domain ID isn't a valid domain. |
| 400 | `invalid_request` | `adminError.badEntityId` | An entity ID isn't a valid entity ID. |
| 400 | `invalid_request` | `adminError.permissionStateInvalidDomain`, `adminError.permissionStateInvalidDevice` or `adminError.permissionStateInvalidEntity` | A node's `state` isn't one of the four. The key names the node's type; `message_params` gives `state`, `node` and `states`. |
| 400 | `invalid_request` | `adminError.permissionHintString` | A node's `hint` isn't a string or `null`. |
| 400 | `invalid_request` | `adminError.permissionHintTooLong` | A node's `hint` is over `max` characters. |
| 400 | `invalid_request` | `adminError.permissionTreeInvalid` | The tree can't be saved as sent. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

### Set one node {#permissions-node}

`PATCH /access-keys/{id}/permissions/domains/{node}`, `.../devices/{node}` and `.../entities/{node}` set one node. `{node}` is the domain, device ID or entity ID. The response is `200` with the whole tree.

| Field | Type | Notes |
|---|---|---|
| `state` | string | Required. One of the four states. |
| `hint` | string or null | Optional. At most 200 characters. Leave it out to keep the node's hint; `null` or `""` clears it. |

Setting `GREY` on a node without a hint removes the node.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.nodeIdTooLong` | `{node}` is over 255 characters. Phoenix checks the node ID first. |
| 400 | `invalid_request` | `adminError.nodeIdInvalidChars` | `{node}` contains a refused character. |
| 400 | `invalid_request` | `adminError.badDomain` | `{node}` isn't a valid domain. |
| 400 | `invalid_request` | `adminError.badEntityId` | `{node}` isn't a valid entity ID. |
| 400 | `invalid_request` | `adminError.stateAllowed` | `state` is missing or not one of the four. `message_params` lists the `states`. |
| 400 | `invalid_request` | `adminError.hintString` | `hint` isn't a string or `null`. |
| 400 | `invalid_request` | `adminError.hintTooLong` | `hint` is over `max` characters. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

### Integration options {#integration-options}

`GET /access-keys/{id}/permissions/integration-options` lists the integrations you can use with [bulk select](guide:#bulk-select). The response is `{"integrations": [...]}`, sorted by title, domain and entry ID.

There's one row per config entry that owns at least one device or live entity without a device:

| Field | Type | Notes |
|---|---|---|
| `entry_id` | string | Use this as `selector_id`. |
| `domain` | string | The integration's domain. |
| `title` | string | The entry's title. |
| `device_count` | integer | |
| `deviceless_entity_count` | integer | |
| `device_ids` | array | |
| `entity_ids` | array | Live, enabled entities without a device. |
| `registry_only_deviceless_count` | integer | Entities without a device that have no live state. |
| `required_domain_ids` | array | |
| `shared_device_count` | integer | |

### Bulk select {#bulk-select}

`POST /access-keys/{id}/permissions/bulk-select` sets one state on everything in an area, label or integration.

| Field | Type | Notes |
|---|---|---|
| `selector_type` | string | `area`, `label` or `integration`. |
| `selector_id` | string | Required. The area ID, label ID or config entry ID. |
| `state` | string | One of the four states. |

- An integration sets its device nodes and the nodes of its entities that have no device.
- An area or label sets entity nodes only, for every entity whose area or label matches. An entity's labels include its device's labels.
- Existing hints stay. With `GREY`, nodes without a hint go, and nodes with a hint stay as `GREY`.

The response has the new tree and a summary:

```json
{
  "permissions": { "domains": {}, "devices": {}, "entities": {} },
  "summary": {
    "selector_type": "area", "selector_id": "kitchen", "state": "GREEN",
    "device_count": 0, "entity_count": 12,
    "registry_only_deviceless_count": 0, "required_domain_ids": [],
    "shared_device_count": 0
  }
}
```

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.selectorTypeInvalid` | `selector_type` isn't one of the three. |
| 400 | `invalid_request` | `adminError.selectorIdRequired` | `selector_id` is missing or empty. |
| 400 | `invalid_request` | `adminError.stateAllowed` | `state` isn't one of the four. `message_params` lists the `states`. |
| 404 | `not_found` | `adminError.permissionSelectionNotFound` | The entry doesn't exist, or no entity matches the area or label. Phoenix checks this before it looks up the key. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

Related: [{{label:selectBy.menu}}](guide:window.permissions#select-by)

## Entity hints {#entity-hints}

```
GET    /api/phoenix-mcp/admin/entity-hints
PUT    /api/phoenix-mcp/admin/entity-hints/{entity_id}
```

Global hints tell every agent something about an entity, whichever key it uses. A hint on a key's own permission node wins over the global one.

Agents see the hint from the key's entity node first, then its device nodes, then its domain node, and only then the global hint. Phoenix removes text that looks like a secret, and replaces entity IDs the key can't reach with `<redacted-entity>`.

Related: [Entity hints](guide:window.permissions#hints)

### Read global hints {#hints-read}

`GET /entity-hints` answers with every global hint:

```json
{ "entity_hints": { "light.desk": "Desk lamp by the window" } }
```

### Set a global hint {#hints-set}

`PUT /entity-hints/{entity_id}` sets or clears one hint. The response is the whole `entity_hints` map. Phoenix checks the entity ID's format only, not whether the entity exists.

| Field | Type | Notes |
|---|---|---|
| `hint` | string or null | Trimmed, at most 200 characters. `null`, `""` or no `hint` field clears it. |

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | `{entity_id}` isn't a valid entity ID. |
| 400 | `invalid_request` | `adminError.hintString` | `hint` isn't a string or `null`. |
| 400 | `invalid_request` | `adminError.hintTooLong` | `hint` is over `max` characters. |

## Entities {#entities}

```
GET    /api/phoenix-mcp/admin/entities
GET    /api/phoenix-mcp/admin/access-keys/{id}/resolve/{entity_id}
GET    /api/phoenix-mcp/admin/access-keys/{id}/scope
```

These show your home as the permissions editor sees it, and what one key can actually reach.

### Entity tree {#entity-tree}

`GET /entities` answers with every entity, grouped by domain and device. It isn't paged. Phoenix keeps the tree cached; add `force_reload` with any non-empty value to rebuild it.

```json
{
  "light": {
    "devices": {
      "<device_id>": {
        "device_id": "...", "parent_device_id": null, "name": "...",
        "area_id": "...", "area_name": "...", "entities": ["light.desk"]
      }
    },
    "deviceless_entities": ["light.porch"],
    "entity_details": {
      "light.desk": {
        "entity_id": "light.desk", "friendly_name": "...", "device_id": "...",
        "area_id": "...", "area_name": "...", "labels": [{"id": "...", "name": "..."}]
      }
    }
  }
}
```

- Disabled entities, the `phoenix_mcp` domain and Phoenix's own sensors are left out.
- An entity with a state but no registry entry appears in `deviceless_entities`.
- A child device's main device appears in every domain where the child does.
- `person`, `tag` and `zone` are always present, even when empty.
- An entity's `labels` include its device's labels.

### Explain one permission {#resolve}

`GET /access-keys/{id}/resolve/{entity_id}` shows how Phoenix decides one entity's permission for the key.

| Field | Type | Notes |
|---|---|---|
| `entity_id` | string | |
| `resolution_path` | array | `{level, state}` steps, outermost first. |
| `effective` | string | `WRITE`, `READ`, `DENY`, `NO_ACCESS` or `NOT_FOUND`. |
| `effective_hint` | string or null | The hint agents see, in the order given under [Entity hints](guide:#entity-hints). |

The `level` values run `global` (always `GREY`), `domain:<domain>`, then `device:<name>` for each device above the entity, main device first, then `entity:<entity_id>`. Device levels show the device's name, not its ID.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | `{entity_id}` isn't a valid entity ID. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No active key has this ID. |

Related: [How a Permission Is Decided](guide:topic.permissionResolution)

### Key scope {#scope}

`GET /access-keys/{id}/scope` lists the entities a key can read and control.

| Field | Type | Notes |
|---|---|---|
| `access_key_id` | string | |
| `access_key_name` | string | |
| `readable` | array | Entity IDs, sorted. |
| `writable` | array | Entity IDs, sorted. |
| `persona` | string | |
| `capability_flags` | object | The key's stored capability values, before pass-through applies. |
