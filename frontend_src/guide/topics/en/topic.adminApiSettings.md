---
{ "id": "topic.adminApiSettings", "title": "Settings and System Endpoints", "parent": "topic.adminApi", "order": 50, "keywords": ["settings", "configuration", "system", "wipe", "reset", "info", "version", "catalog", "translation"], "sectionKeywords": { "settings": ["PATCH", "settings object"], "settings-read": ["computed fields", "tool_routing_qualified_languages", "esphome_builder"], "settings-change": ["validation", "side effects"], "writable-settings": ["default", "range", "clamped", "kill_switch", "mesa_mode"], "api-only": ["shadow", "tool review", "observe", "advise", "local_shadow", "hybrid", "command_routing_languages", "command_routing_timeout_ms", "decision_shadow_enabled"], "wipe": ["delete everything", "factory reset", "WIPE", "wipe_core", "wipe_providers", "wipe_mesa"], "confirmations": ["confirm", "confirmation"], "info": ["version", "tool count", "home_companion_reach"], "catalog": ["language", "locale", "strings", "translation"], "audit": ["request log", "audit log", "filter"], "conversations": ["Conversation Log", "transcript", "history"], "voice-pipeline": ["Assist pipeline", "voice assistant", "preferred assistant"], "ai-task": ["AI Task", "data generation", "default entity"], "routing": ["command routing", "recognition", "preview", "statistics", "request budget", "decision provider"] }, "covers": ["custom_components/phoenix_mcp/admin_view.py", "custom_components/phoenix_mcp/access_key_store.py", "custom_components/phoenix_mcp/const.py"] }
---
Use these endpoints to read and change Phoenix's global settings, clear its data, and read its logs, version and routing counters. Every endpoint follows the rules in the [Admin API](guide:topic.adminApi) overview.

