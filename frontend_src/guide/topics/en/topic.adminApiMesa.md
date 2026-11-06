---
{ "id": "topic.adminApiMesa", "title": "MESA Endpoints", "parent": "topic.adminApi", "order": 30, "keywords": ["MESA API", "profiles API", "semantic profile", "privacy classification", "safety profile"], "sectionKeywords": { "shared": ["mesaUnavailable", "503", "audit log", "lease", "mesa_request_lease", "mesa_release_lease"], "entity-profiles": ["entity profile", "cursor", "next_cursor", "origin"], "list-profiles": ["filter", "tag", "area", "inferred_ai", "has_more", "total_matched"], "read-profile": ["effective", "explanation", "resolved profile"], "save-profile": ["If-None-Match", "create only", "semantic_profile", "privacy_classification", "warnings", "mesaProfileExists"], "scope-profiles": ["device profile", "area profile", "integration profile", "domain profile"], "picker-lists": ["device-options", "integration-options"], "defaults": ["deployment_defaults", "default_control_mode", "triggers_automations_domains", "domain_overrides", "autonomous", "confirm", "read_only", "prohibited"], "issues": ["vocabulary", "canonical_tags", "canonical_roots", "trigger issues", "orphans", "refresh"], "clear-orphans": ["orphaned profiles", "clean up"], "portability": ["backup", "archive", "mesa_export", "format_version"], "import": ["on_conflict", "overwrite", "skip", "skipped_existing", "invalid"], "suggestions": ["dismiss", "dismissed_suggestions", "restore suggestion"] }, "covers": ["custom_components/phoenix_mcp/admin/mesa.py", "custom_components/phoenix_mcp/mesa.py"] }
---
These endpoints read and change MESA: profiles at every level, deployment defaults, the tag vocabulary, issues and suggestions, and portable archives. Signing in, the error body and request bodies work as described in [Admin API](guide:topic.adminApi).

