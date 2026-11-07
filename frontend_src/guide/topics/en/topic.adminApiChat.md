---
{"id": "topic.adminApiChat", "title": "Agent Chat Endpoints", "parent": "topic.adminApi", "order": 40, "keywords": ["provider", "AI provider", "account", "model", "chat", "agent turn", "SSE", "stream"], "sectionKeywords": {"providers": ["provider account", "instance", "instance_id"], "provider-kinds": ["Anthropic", "OpenAI", "Claude", "ChatGPT", "Gemini", "Ollama", "OpenRouter", "Infron", "Qwen", "TypeSafe", "JEV", "decision provider", "endpoint_id", "region"], "setup-fields": ["api_key", "base_url", "endpoint_id", "API key"], "list-providers": ["provider_types", "purpose", "capabilities"], "change-provider": ["automatic model", "auto", "model_mode"], "refresh-models": ["refresh", "capabilities"], "probe-model": ["probe", "options", "effort", "vision"], "check-setup": ["probe", "test connection"], "chat": ["agentcli/chat", "turn", "access_key_id"], "chat-options": ["thinking", "effort", "temperature", "max_tokens", "show_thinking"], "chat-images": ["attachments", "image", "JPEG", "PNG", "GIF", "base64"], "events": ["keepalive", "text/event-stream", "Server-Sent Events", "ready", "status", "thinking_delta", "assistant_delta", "focus_declined", "tool_call", "tool_progress", "approval_required", "approval_resolved", "tool_image", "tool_result", "usage", "notice", "continue_required", "error", "messages", "done", "event stream", "SSE"]}, "covers": ["custom_components/phoenix_mcp/agent_chat_views.py", "custom_components/phoenix_mcp/agent_chat.py", "custom_components/phoenix_mcp/agent_turn.py"]}
---
Use these endpoints to manage the AI provider accounts Phoenix uses and to run Agent Chat turns from your own code. Every endpoint follows the rules in the [Admin API](guide:topic.adminApi) overview.