Related: [Errors](guide:topic.adminApi#errors), [Agent Chat Endpoints](guide:topic.adminApiChat)

## Settings {#settings}

```
GET    /api/phoenix-mcp/admin/settings
PATCH  /api/phoenix-mcp/admin/settings
```

Read and change the settings behind the Settings workspace. For what each setting means to an operator, see its window page: [General](guide:window.settingsGeneral), [Safety](guide:window.settingsSafety), [Logs](guide:window.settingsLogs), [Agent Chat](guide:window.settingsChat), [Home Assistant](guide:window.settingsHome) and [AI providers](guide:window.settingsProviders).

### Read settings {#settings-read}

`GET /api/phoenix-mcp/admin/settings` returns every [writable setting](guide:#writable-settings) plus these read-only fields:

- `tool_routing_qualified_languages`
- `assist_api_supported`
- `voice_agent_pipeline_supported`
- `voice_agent_pipeline_preferred`
- `voice_agent_pipeline_id`, set only by the [Voice Agent assistant](guide:#voice-pipeline) routes
- `ai_task_supported`
- `esphome_integration`, `esphome_builder` and `esphome_builder_live`

### Change settings {#settings-change}

`PATCH /api/phoenix-mcp/admin/settings` takes any subset of the writable settings. No setting needs a confirmation field, not even `kill_switch`.

Phoenix checks every value before it writes anything. The first problem rejects the whole request, and nothing changes.

An accepted change has these effects:

- It cancels decision work in progress, and invalidates routing results and command approvals still in flight.
- Turning `access_key_presets_enabled` on gives every key a Default preset.
- Turning `kill_switch` off opens the agent routes again.
- Phoenix applies `mesa_mode` straight away, and updates the Voice Agent, AI Task and the Assist API.
- `mesa_inject_enabled` and `agentcli_global` take effect on the next full page load.

The audit log records the change with the names of the changed settings, not their values. The response is the same as for a read. It adds `voice_agent_pipeline_kept: true` when the Voice Agent is no longer fully set up, but Home Assistant kept Phoenix's assistant as its only one.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.unknownFields` | A field isn't writable, such as `voice_agent_pipeline_id`. `message_params` has `fields`. |
| 400 | `invalid_request` | `adminError.booleanRequired` | A boolean setting got another type. `message_params` has `field`. |
| 400 | `invalid_request` | `adminError.valueAllowed` | A choice setting got another value: `mesa_mode`, a style, a detail level, a routing mode or an audit menu. `message_params` has `field` and `values`. |
| 400 | `invalid_request` | `adminError.integerRequired` | An integer setting got another type. `message_params` has `field`. |
| 400 | `invalid_request` | `adminError.routingLanguagesInvalid` | `command_routing_languages` names a language that isn't an installed Assist language. |
| 400 | `invalid_request` | `adminError.conversationLimitInvalid` | A Conversation Log limit, `command_routing_timeout_ms`, `decision_timeout_ms` or `decision_request_budget` is out of range. `message_params` has `field`, `min` and `max`. |
| 400 | `invalid_request` | `adminError.accessKeyIdOrNull` | An access key setting isn't a string or `null`. `message_params` has `field`. |
| 400 | `invalid_request` | `adminError.activeAccessKeyRequired` | An access key setting doesn't name an active key. `message_params` has `field`. |
| 400 | `invalid_request` | `adminError.stringOrNull` | A model setting isn't a string or `null`. `message_params` has `field`. |
| 400 | `invalid_request` | `adminError.configuredProviderRequired` | A provider setting names no existing account. `message_params` has `field`. |
| 400 | `invalid_request` | `adminError.decisionProviderRequired` | A decision feature was turned on with no decision account. |
| 400 | `invalid_request` | `adminError.toolRoutingUnqualified` | A tool review mode was set to `advise` for a model with no qualified language. |

### Writable settings {#writable-settings}

"Rejected" means an out-of-range value answers `400`. "Clamped" means Phoenix moves it to the nearest bound. A "decision account" is an account of the decision kind, and a "conversation account" any other.

| Setting | Type and range | Default | Notes |
|---|---|---|---|
| `kill_switch` | boolean | `false` | Turning it off opens the agent routes. While on, the chat route answers `503`. See [Kill switch](guide:window.settingsSafety#kill-switch). |
| `disable_all_logging` | boolean | `false` | See [Logs](guide:window.settingsLogs#log-disable-all). |
| `log_allowed` | boolean | `true` | See [Logs](guide:window.settingsLogs#log-allowed). |
| `log_denied` | boolean | `true` | See [Logs](guide:window.settingsLogs#log-denied). |
| `log_rate_limited` | boolean | `true` | See [Logs](guide:window.settingsLogs#log-rate-limited). |
| `log_entity_names` | boolean | `true` | See [Logs](guide:window.settingsLogs#log-entity-ids). |
| `log_client_ip` | boolean | `true` | See [Logs](guide:window.settingsLogs#log-client-ip). |
| `notify_on_rate_limit` | boolean | `false` | See [Logs](guide:window.settingsLogs#notify-rate-limit). |
| `notify_on_approval` | boolean | `true` | See [Logs](guide:window.settingsLogs#notify-approval). |
| `audit_flush_interval` | integer: 0, 5, 10, 15, 30 or 60 minutes | 15 | Rejected otherwise. Numeric strings are accepted. See [Logs](guide:window.settingsLogs#log-flush). |
| `audit_log_maxlen` | integer: 100, 1000, 5000 or 10000 | 10000 | Rejected otherwise. Numeric strings are accepted. See [Logs](guide:window.settingsLogs#log-max-entries). |
| `conversation_log_enabled` | boolean | `false` | See [Logs](guide:window.settingsLogs#conversation-log). |
| `conversation_log_max_entries` | integer, 1 to 2000 | 100 | Rejected. Needs a JSON integer. See [Logs](guide:window.settingsLogs#conversation-entries). |
| `conversation_log_max_days` | integer, 1 to 365 | 7 | Rejected. Needs a JSON integer. See [Logs](guide:window.settingsLogs#conversation-days). |
| `conversation_log_max_mb` | integer, 1 to 250 (decimal MB) | 25 | Rejected. Needs a JSON integer. See [Logs](guide:window.settingsLogs#conversation-size). |
| `mesa_mode` | `off`, `advisory` or `enforced` | `advisory` | See [Safety](guide:window.settingsSafety#mesa-mode). |
| `mesa_inject_enabled` | boolean | `false` | Takes effect on the next full page load. See [General](guide:window.settingsGeneral#inject). |
| `access_key_presets_enabled` | boolean | `false` | Turning it on gives every key a Default preset. See [General](guide:window.settingsGeneral#presets). |
| `agentcli_global` | boolean | `true` | Takes effect on the next full page load. See [Agent Chat](guide:window.settingsChat#chat-global). |
| `agentcli_scrollback_lines` | integer, 0 to 5000 | 100 | Clamped. Numeric strings are accepted. See [Agent Chat](guide:window.settingsChat#chat-memory). |
| `agentcli_max_iterations` | integer, 3 to 100 | 20 | Clamped. See [Agent Chat](guide:window.settingsChat#chat-steps). |
| `agentcli_conversation_style` | `direct`, `warm`, `calm_guide`, `lively` or `technical` | `direct` | See [Agent Chat](guide:window.settingsChat#chat-style). |
| `agentcli_detail_level` | `concise`, `balanced` or `detailed` | `concise` | See [Agent Chat](guide:window.settingsChat#chat-detail). |
| `agentcli_home_focused` | boolean | `false` | A preference, not a security control. See [Agent Chat](guide:window.settingsChat#chat-home-focused). |
| `agentcli_routing_mode` | `off`, `auto`, `local` or `hybrid` | `off` | The panel writes only `off` and `auto`. `hybrid` needs a decision account. See [Agent Chat](guide:window.settingsChat#chat-commands) and [API-only settings](guide:#api-only). |
| `agentcli_routing_shadow_enabled` | boolean | `false` | API only. See [API-only settings](guide:#api-only). |
| `voice_agent_routing_mode` | `off`, `auto`, `local_shadow`, `local` or `hybrid` | `off` | The panel writes only `off` and `auto`. See [Home Assistant](guide:window.settingsHome#voice-commands) and [API-only settings](guide:#api-only). |
| `command_routing_languages` | list of routing language tags, no duplicates | `["en"]` | API only. An empty list pauses recognition in the modes that use it. See [API-only settings](guide:#api-only). |
| `command_routing_timeout_ms` | integer, 50 to 5000 | 500 | API only. Rejected. Needs a JSON integer. |
| `decision_provider_id` | decision account ID or `null` | `null` | The decision account in use. Setting it also sets the default. `null` turns off shadow observation, all tool review and every `hybrid` mode. See [AI providers](guide:window.settingsProviders#decision-provider). |
| `decision_provider_default_id` | decision account ID or `null` | `null` | The chosen default, kept while the decision provider is off. Setting it while an account is in use switches to it. |
| `decision_shadow_enabled` | boolean | `false` | API only. Turning it on needs a decision account. See [API-only settings](guide:#api-only). |
| `decision_timeout_ms` | integer, 250 to 30000 | 5000 | Rejected. Needs a JSON integer. See [AI providers](guide:window.settingsProviders#decision-timeout). |
| `decision_request_budget` | integer, 0 to 100000 per hour | 0 | 0 means no limit. Rejected. Needs a JSON integer. See [AI providers](guide:window.settingsProviders#decision-request-limit). |
| `agentcli_tool_routing_mode` | `off`, `observe` or `advise` | `off` | API only. See [API-only settings](guide:#api-only). |
| `voice_agent_tool_routing_mode` | `off`, `observe` or `advise` | `off` | API only. See [API-only settings](guide:#api-only). |
| `ai_task_tool_routing_mode` | `off`, `observe` or `advise` | `off` | API only. See [API-only settings](guide:#api-only). |
| `provider_catalog_refresh_enabled` | boolean | `true` | See [AI providers](guide:window.settingsProviders#provider-checks). |
| `provider_catalog_refresh_hours` | integer, 6 to 720 | 24 | Clamped. See [AI providers](guide:window.settingsProviders#provider-checks-hours). |
| `assist_bound_access_key_id` | active access key ID or `null` | `null` | An empty string also clears it. See [Home Assistant](guide:window.settingsHome#assist-access-key). |
| `voice_agent_enabled` | boolean | `false` | See [Home Assistant](guide:window.settingsHome#voice-enable). |
| `voice_agent_access_key_id` | active access key ID or `null` | `null` | See [Home Assistant](guide:window.settingsHome#voice-access-key). |
| `voice_agent_provider_id` | conversation account ID or `null` | `null` | See [Home Assistant](guide:window.settingsHome#voice-provider). |
| `voice_agent_model` | string or `null` | `null` | Phoenix doesn't check it against the provider. See [Home Assistant](guide:window.settingsHome#voice-model). |
| `voice_agent_conversation_style` | as `agentcli_conversation_style` | `direct` | See [Home Assistant](guide:window.settingsHome#voice-style). |
| `voice_agent_detail_level` | as `agentcli_detail_level` | `concise` | See [Home Assistant](guide:window.settingsHome#voice-detail). |
| `voice_agent_home_focused` | boolean | `false` | See [Home Assistant](guide:window.settingsHome#voice-home-focused). |
| `ai_task_enabled` | boolean | `false` | See [Home Assistant](guide:window.settingsHome#ai-task-enable). |
| `ai_task_access_key_id` | active access key ID or `null` | `null` | See [Home Assistant](guide:window.settingsHome#ai-task-access-key). |
| `ai_task_provider_id` | conversation account ID or `null` | `null` | See [Home Assistant](guide:window.settingsHome#ai-task-provider). |
| `ai_task_model` | string or `null` | `null` | See [Home Assistant](guide:window.settingsHome#ai-task-model). |
| `ai_task_conversation_style` | as `agentcli_conversation_style` | `direct` | See [Home Assistant](guide:window.settingsHome#ai-task-style). |
| `ai_task_detail_level` | as `agentcli_detail_level` | `balanced` | See [Home Assistant](guide:window.settingsHome#ai-task-detail). |

## Settings without a panel control {#api-only}

The panel has no control for these settings and values. Set them only through the API.

- `agentcli_routing_mode` `local` and `hybrid`: the panel shows either as on. `hybrid` needs a decision account.
- `voice_agent_routing_mode` `local_shadow`, `local` and `hybrid`. `auto` itself works as `hybrid` while a decision account is in use, and as `local` otherwise.
- `agentcli_routing_shadow_enabled`: runs Agent Chat command recognition as observation only, while chat routing is off. Any change that sets either routing mode to `off` or `auto` turns it off.
- `command_routing_languages`: the languages for `local_shadow`, `local`, `hybrid` and background observation, from the installed Assist sentence languages plus `zh-Hans` and `zh-Hant`. `auto` uses every installed Assist language instead.
- `command_routing_timeout_ms`: how long local recognition may take, in milliseconds.
- `decision_shadow_enabled`: sends Voice Agent commands that recognition missed to the decision provider, as observation only. It works only with `voice_agent_routing_mode` `local` or `local_shadow`.

The three tool review modes ask the decision provider to check each tool call the model proposes, before it runs. They are `agentcli_tool_routing_mode`, `voice_agent_tool_routing_mode` and `ai_task_tool_routing_mode`.

- `observe` and `advise` need a decision account.
- `advise` also needs a qualified language. `tool_routing_qualified_languages` lists them; while it's empty, Phoenix refuses `advise` with `adminError.toolRoutingUnqualified`.
- Home Companion keys skip tool review.

Related: [How a command is routed](guide:topic.commandRouting)

## Wiping data {#wipe}

```
DELETE /api/phoenix-mcp/admin/wipe
```

Deletes Phoenix's data, by scope. This can't be undone.

```json
{"confirm": "WIPE", "wipe_core": true, "wipe_providers": true, "wipe_mesa": false}
```

| Field | Type | Notes |
|---|---|---|
| `confirm` | string | Required. Exactly `"WIPE"`. |
| `wipe_core` | boolean | Default `true`. |
| `wipe_providers` | boolean | Default `true`. |
| `wipe_mesa` | boolean | Default `false`. |

A missing flag, or one that isn't a boolean, uses its default. So `{"confirm": "WIPE"}` alone wipes core data and provider accounts.

`wipe_core` clears:

- Access keys, archived keys, entity hints, and every setting back to its default.
- Pending approvals. Phoenix dismisses their notifications and reports them resolved.
- The audit log, the Conversation Log, version history and configuration recovery records.
- Routing statistics, the decision request count, the card catalog cache and routing clarifications.
- Rate limit counts, request counters and notification markers.
- Per-key sensors and expiry timers. Phoenix restores any loggers it had changed for a set time.
- The Phoenix Assist pipeline.

Afterwards, Phoenix applies each changed default, such as MESA mode, the Voice Agent, AI Task and the modules added to Home Assistant's pages. It opens the agent routes if they were off.

`wipe_providers` clears every Agent Chat and decision account, with their API keys. It also clears the settings that use them. That's the Voice Agent and AI Task provider and model, and the decision account in use and its default. Shadow observation, tool review and every `hybrid` mode turn off too.

`wipe_mesa` clears every MESA profile and dismissed suggestion.

The response is `204` with no body.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.wipeConfirmationRequired` | `confirm` isn't exactly `"WIPE"`. |
| 409 | `conflict` | `adminError.approvalWipeInProgress` | An approval is running. Wait for it to finish. Only with `wipe_core`. |
| 409 | `conflict` | `adminError.approvalWipeExecutionUncertain` | An approval may have applied without reporting back. Check its result first. Only with `wipe_core`. |

Related: [Safety settings](guide:window.settingsSafety#data-management)

### Confirmation fields {#confirmations}

Only three routes ask for a confirmation in the body. The settings PATCH asks for none.

| Route | Body |
|---|---|
| [Wipe data](guide:#wipe) | `{"confirm": "WIPE"}` plus optional scope flags |
| [Clear the Conversation Log](guide:#clear-conversations) | Exactly `{"confirm": true}` |
| [Reset routing statistics](guide:#reset-statistics) | Exactly `{"surface": "voice", "confirm": true}`, or `"chat"` |

## Version and tools {#info}

```
GET    /api/phoenix-mcp/admin/info
```

Returns Phoenix's version and what it offers agents.

| Field | Notes |
|---|---|
| `version` | Phoenix's version. |
| `min_ha_version` | The oldest Home Assistant version Phoenix supports. |
| `github_url` | The project's repository. |
| `tool_count` | `total`, `native` and `additional`, counted from the tools running now. |
| `home_companion_reach` | `control_domains`, `read_domains` and `sensor_classes` for Home Companion keys. |

## Panel strings {#catalog}

```
GET    /api/phoenix-mcp/admin/catalog/{language}
```

Returns the panel's strings for one language. It answers while Phoenix is still starting, unlike every other endpoint.

The response is `{"language": "<code>", "resources": {...}}`, with flat dotted keys. Phoenix picks the closest language it ships, then falls back to English. An unknown language answers English with `"language": "en"`. Strings that aren't translated, or whose placeholders don't match, come back in English.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.catalogNotFound` | No catalog was found. This happens only if the English catalog is missing. |

## Audit log {#audit}

```
GET    /api/phoenix-mcp/admin/audit
```

Pages through the log of agent requests, newest first.

| Field | Type | Notes |
|---|---|---|
| `limit` | integer | Default 100, at most 500. |
| `offset` | integer | See [Request bodies](guide:topic.adminApi#bodies). |
| `access_key_id` | string | Exact match. |
| `outcome` | string | Exact match: `allowed`, `denied`, `not_found`, `rate_limited`, `not_implemented`, `invalid_request` or `pending_approval`. |
| `ip` | string | Part of the client address. |
| `method` | string | Part of the method, any case. |
| `resource` | string | Part of the resource, any case. |
| `request_id` | string | Exact match. |
| `since` | string | An ISO time. A time without a zone counts as local time. |

The response has `entries` and `total`. Each entry has `request_id`, `timestamp`, `access_key_id`, `access_key_name`, `method`, `resource`, `outcome`, `client_ip` and `pass_through`. It may also have `payload`, `mesa_advisory`, `preset`, `stale_tools_advisory`, `ha_user_id` and `originating_access_key_id`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination`, `adminError.paginationNonNegative` | `limit` or `offset` isn't a whole number of 0 or more. |
| 400 | `invalid_request` | `adminError.sinceTimestampInvalid` | `since` isn't a valid time. |
| 400 | `invalid_request` | `adminError.outcomeFilterUnknown` | `outcome` isn't one of the values above, or is empty. `message_params` has `value`. |

Related: [{{label:shell.tabAudit}}](guide:window.auditLog), [Log records](guide:topic.logRecords)

## Conversation Log {#conversations}

```
GET    /api/phoenix-mcp/admin/conversations
DELETE /api/phoenix-mcp/admin/conversations
GET    /api/phoenix-mcp/admin/conversations/{entry_id}
```

Read and clear the recorded conversations. Turn recording on with `conversation_log_enabled`.

Related: [Conversation Log](guide:window.conversationLog)

### List conversations {#list-conversations}

`GET /api/phoenix-mcp/admin/conversations`

| Field | Type | Notes |
|---|---|---|
| `limit` | integer | Default 25, at most 100. |
| `offset` | integer | See [Request bodies](guide:topic.adminApi#bodies). |
| `search` | string | At most 512 characters. Matches recorded content, any case. |
| `source` | string | At most 64 characters: `mcp`, `agentcli`, `assist`, `voice`, `ai_task` or `home_companion`. |
| `outcome` | string | At most 64 characters. A status value. `failed` and `denied` also match turns with mixed results. |
| `since` | string | An ISO time. A time without a zone counts as local time. |
| `grouped` | string | Set to `true` for grouped rows. |

The response has `entries`, `total`, `enabled`, `available`, `bytes` and `max_bytes`. Entries are newest first.

Each entry has `id`, `source`, `started_at`, `updated_at`, `finished_at`, `coverage`, `status` and `execution_status`. It also has `response_status`, `tool_count`, `change_count`, `omissions`, `omitted_events`, `first_tool`, `excerpt` and `prompt_attachments`.

Grouped entries also have `latest_at`. Agent Chat threads add `turns`, oldest first, and `history_missing`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badPagination`, `adminError.paginationNonNegative` | `limit` or `offset` isn't a whole number of 0 or more. |
| 400 | `invalid_request` | `adminError.sinceTimestampInvalid` | `since` isn't a valid time. |

### Open a conversation {#open-conversation}

`GET /api/phoenix-mcp/admin/conversations/{entry_id}` returns the full stored record, with its events and the reasons anything was left out.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.conversationNotFound` | No conversation has this ID, or it has expired. |

### Clear the Conversation Log {#clear-conversations}

`DELETE /api/phoenix-mcp/admin/conversations` takes exactly `{"confirm": true}` and answers `{"cleared": true}`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.conversationClearConfirm` | The body isn't exactly `{"confirm": true}`. |
| 503 | `service_unavailable` | `adminError.conversationStorageUnavailable` | The log was cleared in memory, but saving failed. Try again. |

## Voice Agent assistant {#voice-pipeline}

```
POST   /api/phoenix-mcp/admin/voice_agent/pipeline
DELETE /api/phoenix-mcp/admin/voice_agent/pipeline
POST   /api/phoenix-mcp/admin/voice_agent/pipeline/preferred
DELETE /api/phoenix-mcp/admin/voice_agent/pipeline/preferred
```

Create and remove a Home Assistant assistant that uses the Phoenix Voice Agent, and choose whether it's the preferred one.

Related: [Home Assistant settings](guide:window.settingsHome#voice-assistant)

### Create the assistant {#create-pipeline}

`POST /api/phoenix-mcp/admin/voice_agent/pipeline`

| Field | Type | Notes |
|---|---|---|
| `preferred` | boolean | Optional. Default `true`. Makes the assistant Home Assistant's preferred one. |

Set up the Voice Agent fully first: enabled, with an access key, a provider and a model. If Phoenix's assistant still exists, Phoenix reuses it.

The response has `pipeline_id`, `name` and `preferred`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.booleanRequired` | `preferred` isn't a boolean. |
| 404 | `not_found` | `adminError.setupMissing` | Phoenix isn't set up. |
| 400 | `invalid_request` | `adminError.assistPipelineUnavailable` | Home Assistant has no assistant support. |
| 400 | `invalid_request` | `adminError.voiceAgentNotConfigured` | The Voice Agent isn't fully set up and turned on. |
| 400 | `invalid_request` | `adminError.assistPipelineFailed` | Creating the assistant failed. |

### Remove the assistant {#remove-pipeline}

`DELETE /api/phoenix-mcp/admin/voice_agent/pipeline` moves the preference to another assistant if needed, then deletes Phoenix's. The response is `{"ok": true, "kept": false}`.

`kept` is `true` when Home Assistant still has the assistant, because it's the only one or deleting it failed.

### Preferred assistant {#preferred-pipeline}

`POST` makes Phoenix's assistant the preferred one, and `DELETE` stops it being preferred but keeps it. Neither takes a body. The response has `pipeline_id` and `preferred`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.assistPipelineUnavailable` | Home Assistant has no assistant support. |
| 400 | `invalid_request` | `adminError.assistantSetupFirst` | There's no Phoenix assistant yet. |
| 400 | `invalid_request` | `adminError.assistantOnlyOneStaysPreferred` | Phoenix's is the only assistant, so it stays preferred. |
| 400 | `invalid_request` | `adminError.assistantPreferredFailed` | Saving the preference failed. |

## AI Task default {#ai-task}

```
GET    /api/phoenix-mcp/admin/ai_task/preferred
POST   /api/phoenix-mcp/admin/ai_task/preferred
DELETE /api/phoenix-mcp/admin/ai_task/preferred
```

Read and set Home Assistant's default data generation entity. `POST` makes Phoenix's AI Task entity the default, replacing any other. `DELETE` clears the default only if it's Phoenix's.

All three answer with the same fields:

| Field | Notes |
|---|---|
| `supported` | Whether Home Assistant supports AI Task. |
| `entity_id` | Phoenix's AI Task entity, or `null` until AI Task is fully set up. |
| `gen_data_entity_id` | The current default entity. |
| `gen_data_name` | The current default's name. |
| `is_preferred` | Whether Phoenix's entity is the default. |

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.setupMissing` | Phoenix isn't set up. |
| 400 | `invalid_request` | `adminError.aiTaskNotConfigured` | `POST` only. Phoenix's AI Task isn't fully set up and turned on. |
| 400 | `invalid_request` | `adminError.aiTaskPreferencesUnavailable` | `POST` only. Home Assistant has nowhere to save the default. |
| 400 | `invalid_request` | `adminError.aiTaskPreferredFailed` | `POST` only. Saving the default failed. |

Related: [Home Assistant settings](guide:window.settingsHome#ai-task-default)

## Command routing {#routing}

```
GET    /api/phoenix-mcp/admin/command-routing/preview
POST   /api/phoenix-mcp/admin/command-routing/preview
GET    /api/phoenix-mcp/admin/command-routing/statistics
DELETE /api/phoenix-mcp/admin/command-routing/statistics
```

Test how Phoenix would route a sentence, and read the routing counters and totals.

Related: [How a command is routed](guide:topic.commandRouting), [Reading routing statistics](guide:topic.routingStatistics)

### Live counters {#routing-counters}

`GET /api/phoenix-mcp/admin/command-routing/preview` returns counters kept in memory. They start again from zero when Phoenix restarts.

| Field | Notes |
|---|---|
| `observations`, `candidates`, `reasons`, `mean_elapsed_ms`, `agent_chat` | Voice recognition counters. |
| `decision_shadow` | Decision counters: `observations`, `reasons`, `choices`, `busy`, `requests`, `tool_routing`, `mean_elapsed_ms`, `hybrid`, `chat_hybrid` and `agent_chat`. |
| `request_budget` | `limit`, `used`, `remaining` (`null` when unlimited), `reset_at` and `available`. `reset_at` is in Unix seconds, at the start of the next UTC hour. |

### Test a sentence {#routing-preview}

`POST /api/phoenix-mcp/admin/command-routing/preview` shows how Phoenix would route one sentence, without running it. It counts no observation.

| Field | Type | Notes |
|---|---|---|
| `access_key_id` | string | Required. An active access key. |
| `sentence` | string | Required. Not blank, at most 512 characters. |
| `language` | string | Default `en`. Any installed Assist language, including regional and Chinese script variants. |
| `decision` | boolean | Also asks the decision provider. |
| `targets` | boolean | Needs `decision`. |
| `batch` | boolean | Needs `decision` and `targets`, and no `messages`. |
| `numeric` | boolean | Needs `batch`. |
| `messages` | list | Earlier turns, for history review. Needs `decision` and no `targets`. |
| `references` | boolean | Needs `messages`. |

No other fields are allowed. Any option that needs `decision` contacts the decision account and counts against `decision_request_budget`.

The response has `route` (`local_candidate` or `provider`), `reason`, `proposal`, `executed: false`, `elapsed_ms`, `voice_observations` and `decision_observations`. Depending on the options, it also has `decision_shadow`, `batch_review` or `context_review`.

If the key or the settings change during the test, the result is `route: provider` with `reason: authority_changed`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.routingPreviewInvalid` | A field is missing, too long, of the wrong type, not allowed, or in a combination that isn't allowed. |
| 400 | `invalid_request` | `adminError.activeAccessKeyRequired` | `access_key_id` doesn't name an active key. `message_params` has `field`. |

### Routing statistics {#routing-statistics}

`GET /api/phoenix-mcp/admin/command-routing/statistics` returns saved totals, kept across restarts. The response has `voice` and `chat`, each with:

- `since`
- `outcomes`: `local`, `decision`, `provider`, `pending`, `failed` and `cancelled`
- `provider_turns`
- `decision_usage` and `provider_usage`, each with `requests`, `reports`, `input_tokens` and `output_tokens`

### Reset statistics {#reset-statistics}

`DELETE /api/phoenix-mcp/admin/command-routing/statistics` resets one surface's totals:

```json
{"surface": "voice", "confirm": true}
```

Set `surface` to `voice` or `chat`. The response shows both surfaces after the reset. The decision request count isn't reset.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.routingStatisticsResetInvalid` | The body isn't exactly a valid `surface` with `confirm: true`. |