Related: [Errors](guide:topic.adminApi#errors), [Request bodies](guide:topic.adminApi#bodies), [How MESA Decides](guide:topic.mesaRules)

## For every MESA endpoint {#shared}

Every MESA endpoint except `device-options`, `integration-options` and `vocabulary` needs MESA running. While it isn't, they answer this:

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 503 | `service_unavailable` | `adminError.mesaUnavailable` | MESA isn't running. |
| 503 | `service_unavailable` | `adminError.mesaSaveFailed` | Phoenix couldn't save the change, so it undid it. |

Every change adds an audit log entry with the request path. Export adds one too, although it changes nothing.

MESA leases have no admin endpoints. Phoenix keeps them in memory, and agents request and release them with their own tools.

## Entity profiles {#entity-profiles}

```
GET    /api/phoenix-mcp/admin/mesa/profiles
GET    /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
PUT    /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
DELETE /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
```

Use these to find, read, save and delete the MESA profile of one entity.

Related: [Profiles](guide:window.mesaProfiles), [Levels](guide:topic.mesaRules#levels)

### List entity profiles {#list-profiles}

Lists stored entity profiles that match every filter you send, a page at a time. Inferred and unknown origins are included.

| Field | Type | Notes |
|---|---|---|
| `domain` | query, string | Only entities in this domain. |
| `tag` | query, string | Only profiles whose effective tags include this tag. |
| `area` | query, string | Only entities in this area. |
| `origin` | query, string | `developer`, `user`, `hybrid`, `inferred_ai` or `unknown`. |
| `limit` | query, integer | Default 50. Values outside 1 to 200 count as the nearest end. |
| `cursor` | query, string | The `next_cursor` of the previous page. Leave it out for the first page. |

The response is `{ profiles, total_matched, has_more, next_cursor }`. Each item in `profiles` is `{ entity_id, document }`. Ask for the next page with `cursor` while `has_more` is `true`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.integerRequired` | `limit` isn't a whole number. `message_params.field` is `limit`. |
| 400 | `invalid_request` | none, `message_passthrough` | MESA refused the cursor or a filter. The message is MESA's own. |

### Read an entity profile {#read-profile}

The response is `{ entity_id, stored, effective, explanation }`:

- `stored` is the entity's own profile document, or `null` when it has none.
- `effective` is the profile MESA applies after combining every level.
- `explanation` is MESA's account of how it got there.

Related: [How profiles combine](guide:topic.mesaRules#combining)

### Save an entity profile {#save-profile}

Saves the entity's profile, replacing any it had. Send a profile document:

```json
{
  "semantic_profile": { },
  "privacy_classification": { }
}
```

You can also send a bare semantic profile. The `stored` field of a read has the same shape. Phoenix stamps the document with the current schema version, and saves it with origin `user` when it names none.

To create a profile without overwriting one, send the header `If-None-Match: *`. If the entity already has a profile, nothing is saved and you get `412`.

The entity ID in the path must be a domain, a dot, then lower-case letters, digits and underscores. Use the same form for reads and deletes.

The response is `{ entity_id, stored, warnings }`. `warnings` lists the trigger issues MESA finds for this entity, in the shape described in [Issues and the tag vocabulary](guide:#issues).

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | The entity ID has the wrong form. |
| 412 | `precondition_failed` | `adminError.mesaProfileExists` | You sent `If-None-Match: *` and a profile already exists. |
| 400 | `invalid_request` | none, `message_passthrough` | MESA refused the document. The message is MESA's own. |

### Delete an entity profile {#delete-profile}

Removes the entity's own profile, so it follows the levels above it again. The response is `{ entity_id, "deleted": true }`, also when there was no profile.

## Profiles at other levels {#scope-profiles}

```
GET    /api/phoenix-mcp/admin/mesa/devices
GET    /api/phoenix-mcp/admin/mesa/devices/{device_id}
PUT    /api/phoenix-mcp/admin/mesa/devices/{device_id}
DELETE /api/phoenix-mcp/admin/mesa/devices/{device_id}
GET    /api/phoenix-mcp/admin/mesa/device-options
GET    /api/phoenix-mcp/admin/mesa/areas
GET    /api/phoenix-mcp/admin/mesa/areas/{area_id}
PUT    /api/phoenix-mcp/admin/mesa/areas/{area_id}
DELETE /api/phoenix-mcp/admin/mesa/areas/{area_id}
GET    /api/phoenix-mcp/admin/mesa/integrations
GET    /api/phoenix-mcp/admin/mesa/integrations/{integration}
PUT    /api/phoenix-mcp/admin/mesa/integrations/{integration}
DELETE /api/phoenix-mcp/admin/mesa/integrations/{integration}
GET    /api/phoenix-mcp/admin/mesa/integration-options
GET    /api/phoenix-mcp/admin/mesa/domains
GET    /api/phoenix-mcp/admin/mesa/domains/{domain}
PUT    /api/phoenix-mcp/admin/mesa/domains/{domain}
DELETE /api/phoenix-mcp/admin/mesa/domains/{domain}
```

Use these for profiles that cover a device, an area, an integration or a domain. They work like entity profiles, with a different key.

| Level | Key in the response | Key format on save | Error for a bad key |
|---|---|---|---|
| Device | `device_id` | At most 255 characters, none of `<`, `>`, `"`, `'`, `&` or `;` | `adminError.nodeIdTooLong`, `adminError.nodeIdInvalidChars` |
| Area | `area_id` | At most 255 characters, none of `<`, `>`, `"`, `'`, `&` or `;` | `adminError.nodeIdTooLong`, `adminError.nodeIdInvalidChars` |
| Integration | `integration` | A lower-case letter, then lower-case letters, digits and underscores | `adminError.integrationNameInvalid` |
| Domain | `domain` | A lower-case letter, then lower-case letters, digits and underscores | `adminError.badDomain` |

Related: [IDs in paths](guide:topic.adminApi#ids)

### Lists {#scope-lists}

Each list answers with one array named after its level, sorted by key: `{ devices }`, `{ areas }`, `{ integrations }` or `{ domains }`. Each item is `{ <key>, document }`, such as `{ device_id, document }`. These lists don't page.

### Read, save and delete {#scope-profile}

- A read answers `{ <key>, stored }`, with `stored` set to `null` when there's no profile. Only entity reads add `effective` and `explanation`.
- A save takes the same document and `If-None-Match: *` header as [Save an entity profile](guide:#save-profile). It answers `{ <key>, stored }`, without `warnings`.
- A delete answers `{ <key>, "deleted": true }`, also when there was no profile.
- Only saves check the key format.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | the key's error from the table above | The key has the wrong form. Saves only. |
| 412 | `precondition_failed` | `adminError.mesaProfileExists` | You sent `If-None-Match: *` and a profile already exists. |
| 400 | `invalid_request` | none, `message_passthrough` | MESA refused the document. The message is MESA's own. |

### Picker lists {#picker-lists}

These list what you can give a profile to, for a picker in your own app. They work while MESA is off.

- `device-options` answers `{ devices: [{ id, name }] }`, sorted by name. The name is the user's own name for the device, else its device name, else its ID.
- `integration-options` answers `{ integrations: [{ id, name }] }`: every integration that created entities, except Phoenix MCP itself.

## Deployment defaults {#defaults}

```
GET    /api/phoenix-mcp/admin/mesa/defaults
PUT    /api/phoenix-mcp/admin/mesa/defaults
```

Use these to read and set what MESA applies to entities that have no profile at any level. Deployment defaults are three settings, not a profile document.

Related: [The built-in baseline](guide:topic.mesaRules#baseline)

### Read the defaults {#read-defaults}

MESA's own format wraps the settings a second time, so the response nests `deployment_defaults` twice. The outer one is `null` when no defaults are stored:

```json
{
  "deployment_defaults": {
    "deployment_defaults": {
      "default_control_mode": "confirm",
      "triggers_automations_domains": [],
      "domain_overrides": {}
    }
  }
}
```

### Set the defaults {#set-defaults}

Send `{ "deployment_defaults": { ... } }`, or the inner object alone, with only these keys:

| Field | Type | Notes |
|---|---|---|
| `default_control_mode` | string | `autonomous`, `confirm`, `read_only` or `prohibited`. Default `confirm`. |
| `triggers_automations_domains` | array of strings | Domains, as strings. |
| `domain_overrides` | object | Keyed by domain. Each value is an object. |

The response has the same shape as a read.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.deploymentDefaultsInvalid` | MESA refused the defaults. `message_params.detail` says why. |

## Issues and the tag vocabulary {#issues}

```
GET    /api/phoenix-mcp/admin/mesa/vocabulary
GET    /api/phoenix-mcp/admin/mesa/issues
POST   /api/phoenix-mcp/admin/mesa/orphans/clear
```

Use these to read the tags MESA knows, find profiles that disagree with your automations, and clean up profiles left behind by removed items.

### Vocabulary {#vocabulary}

Answers `{ canonical_tags, canonical_roots }`, both sorted. It works while MESA is off.

### Issues, orphans and suggestions {#read-issues}

| Field | Type | Notes |
|---|---|---|
| `refresh` | query, string | Leave it out for the stored lists. `suggestions` recomputes suggestions only. Any other value recomputes everything. |

The response is `{ issues, orphans, orphan_devices, orphan_areas, orphan_integrations, suggestions, dismissed_suggestions }`.

- An issue is `{ entity_id, declared_value, automation_id, role, severity, recommendation }`.
- Suggestions have the same shape, plus a `key` you use to dismiss or restore them.
- The orphan lists name profiles whose entity, device, area or integration no longer exists.

### Clear orphans {#clear-orphans}

Deletes every orphaned entity, device, area and integration profile. Send no body. Phoenix checks the orphans against Home Assistant first, then deletes exactly those profiles.

The response is `{ deleted: { entities, devices, areas, integrations }, count }`.

## Import and export {#portability}

```
GET    /api/phoenix-mcp/admin/mesa/export
POST   /api/phoenix-mcp/admin/mesa/import
```

Use these to copy every MESA profile, and the deployment defaults, to a file and back.

### Export {#export}

Returns every profile as one archive:

```json
{
  "mesa_export": {
    "format_version": "1.1",
    "exported_at": "...",
    "mesa_core_version": "...",
    "entities": {},
    "domains": {},
    "integrations": {},
    "areas": {},
    "devices": {},
    "deployment_defaults": {},
    "unreadable_profiles": []
  }
}
```

Each section maps a profile's key to its document. `deployment_defaults` appears only when defaults are stored. A stored profile that MESA can't read is left out, and its key is listed in `unreadable_profiles`, which appears only then.

### Import {#import}

Checks every profile in an archive and saves the valid ones. Phoenix never saves an invalid profile; it reports it instead. Afterwards it recomputes orphans and suggestions.

| Field | Type | Notes |
|---|---|---|
| `archive` | object | Required. An export, with `mesa_export` at the root and `format_version` `1.0` or `1.1`. |
| `on_conflict` | string | `skip` (default) keeps profiles you already have. `overwrite` replaces them. |

The response is `{ imported, overwritten, skipped_existing, invalid }`:

- `imported` and `overwritten` are counts.
- `skipped_existing` lists the profiles kept because they already existed.
- `invalid` maps each refused profile to the reason.
- Profiles are named `<section>:<key>`, such as `entities:light.kitchen`, or `deployment_defaults`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.mesaArchiveRequired` | `archive` is missing. |
| 400 | `invalid_request` | `adminError.mesaConflictModeInvalid` | `on_conflict` is something other than `skip` or `overwrite`. |
| 400 | `invalid_request` | none, `message_passthrough` | The archive isn't a MESA export, or its `format_version` isn't supported. |

## Suggestions {#suggestions}

```
POST   /api/phoenix-mcp/admin/mesa/suggestions/dismiss
POST   /api/phoenix-mcp/admin/mesa/suggestions/restore
```

Use these to hide a profile suggestion you don't want, or bring dismissed ones back. Read the suggestions themselves from [Issues, orphans and suggestions](guide:#read-issues).

Related: [Suggestions](guide:window.mesaSuggestions)

### Dismiss a suggestion {#dismiss-suggestion}

| Field | Type | Notes |
|---|---|---|
| `key` | string | Required, not empty. The `key` of a suggestion in the current list. |

The response is `{ dismissed, suggestions, dismissed_suggestions }`, where `dismissed` is the key.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.suggestionKeyRequired` | `key` is missing or empty. |
| 404 | `not_found` | `adminError.suggestionNotCurrent` | No current suggestion has this key. |

### Restore suggestions {#restore-suggestion}

Send `key` for one dismissed suggestion, or `all: true` for every one.

| Field | Type | Notes |
|---|---|---|
| `key` | string | The key of one dismissed suggestion. |
| `all` | boolean | `true` restores every dismissed suggestion. |

The response is `{ restored, suggestions, dismissed_suggestions }`. `restored` is the key, or with `all`, how many dismissals were cleared.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.suggestionRestoreTargetRequired` | Neither `key` nor `all: true` was sent. |
| 404 | `not_found` | `adminError.suggestionNotDismissed` | That key isn't dismissed. |