Related: [{{label:agentchat.title}}](guide:window.agentChat), [AI provider settings](guide:window.settingsProviders), [Errors](guide:topic.adminApi#errors)

## Provider accounts {#providers}

```
GET    /api/phoenix-mcp/admin/agentcli/providers
POST   /api/phoenix-mcp/admin/agentcli/providers
PATCH  /api/phoenix-mcp/admin/agentcli/providers/{instance_id}
DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id}
GET    /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models
POST   /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh
POST   /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe
POST   /api/phoenix-mcp/admin/agentcli/probe
```

Add, check and remove the accounts Phoenix uses to reach AI providers. Conversation accounts serve Agent Chat, the Voice Agent and AI Task. Decision accounts serve the decision provider.

### Provider kinds {#provider-kinds}

Every account has a `kind`. Kinds with endpoints take an `endpoint_id`; the first value listed is the default.

| Kind | Purpose | Setup fields |
|---|---|---|
| `claude` (Anthropic) | conversation | `api_key` |
| `deepseek` | conversation | `api_key` |
| `chatgpt` (OpenAI) | conversation | `api_key`, `endpoint_id`: `global`, `us`, `eu`, `ae`, `au`, `ca`, `jp`, `in`, `sg`, `kr`, `gb` |
| `gemini` | conversation | `api_key` |
| `grok` | conversation | `api_key`, `endpoint_id`: `global`, `us` |
| `groq` | conversation | `api_key` |
| `kimi` | conversation | `api_key`, `endpoint_id`: `global`, `china` |
| `meta` | conversation | `api_key` |
| `minimax` | conversation | `api_key`, `endpoint_id`: `global`, `china` |
| `mistral` | conversation | `api_key`, `endpoint_id`: `global`, `eu`, `us` |
| `nvidia` | conversation | `api_key` |
| `mimo` (Xiaomi MiMo) | conversation | `api_key`, `endpoint_id`: `payg`, `token_plan`, `token_plan_sgp`, `token_plan_ams` |
| `tencent` (Tencent Cloud TokenHub) | conversation | `api_key`, `endpoint_id`: `singapore`, `guangzhou`, `silicon_valley` |
| `opencode` | conversation | `api_key`, `endpoint_id`: `zen`, `console` |
| `ollama_cloud` | conversation | `api_key` |
| `ollama` (local) | conversation | `base_url` |
| `openrouter` | conversation | `api_key` |
| `infron` | conversation | `api_key` |
| `together` | conversation | `api_key` |
| `cerebras` | conversation | `api_key` |
| `fireworks` | conversation | `api_key` |
| `qwen` | conversation | `api_key`, `base_url` |
| `zai` | conversation | `api_key`, `endpoint_id`: `standard`, `coding`, `china` |
| `typesafe` (TypeSafe) | decision | `api_key`, `model` |

### Setup fields {#setup-fields}

Adding an account and checking a setup take the same body.

| Field | Type | Notes |
|---|---|---|
| `kind` | string | Required. One of the kinds above. |
| `api_key` | string | Required for every kind except `ollama`. At most 4096 characters, with no control characters. Phoenix never returns it. |
| `base_url` | string | Required for `ollama` and `qwen`. Only `ollama` may use a private address. Every other kind uses a fixed endpoint and ignores this field. |
| `endpoint_id` | string | Only for kinds with endpoints. Leave it out for the default. |
| `model` | string | Optional for conversation kinds; leave it empty to follow the provider automatically. Required for `typesafe`: at most 200 characters, and in the provider's current list. |

### List accounts {#list-providers}

`GET /api/phoenix-mcp/admin/agentcli/providers`

| Field | Type | Notes |
|---|---|---|
| `purpose` | query string | `conversation` (default) or `decision`. Any other value returns empty lists, not an error. |

The response has `instances`, the accounts, and `provider_types`, the setup forms. Both are sorted by label.

Each entry in `provider_types` has `kind`, `label`, `label_key` and `fields`. Each field has `id`, `type` (`secret`, `url` or `choice`), `label_key` and `required`. It may also have `placeholder` and `choices`, each choice with `value`, `label` and `label_key`.

Each account in `instances` has these fields. The API key is never among them.

| Field | Notes |
|---|---|
| `id` | The account's ID, used as `{instance_id}`. |
| `kind` | The provider kind. |
| `name` | Made by Phoenix. It adds a distinguishing part only when one kind has several accounts. |
| `model` | The model a request uses now. |
| `model_mode` | `fixed` or `auto`. Decision accounts are always `fixed`. |
| `model_replaced_from` | A chosen model the provider has retired, or `null`. |
| `models_checked_at` | When Phoenix last read the model list. |
| `base_url` | As set up. |
| `endpoint_id` | As set up. |
| `capabilities` | Per model: what the provider declares, what a probe found, and options the provider refused in the last 30 days. |
| `capabilities_checked_at` | When Phoenix last checked capabilities. |

### Add an account {#add-provider}

`POST /api/phoenix-mcp/admin/agentcli/providers` takes the [setup fields](guide:#setup-fields).

For a conversation kind, Phoenix runs the provider's credential check. For a decision kind, it loads the model list and requires your model to be in it. If that passes, Phoenix stores the account and tries to read its model list and declared capabilities.

The response is `201` with `{"instance": {...}}`, holding the account fields above.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.providerUnknown` | `kind` isn't a known kind. |
| 400 | `invalid_request` | `adminError.providerApiKeyRequired` | `api_key` is missing. |
| 400 | `invalid_request` | `adminError.providerEndpointInvalid` | `endpoint_id` isn't one of the kind's values. |
| 400 | `invalid_request` | `adminError.ollamaUrlRequired`, `adminError.providerBaseUrlRequired` | A required `base_url` is missing. |
| 400 | `invalid_request` | `adminError.providerUrlInvalid`, `adminError.providerUrlScheme`, `adminError.providerRemoteUrlScheme`, `adminError.providerUrlHostRequired`, `adminError.providerUrlCredentialsForbidden`, `adminError.providerUrlFragmentForbidden`, `adminError.providerUrlAddressForbidden`, `adminError.providerUrlResolutionForbidden`, `adminError.providerRemoteUrlAddressForbidden`, `adminError.providerRemoteUrlResolutionForbidden`, `adminError.providerRemoteUrlResolutionFailed` | `base_url` isn't an acceptable URL. |
| 400 | `invalid_request` | `adminError.providerConnectionFailed`, or a key for the provider's own failure | The credential check failed. `adminError.providerConnectionFailed` comes with a `detail`. |
| 400 | `invalid_request` | `adminError.providerModelRequired` | A decision kind has no `model`. |
| 400 | `invalid_request` | `adminError.providerModelNotListed` | The model isn't in the provider's list. |
| 400 | `invalid_request` | `adminError.decisionProviderRateLimited`, `adminError.decisionProviderTimeout`, `adminError.decisionProviderUnreachable`, `adminError.decisionProviderUnreadable`, `adminError.providerKeyRejected`, `adminError.decisionProviderCheckFailed` | The decision provider check failed: rate limited, timed out, unreachable, unreadable, key rejected, or another failure. |
| 409 | `already_exists` | `adminError.providerAlreadyConfigured` | An account with this setup already exists. |

### Change the model {#change-provider}

`PATCH /api/phoenix-mcp/admin/agentcli/providers/{instance_id}`

| Field | Type | Notes |
|---|---|---|
| `model` | string | The model to use. Conversation accounts accept any model ID. Decision accounts need one the provider lists now. |
| `auto` | boolean | `true` follows the provider's model automatically. Decision accounts refuse it. |

Send `model` or `auto: true`, not both, and no other fields. With `auto: true`, Phoenix reads the model list straight away, so the response shows the model it picked.

The response is `200` with only `{"instance": {"id": ..., "model": ..., "model_mode": ...}}`, not the full account.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.unknownFields` | The body has fields other than `model` and `auto`. |
| 400 | `invalid_request` | `adminError.booleanRequired` | `auto` isn't a boolean. |
| 400 | `invalid_request` | `adminError.providerModelOrAuto` | Both `model` and `auto: true` were sent. |
| 400 | `invalid_request` | `adminError.providerModelRequired` | Neither was sent. |
| 400 | `invalid_request` | `adminError.decisionModelRequired` | `auto: true` went to a decision account, which needs a specific model. |
| 400 | `invalid_request` | `adminError.providerModelNotListed` and the decision provider errors under [Add an account](guide:#add-provider) | A decision account's model isn't listed, or the check failed. |
| 409 | `conflict` | `adminError.providerAccountChanged` | The decision account changed during the check. Read it again and retry. |
| 404 | `not_found` | `adminError.providerAccountNotFound` | No account has this ID. |

### Remove an account {#remove-provider}

`DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id}` takes no body. It removes the account and clears every setting that points at it:

- The Voice Agent's provider and model.
- AI Task's provider and model, with its entity and the Phoenix Assist pipeline.
- For the decision account in use: `decision_provider_id`, `decision_shadow_enabled`, all three tool review modes, and any `hybrid` routing mode, which turns `off`.

If you remove the default decision account, another decision account becomes the default. The response is `200` with `{"deleted": "<instance_id>"}`, even for an ID that doesn't exist.

Related: [Settings and System Endpoints](guide:topic.adminApiSettings#settings)

### List models {#list-models}

`GET /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models` asks the provider for its models once. The response is `{"models": [...]}`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured` | No account has this ID. |
| 400 | `invalid_request` | `adminError.providerConnectionFailedNoDetail` | A decision provider can't be reached. |

### Refresh models and capabilities {#refresh-models}

`POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh` takes no body. It rereads the model list and the declared capabilities, and stores them. It sends no completion requests, so it costs no tokens. For `ollama`, it sends one request per model.

The response has `models`, `capabilities`, `declared` (whether the provider reports capabilities at all) and `checked_at`.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured`, `adminError.providerAccountNotFound` | No account has this ID. |
| 400 | `invalid_request` | `adminError.providerConnectionFailedNoDetail` | A decision provider can't be reached. |

### Check the model's options {#probe-model}

`POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe` takes no body. It asks the provider which options the selected model accepts. This spends credit.

Phoenix sends up to 10 one-token completions, plus one image check when image support is unknown. The result goes over the declared capabilities.

The response has `model`, `probed`, `calls`, `checked_at`, `effort_checkable`, `answered` and `vision_checked`. `answered` is `false` when the provider declined every question.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 404 | `not_found` | `adminError.providerNotConfigured`, `adminError.providerAccountNotFound` | No account has this ID. |
| 400 | `invalid_request` | `adminError.providerChooseModel` | The account has no model selected. |

### Check a setup {#check-setup}

`POST /api/phoenix-mcp/admin/agentcli/probe` takes the [setup fields](guide:#setup-fields), checks them and lists the models. It stores nothing.

A setup or connection failure still answers `200`, with the failure in the body:

```json
{ "ok": false, "error": "English description.", "message_key": "adminError.providerConnectionFailed", "message_params": {}, "models": [] }
```

On success the body is `{"ok": true, "models": [...]}`. A failure carries `message_passthrough: true` instead of `message_key` when the text came from the provider. `message_params` appears only when the message has values.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.providerUnknown` | `kind` isn't a known kind. |
| 409 | `already_exists` | `adminError.providerAlreadyConfigured` | An account with this setup already exists. |

## Running a chat turn {#chat}

```
POST   /api/phoenix-mcp/agentcli/chat
```

Runs one Agent Chat turn and streams it back as Server-Sent Events. Note the path: it sits outside `/admin`, but still needs an administrator.

The route follows the kill switch. While the kill switch is on, it answers `503`. If the kill switch was on when Phoenix started, Home Assistant answers `404` until you turn the kill switch off.

Related: [Kill switch](guide:topic.killSwitch)

### Request body {#chat-body}

```json
{
  "access_key_id": "...",
  "instance_id": "...",
  "user": "turn off the kitchen lights",
  "messages": [{"role": "user", "content": "..."}],
  "options": {"effort": "high"},
  "language": "en"
}
```

| Field | Type | Notes |
|---|---|---|
| `access_key_id` | string | Required. The access key the agent works under. It must be active. |
| `instance_id` | string | Required. The provider account to use. |
| `model` | string | Optional. Overrides the account's model for this turn. |
| `user` | string | The new message. Required unless you send `attachments` or `continue: true`. |
| `attachments` | list | Images for this message. See [Images](guide:#chat-images). |
| `messages` | list | The earlier turns. Phoenix doesn't keep the transcript: store the list from the `messages` event and send it back with the next turn. |
| `options` | object | Model options. See [Options](guide:#chat-options). |
| `continue` | boolean | `true` resumes a turn paused at the round limit, without adding a message. |
| `language` | string | The language for command routing. The panel always sends it. |
| `log_conversation_id` | string | Groups turns into one thread in the Conversation Log. |

The round limit is the `agentcli_max_iterations` setting. Reply style, detail level and Home-focused mode come from the Agent Chat settings when the request starts.

Related: [Agent Chat settings](guide:window.settingsChat), [How a command is routed](guide:topic.commandRouting)

### Options {#chat-options}

Phoenix uses each option only where the provider supports it.

| Field | Type | Notes |
|---|---|---|
| `thinking` | boolean | Turns the model's reasoning on. Default `true`. |
| `effort` | string | `low`, `medium`, `high`, `xhigh` or `max`. Default `high`. |
| `show_thinking` | boolean | Whether to show the model's reasoning. |
| `temperature` | number | The provider's sampling temperature. |
| `max_tokens` | integer | The most tokens the model may write. Default 32000. |

### Images {#chat-images}

Each attachment is `{"mime_type": "image/jpeg", "data": "<base64>"}`.

- Send at most 4 images, each JPEG, PNG or GIF.
- Each image is at most 2 MiB decoded and 8000 pixels on a side, and all of them together at most 4 MiB.
- The image's own header must match its `mime_type`.
- Images inside `messages` share the same 4 MiB budget. Going over it answers `413`.

This route accepts a body of up to 6,706,518 bytes. Everything except the images must still fit in 1 MiB.

### Errors before the stream {#chat-errors}

Phoenix checks the body size, setup, kill switch, access key, account, model, images and message, in that order. The first failure answers with an HTTP status. The [body errors](guide:topic.adminApi#bodies) apply too.

| Status | `error` | `message_key` | When |
|---|---|---|---|
| 413 | `request_too_large` | `adminError.bodyTooLarge` | The body, or the images, are over their limits. |
| 404 | `not_found` | `adminError.setupMissing` | Phoenix isn't set up. |
| 503 | `service_unavailable` | `adminError.serviceDisabled` | The kill switch is on. |
| 503 | `service_unavailable` | `adminError.setupIncomplete` | Phoenix is still starting. |
| 503 | `service_unavailable` | `adminError.serviceStopping` | Home Assistant is shutting down. |
| 404 | `not_found` | `adminError.accessKeyNotFound` | No access key has this ID. |
| 400 | `invalid_request` | `adminError.accessKeyInactive` | The access key isn't active. |
| 404 | `not_found` | `adminError.providerNotConfigured` | No provider account has this ID. |
| 400 | `invalid_request` | `adminError.providerModelMissing` | No model is selected for the account. |
| 400 | `invalid_request` | `adminError.attachmentsInvalid`, `adminError.attachmentsTooMany`, `adminError.attachmentTooLarge`, `adminError.attachmentUnsupported`, `adminError.attachmentDimensions`, `adminError.attachmentsTooLarge` | An image can't be read, or breaks one of the [image limits](guide:#chat-images). |
| 400 | `invalid_request` | `adminError.attachmentsContinue` | Images were sent with `continue: true`. They go only with a new message. |
| 400 | `invalid_request` | `adminError.attachmentsCompanion` | Images were sent with a Home Companion key, which takes text only. |
| 400 | `invalid_request` | `adminError.attachmentsVisionUnsupported` | The model doesn't accept images. |
| 400 | `invalid_request` | `adminError.invalidHostHeader` | The request's `Host` header doesn't form a URL. |
| 400 | `invalid_request` | `adminError.nothingToContinue` | `continue: true` was sent, but there's no conversation to continue. |
| 400 | `invalid_request` | `adminError.emptyMessage` | The message is empty, with no images. |

## Event stream {#events}

A turn that starts answers `200` with `text/event-stream` and the `X-Phoenix-Request-ID` header. Each frame is an event name and a JSON object:

```
event: assistant_delta
data: {"text": "Turning off the kitchen lights."}
```

Phoenix sends a `: keepalive` comment every 15 seconds. Once the stream starts, problems arrive as `error` events, never as an HTTP status. Fields marked optional appear only when they have a value.

| Event | Data fields | Notes |
|---|---|---|
| `ready` | `provider` (the kind), `model`, `access_key_id` | Always the first event. |
| `status` | `provider` (the label), `step`, `tools`, `phase`, `seconds`; during a retry also `attempt`, `detail`, `detail_code` | `phase` is `waiting`, `thinking` or `retrying`. Sent when a model call starts, on each change, and every 30 seconds while nothing else has arrived. |
| `thinking_delta` | `text` | Reasoning text, only when the provider streams it. |
| `assistant_delta` | `text` | Reply text. |
| `focus_declined` | none | The model declined a request outside the home in Home-focused mode. Phoenix removes the model's marker from the reply. |
| `tool_call` | `id`, `name`, `arguments` | Sent before the tool runs. |
| `tool_progress` | `id`, `message`; optional `message_key`, `message_params` | Live status of a long tool, at most every 2 seconds. |
| `approval_required` | `approval_id`, `tool_name`, `review_url`, `expires_at`, `diff` | The turn waits up to 900 seconds for a decision. |
| `approval_resolved` | `approval_id`, `status`, `reason`; optional `reason_kind` | `reason_kind` is `execution` when the approved action failed. |
| `tool_image` | `id`, `name`, `index`, `mime_type`, `data` | A base64 image from a tool result. |
| `tool_result` | `id`, `name`, `is_error`, `summary` | `summary` is shortened for display. |
| `usage` | `input_tokens`, `output_tokens`, `context_tokens`, `cache_read_tokens`, `cache_write_tokens` | The provider's totals for the turn so far, only when it reports usage. |
| `notice` | `code`, `message` | `code` is `bad_tool_call_retry`, `max_tokens_stop` or `empty_turn`. |
| `continue_required` | `iterations` | The turn paused at the round limit. Send the next request with `continue: true`. |
| `error` | `code`, `message`; optional `retryable`, `outcome`, `diagnostic`, `message_params`, `provider_detail`, `provider_code`, `rate_limit`, `refused` | `code` includes `cancelled`, `internal`, `kill_switch`, `unauthorized`, `review_changed`, `outcome_unknown`, `output_limit`, `invalid_request` and the provider's own codes. |
| `messages` | `messages` | The full updated transcript. Keep it for the next turn. |
| `done` | `stop_reason` | Always the last event. `stop_reason` is `error` after a failed turn. |

Related: [Approval and Change Endpoints](guide:topic.adminApiApprovals), [{{label:agentchat.title}}](guide:window.agentChat#approvals)
